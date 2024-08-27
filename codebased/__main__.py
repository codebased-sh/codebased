from __future__ import annotations

import argparse
import hashlib
import os
import typing as T
from datetime import datetime
from pathlib import Path

import faiss
import numpy as np

from codebased.constants import EMBEDDING_MODEL_CONTEXT_LENGTH
from codebased.core import Context, greet, Settings, PACKAGE_DIR
from codebased.embeddings import create_openai_embeddings_sync_batched
from codebased.exceptions import NotFoundException, AlreadyExistsException
from codebased.filesystem import find_git_repositories, get_git_files, get_file_bytes
from codebased.models import PersistentRepository, Repository, ObjectHandle, FileRevision, FileRevisionHandle, \
    Embedding, PersistentFileRevision, EmbeddingRequest
from codebased.parser import parse_objects, render_object
from codebased.storage import persist_file_revision, persist_object, fetch_objects, fetch_embedding, DatabaseMigrations, \
    persist_repository, fetch_embedding_for_hash


def cli():
    parser = argparse.ArgumentParser(description="Codebased")
    parser.add_argument(
        "--root",
        type=Path,
        help="The directory to index.",
        default=os.getcwd(),
        required=False,
    )
    args = parser.parse_args()
    main(args.root)


class Main:
    def __init__(self, context: Context):
        self.context = context

    def gather_repositories(self, root: Path) -> T.Iterable[PersistentRepository]:
        for repo in find_git_repositories(root):
            repo_object = Repository(path=repo, type='git')
            db = self.context.db
            persistent_repository = persist_repository(db, repo_object)
            db.commit()
            yield persistent_repository

    def gather_objects(self, root: Path) -> T.Iterable[ObjectHandle]:
        for repo in self.gather_repositories(root):
            for path in get_git_files(repo.path):
                file_revision_abs_path = repo.path / path
                content = get_file_bytes(file_revision_abs_path)
                content_hash = hashlib.sha1(content).hexdigest()
                size = path.stat().st_size
                last_modified = datetime.fromtimestamp(path.stat().st_mtime)
                file_revision = FileRevision(
                    repository_id=repo.id,
                    path=path,
                    hash=content_hash,
                    size=size,
                    last_modified=last_modified
                )
                try:
                    self.context.db.execute("begin;")
                    persistent_file_revision = persist_file_revision(self.context.db, file_revision)
                    file_revision_handle = FileRevisionHandle(repo, persistent_file_revision)
                    objects = parse_objects(persistent_file_revision)
                    tmp = []
                    for obj in objects:
                        persistent_object = persist_object(self.context.db, obj)
                        object_handle = ObjectHandle(file_revision_handle, persistent_object)
                        tmp.append(object_handle)
                    self.context.db.execute("commit;")
                    yield from tmp
                except AlreadyExistsException as e:
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
                    for obj in fetch_objects(self.context.db, file_revision):
                        yield ObjectHandle(
                            file_revision=file_revision_handle,
                            object=obj
                        )
                    self.context.db.execute("rollback;")

    def gather_embeddings(self, root_path: Path) -> T.Iterable[Embedding]:
        q: T.List[EmbeddingRequest] = []

        def enqueue(req: EmbeddingRequest):
            q.append(req)

        def should_drain() -> bool:
            return len(q) > 100

        context = self.context
        client = context.openai_client
        context1 = self.context
        encoding = context1.embedding_model_encoding

        def drain() -> T.Iterable[Embedding]:
            nonlocal q, client
            yield from create_openai_embeddings_sync_batched(client, q, self.context.config.embeddings)
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
                    yield fetch_embedding_for_hash(self.context.db, content_hash)
                except NotFoundException:
                    token_count = len(encoding.encode(text))
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
        big_vec = np.array([e.data for e in all_embeddings])
        assert big_vec.shape == (len(all_embeddings), self.context.config.embeddings.dimensions)
        ids = [e.object_id for e in all_embeddings]
        index_id_mapping.add_with_ids(big_vec, ids)
        return index_id_mapping


def main(root: Path):
    greet()
    settings = Settings.default()
    settings.ensure_ok()
    context = Context.from_settings(settings)
    migrations = DatabaseMigrations(context.db, PACKAGE_DIR / "migrations")
    migrations.initialize()
    migrations.migrate()
    m = Main(context)
    faiss_index = m.create_index(root)
    while True:
        query = input("What do you want to search for? ")
        ...


if __name__ == '__main__':
    cli()
