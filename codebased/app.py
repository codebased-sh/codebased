from __future__ import annotations

import hashlib
import logging
import sqlite3
import sys
import typing as T
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import faiss
import numpy as np
import tqdm

from codebased.constants import EMBEDDING_MODEL_CONTEXT_LENGTH
from codebased.core import Context, Settings, PACKAGE_DIR
from codebased.embeddings import create_openai_embeddings_sync_batched, create_ephemeral_embedding
from codebased.exceptions import AlreadyExistsException, NotFoundException
from codebased.filesystem import find_git_repositories, get_git_files, get_file_bytes
from codebased.models import PersistentRepository, Repository, ObjectHandle, FileRevision, FileRevisionHandle, \
    PersistentFileRevision, Embedding, EmbeddingRequest, SearchResult
from codebased.parser import parse_objects, render_object
from codebased.stats import STATS
from codebased.storage import persist_repository, persist_file_revision, persist_object, fetch_objects, \
    persist_embedding, fetch_embedding, fetch_embedding_for_hash, fetch_object_handle, DatabaseMigrations

commits, rollbacks, begins = 0, 0, 0

logger = logging.getLogger(__name__)


def rollback(db: sqlite3.Connection):
    global rollbacks
    rollbacks += 1
    db.execute("rollback;")


def commit(db: sqlite3.Connection):
    global commits
    commits += 1
    db.execute("commit;")


def begin(db: sqlite3.Connection):
    global begins
    begins += 1
    db.execute("begin;")


class App:
    def __init__(self, context: Context):
        self.context = context
        self.setup_logging()

    def setup_logging(self):
        log_file = self.context.application_directory / "codebased.log"
        logging.basicConfig(
            filename=str(log_file),
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

    def gather_repositories(self, root: Path) -> T.Iterable[PersistentRepository]:
        repositories = find_git_repositories(root)
        for repo in repositories:
            repo_object = Repository(path=repo, type='git')
            db = self.context.db
            begin(db)
            persistent_repository = persist_repository(db, repo_object)
            commit(db)
            assert persistent_repository.id != 0
            yield persistent_repository

    def gather_objects(self, root: Path) -> T.Iterable[ObjectHandle]:
        for repo in tqdm.tqdm(list(self.gather_repositories(root)), file=sys.stderr):
            logger.debug(f"Indexing {repo.path} with id {repo.id}")
            for path in tqdm.tqdm(
                    get_git_files(repo.path),
                    leave=False,
                    desc=f"Indexing {repo.path.name}",
                    file=sys.stderr
            ):
                file_revision_abs_path = repo.path / path
                content = get_file_bytes(file_revision_abs_path)
                content_hash = hashlib.sha1(content).hexdigest()
                stat_result = file_revision_abs_path.stat()
                size = stat_result.st_size
                last_modified = datetime.fromtimestamp(stat_result.st_mtime)
                file_revision = FileRevision(
                    repository_id=repo.id,
                    path=path,
                    hash=content_hash,
                    size=size,
                    last_modified=last_modified
                )
                try:
                    begin(self.context.db)
                    persistent_file_revision = persist_file_revision(self.context.db, file_revision)
                    file_revision_handle = FileRevisionHandle(repo, persistent_file_revision)
                    logger.debug(
                        f"Indexing new file revision for {file_revision_abs_path} w/ id {persistent_file_revision.id}"
                    )
                    objects = parse_objects(persistent_file_revision)
                    tmp = []
                    for obj in objects:
                        persistent_object = persist_object(self.context.db, obj)
                        object_handle = ObjectHandle(file_revision_handle, persistent_object)
                        logger.debug(f"Indexing new object {obj.name} w/ id {persistent_object.id}")
                        tmp.append(object_handle)
                    commit(self.context.db)
                    yield from tmp
                except AlreadyExistsException as e:
                    rollback(self.context.db)
                    persistent_file_revision = PersistentFileRevision(
                        id=e.identifier,
                        repository_id=repo.id,
                        path=path,
                        hash=content_hash,
                        size=size,
                        last_modified=last_modified
                    )
                    file_revision_handle = FileRevisionHandle(
                        repository=repo,
                        file_revision=persistent_file_revision
                    )
                    logger.debug(f"Fetching objects for {file_revision_abs_path} w/ id {persistent_file_revision.id}")
                    for obj in fetch_objects(self.context.db, file_revision):
                        logger.debug(f"Fetched object {obj.name} w/ id {obj.id}")
                        yield ObjectHandle(
                            file_revision=file_revision_handle,
                            object=obj
                        )

    def gather_embeddings(self, root_path: Path) -> T.Iterable[Embedding]:
        q: T.List[EmbeddingRequest] = []

        def enqueue(req: EmbeddingRequest):
            q.append(req)

        def should_drain() -> bool:
            return len(q) > 100

        context = self.context
        client = context.openai_client
        encoding = self.context.embedding_model_encoding

        def drain() -> T.Iterable[Embedding]:
            nonlocal q, client
            if not q:
                return
            embeddings = create_openai_embeddings_sync_batched(client, q, self.context.config.embeddings)
            begin(context.db)
            try:
                for e in embeddings:
                    pe = persist_embedding(self.context.db, e)
                    yield pe
                commit(context.db)
            except Exception:
                rollback(context.db)
                raise
            q.clear()

        for obj in self.gather_objects(root_path):
            # Don't create an embedding if one already exists.
            # It might be nice to store all the embeddings for a file atomically.
            try:
                embedding = fetch_embedding(self.context.db, obj.object.id)
                yield embedding
            except NotFoundException:
                text = render_object(obj)
                content_hash = hashlib.sha1(text.encode('utf-8')).hexdigest()
                try:
                    cached = fetch_embedding_for_hash(self.context.db, content_hash)
                    embedding_for_object = Embedding(
                        object_id=obj.object.id,
                        data=cached.data,
                        content_hash=cached.content_hash
                    )
                    yield embedding_for_object
                    # yield persist_embedding(self.context.db, embedding_for_object)
                except NotFoundException:
                    token_count = len(encoding.encode(text, disallowed_special=()))
                    request = EmbeddingRequest(
                        object_id=obj.object.id,
                        content=text,
                        content_hash=content_hash,
                        token_count=token_count
                    )
                    if 0 < token_count < EMBEDDING_MODEL_CONTEXT_LENGTH:
                        enqueue(request)
                        if should_drain():
                            yield from drain()
        else:
            yield from drain()

    def create_index(self, root: Path) -> faiss.Index:
        index_l2 = faiss.IndexFlatL2(self.context.config.embeddings.dimensions)
        index_id_mapping = faiss.IndexIDMap2(index_l2)
        all_embeddings = list(self.gather_embeddings(root))
        STATS.increment("codebased.index.objects.total", len(all_embeddings))
        with STATS.timer("codebased.index.create.duration"):
            big_vec = np.array([e.data for e in all_embeddings])
            assert big_vec.shape == (len(all_embeddings), self.context.config.embeddings.dimensions)
            ids = [e.object_id for e in all_embeddings]
            logger.debug(f"Adding {len(ids)} embeddings to index: {ids}")
            index_id_mapping.add_with_ids(big_vec, ids)
        return index_id_mapping

    @lru_cache
    def perform_search(self, query: str, faiss_index: faiss.Index, *, n: int = 10) -> list[SearchResult]:
        if not query:
            return []
        embedding = create_ephemeral_embedding(
            self.context.openai_client,
            query,
            self.context.config.embeddings
        )
        distances_s, ids_s = faiss_index.search(np.array([embedding]), k=n)
        distances, object_ids = distances_s[0], ids_s[0]
        handles = [fetch_object_handle(self.context.db, int(object_id)) for object_id in object_ids]
        results = [SearchResult(object_handle=h, score=s) for h, s in zip(handles, distances)]
        for result in results:
            logger.debug(f"Result: {result}")
        return results


def get_app() -> App:
    settings = Settings.default()
    settings.ensure_ok()
    context = Context.from_settings(settings)
    migrations = DatabaseMigrations(context.db, PACKAGE_DIR / "migrations")
    migrations.initialize()
    migrations.migrate()
    app = App(context)
    return app
