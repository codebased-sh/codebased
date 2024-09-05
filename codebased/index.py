from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import sqlite3
import sys
import threading
import typing as T
from collections import namedtuple
from functools import cached_property, wraps
from pathlib import Path
from typing import Generic, TypeVar, Dict

import faiss
import gitignore_parser
import numpy as np
import tiktoken
from tqdm import tqdm

from codebased.settings import EmbeddingsConfig

if T.TYPE_CHECKING:
    from openai import OpenAI

from codebased.settings import Settings
from codebased.embeddings import create_openai_embeddings_sync_batched
from codebased.models import EmbeddingRequest, Embedding, Object
from codebased.parser import parse_objects, render_object
from codebased.stats import STATS
from codebased.storage import DatabaseMigrations, deserialize_embedding_data, serialize_embedding_data


def find_root_git_repository(path: Path):
    # copy to mutate
    search_current_dir = Path(path).resolve()
    done = False
    while not done:
        if (search_current_dir / '.git').is_dir():
            return search_current_dir
        search_current_dir = search_current_dir.parent.resolve()
        done = search_current_dir == Path('/')
    return None


def exit_with_error(message: str, *, exit_code: int = 1) -> T.NoReturn:
    print(message, file=sys.stderr)
    sys.exit(exit_code)


def get_db(database_file: Path) -> sqlite3.Connection:
    db = sqlite3.connect(database_file)
    db.row_factory = sqlite3.Row
    return db


class Events:
    FlushEmbeddings = namedtuple('FlushEmbeddings', [])
    StoreEmbeddings = namedtuple('StoreEmbeddings', ['embeddings'])
    Commit = namedtuple('Commit', [])
    FaissInserts = namedtuple('FaissInserts', ['embeddings'])
    IndexObjects = namedtuple('IndexObjects', ['file_bytes', 'objects'])
    ScheduleEmbeddingRequests = namedtuple('EmbeddingRequests', ['requests'])
    FaissDeletes = namedtuple('FaissDeletes', ['ids'])
    IndexFile = namedtuple('IndexFile', ['path', 'content'])  # tuple[Path, bytes]
    DeleteFile = namedtuple('DeleteFile', ['path'])
    DeleteFileObjects = namedtuple('DeleteFile', ['path'])
    Directory = namedtuple('Directory', ['path'])
    File = namedtuple('File', ['path'])
    DeleteNotVisited = namedtuple('DeleteNotVisited', ['paths'])
    ReloadFileEmbeddings = namedtuple('ReloadFileEmbeddings', ['path'])


def is_binary(file_bytes: bytes) -> bool:
    return b'\x00' in file_bytes


def is_utf8(file_bytes: bytes) -> bool:
    try:
        file_bytes.decode('utf-8')
        return True
    except UnicodeDecodeError:
        return False


def is_utf16(file_bytes: bytes) -> bool:
    # Check for UTF-16 BOM (Byte Order Mark)
    if file_bytes.startswith(b'\xff\xfe') or file_bytes.startswith(b'\xfe\xff'):
        return True

    # If no BOM, check if the file decodes as UTF-16
    try:
        file_bytes.decode('utf-16')
        return True
    except UnicodeDecodeError:
        return False


# Put this on Dependencies object.
class OpenAIRequestScheduler:
    def __init__(self, oai_client: "OpenAI", embedding_config: EmbeddingsConfig):
        self.oai_client = oai_client
        self.embedding_config = embedding_config
        self.batch = []
        self.batch_tokens = 0
        self.batch_size_limit = 2048
        self.batch_token_limit = 400_000

    @cached_property
    def encoding(self) -> tiktoken.Encoding:
        return tiktoken.encoding_for_model(self.embedding_config.model)

    def schedule(self, req: EmbeddingRequest) -> T.Iterable[Embedding]:
        request_tokens = len(self.encoding.encode(req.content, disallowed_special=()))
        results = []
        if request_tokens > 8192:
            return results
        if len(self.batch) >= self.batch_size_limit or self.batch_tokens + request_tokens > self.batch_token_limit:
            results = self.flush()
            self.batch.clear()
            self.batch_tokens = 0
        self.batch.append(req)
        self.batch_tokens += request_tokens
        return results

    def flush(self) -> T.Iterable[Embedding]:
        if not self.batch:
            return []
        results = create_openai_embeddings_sync_batched(
            self.oai_client,
            self.batch,
            self.embedding_config
        )
        self.batch = []
        self.batch_tokens = 0
        return results


@dataclasses.dataclass
class Config:
    flags: Flags

    @cached_property
    def root(self) -> Path:
        git_repository_dir = find_root_git_repository(self.flags.directory)
        if git_repository_dir is None:
            exit_with_error('Codebased must be run within a Git repository.')
        print(f'Found Git repository {git_repository_dir}')
        git_repository_dir: Path = git_repository_dir
        return git_repository_dir

    @property
    def git_directory(self) -> Path:
        return self.root / '.git'

    @property
    def codebased_directory(self) -> Path:
        directory = self.root / '.codebased'
        directory.mkdir(exist_ok=True)
        return directory

    @property
    def index_path(self) -> Path:
        return self.codebased_directory / 'index.faiss'

    @cached_property
    def rebuild_faiss_index(self) -> bool:
        return self.flags.rebuild_faiss_index or not self.index_path.exists()


K = TypeVar('K')
V = TypeVar('V')


@dataclasses.dataclass
class ThreadSafeCache(Generic[K, V]):
    _lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)
    _cache: Dict[K, V] = dataclasses.field(default_factory=dict)

    def __getitem__(self, key: K) -> V:
        with self._lock:
            return self._cache[key]

    def __setitem__(self, key: K, value: V) -> None:
        with self._lock:
            self._cache[key] = value

    def __delitem__(self, key: K) -> None:
        with self._lock:
            del self._cache[key]

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def get(self, key: K, default: V = None) -> V:
        with self._lock:
            return self._cache.get(key, default)


class thread_local_cached_property:
    def __init__(self, func):
        self.func = func
        self.local = threading.local()

    def __get__(self, obj, cls=None):
        if obj is None:
            return self

        if not hasattr(self.local, 'instance'):
            self.local.instance = self.func(obj)
        return self.local.instance

    def __set_name__(self, owner, name):
        self.name = name

    def clear_cache(self, obj):
        if hasattr(self.local, 'instance'):
            delattr(self.local, 'instance')


def clear_thread_local_cache(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        result = func(self, *args, **kwargs)
        for name, attr in type(self).__dict__.items():
            if isinstance(attr, thread_local_cached_property):
                attr.clear_cache(self)
        return result

    return wrapper


@dataclasses.dataclass
class Dependencies:
    # config must be passed in explicitly.
    config: Config
    settings: Settings
    search_cache: ThreadSafeCache[str, list] = dataclasses.field(default_factory=ThreadSafeCache)

    @cached_property
    def openai_client(self) -> "OpenAI":
        from openai import OpenAI

        return OpenAI(api_key=self.settings.OPENAI_API_KEY)

    @cached_property
    def index(self) -> faiss.Index:
        if self.config.rebuild_faiss_index:
            index = faiss.IndexIDMap2(faiss.IndexFlatL2(self.settings.embeddings.dimensions))
            if not self.config.index_path.exists():
                faiss.write_index(index, str(self.config.index_path))
        else:
            index = faiss.read_index(str(self.config.index_path))
        return index

    @thread_local_cached_property
    def db(self) -> sqlite3.Connection:
        db = get_db(self.config.codebased_directory / 'codebased.db')
        migrations = DatabaseMigrations(db, Path(__file__).parent / 'migrations')
        migrations.initialize()
        migrations.migrate()
        return db

    @cached_property
    def gitignore(self) -> T.Callable[[Path], bool]:
        gitignore_path = self.config.root / '.gitignore'
        try:
            return gitignore_parser.parse_gitignore(gitignore_path, base_dir=self.config.root)
        except FileNotFoundError:
            return lambda _: False

    @cached_property
    def request_scheduler(self) -> OpenAIRequestScheduler:
        return OpenAIRequestScheduler(self.openai_client, self.settings.embeddings)


class FileExceptions:
    class AlreadyIndexed(Exception):
        """
        File has already been indexed.
        """
        pass

    class Ignore(Exception):
        """
        File cannot be indexed because it's binary or not UTF-8 / UTF-16.
        """
        pass

    class Delete(Exception):
        """
        File should be deleted.
        """
        pass


def index_paths(
        dependencies: Dependencies,
        config: Config,
        paths_to_index: list[Path],
        *,
        total: bool = True
):
    ignore = dependencies.gitignore
    db = dependencies.db
    index = dependencies.index

    rebuilding_faiss_index = config.rebuild_faiss_index
    if not total:
        rebuilding_faiss_index = False

    dependencies.db.execute("begin;")
    # We can actually be sure we visit each file at most once.
    # Also, we don't need O(1) contains checks.
    # So use a list instead of a set.
    # May be useful to see what the traversal order was too.
    embeddings_to_index: list[Embedding] = []
    deletion_markers = []
    paths_visited = []
    events = [
        Events.Commit(),
        # Add to FAISS after deletes, because SQLite can reuse row ids.
        Events.FaissInserts(embeddings_to_index),
        Events.FaissDeletes(deletion_markers),
        Events.FlushEmbeddings(),
        *[Events.Directory(x) if x.is_dir() else Events.File(x) for x in paths_to_index]
    ]
    if total:
        events.insert(3, Events.DeleteNotVisited(paths_visited))

    # Why do we need to put space before file?
    pbar = tqdm(total=None, desc=f"Indexing {config.root.name}", unit=" file")

    try:
        while events:
            event = events.pop()
            STATS.increment(f"codebased.index.events.{type(event).__name__}.total")
            if isinstance(event, Events.Directory):
                path = event.path
                if path == config.root / '.git' or path == config.root / '.codebased':
                    continue
                for entry in os.scandir(path):
                    entry_path = Path(entry.path)
                    if ignore(entry_path):  # noqa
                        continue
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir():
                            events.append(Events.Directory(entry_path))
                        elif entry.is_file():
                            events.append(Events.File(entry_path))
                    except PermissionError:
                        continue
            elif isinstance(event, Events.File):
                path = event.path
                assert isinstance(path, Path)
                relative_path = path.relative_to(config.root)
                pbar.update(1)
                try:
                    if not (path.exists() and path.is_file()):
                        raise FileExceptions.Delete()
                    # TODO: This is hilariously slow.
                    paths_visited.append(relative_path)

                    result = db.execute(
                        """
                            select
                                size_bytes, 
                                last_modified_ns, 
                                sha256_digest 
                            from file 
                            where path = :path;
                        """,
                        {'path': str(relative_path)}
                    ).fetchone()

                    stat = path.stat()
                    if result is not None:
                        size, last_modified, previous_sha256_digest = result
                        if stat.st_size == size and stat.st_mtime == last_modified:
                            raise FileExceptions.AlreadyIndexed()
                    else:
                        previous_sha256_digest = None

                    try:
                        file_bytes = path.read_bytes()
                    except FileNotFoundError:
                        raise FileExceptions.Delete()
                    # Ignore binary files.
                    if is_binary(file_bytes):
                        raise FileExceptions.Ignore()
                    # TODO: See how long this takes on large repos.
                    # TODO: We might want to memoize the "skip" results if this is an issue.
                    if not (is_utf8(file_bytes) or is_utf16(file_bytes)):
                        raise FileExceptions.Ignore()
                    real_sha256_digest = hashlib.sha256(file_bytes).digest()
                    # TODO: To support incremental indexing, i.e. allowing this loop to make progress if interrupted
                    #  we would need to wait until the objects, embeddings, FTS index, etc. are computed to insert.
                    db.execute(
                        """
                            insert into file 
                                (path, size_bytes, last_modified_ns, sha256_digest)
                                 values 
                                 (:path, :size_bytes, :last_modified_ns, :sha256_digest)
                                 on conflict (path) do update 
                                 set size_bytes = :size_bytes, 
                                    last_modified_ns = :last_modified_ns, 
                                    sha256_digest = :sha256_digest;
                        """,
                        {
                            'path': str(relative_path),
                            'size_bytes': stat.st_size,
                            'last_modified_ns': stat.st_mtime_ns,
                            'sha256_digest': real_sha256_digest
                        }
                    )
                    # Do this after updating the DB, because a write to SQLite is cheaper than reading a file.
                    # https://www.sqlite.org/fasterthanfs.html
                    if previous_sha256_digest == real_sha256_digest:
                        raise FileExceptions.AlreadyIndexed()
                    # Actually schedule the file for indexing.
                    events.append(Events.IndexFile(relative_path, file_bytes))
                    # Delete old objects before adding new ones.
                    events.append(Events.DeleteFileObjects(relative_path))
                    continue
                except FileExceptions.Delete:
                    events.append(Events.DeleteFile(path))
                    # Need to run this first due to foreign key constraints.
                    events.append(Events.DeleteFileObjects(path))
                    continue
                except FileExceptions.AlreadyIndexed:
                    if rebuilding_faiss_index:
                        events.append(Events.ReloadFileEmbeddings(relative_path))
                    continue
                except FileExceptions.Ignore:
                    continue
            elif isinstance(event, Events.ReloadFileEmbeddings):
                # Could do this in a single query at the end.
                path = event.path
                assert isinstance(path, Path)
                embedding_rows = db.execute(
                    """
                           select 
                               object_id,
                               content_sha256,
                               data 
                           from embedding 
                           where object_id in (
                               select id from object 
                               where path = :path
                           )
                        """,
                    {'path': str(path)}
                ).fetchall()
                embeddings = [
                    Embedding(
                        object_id=x['object_id'],
                        data=deserialize_embedding_data(x['data']),
                        content_hash=x['content_sha256']
                    )
                    for x in embedding_rows
                ]
                embeddings_to_index.extend(embeddings)
            elif isinstance(event, Events.DeleteFile):
                relative_path = event.path
                assert isinstance(relative_path, Path)
                db.execute(
                    """
                        delete from file
                        where path = :path 
                    """,
                    {'path': str(relative_path)}
                )
            elif isinstance(event, Events.DeleteFileObjects):
                relative_path = event.path
                id_tuples = db.execute(
                    """
                        delete from object 
                        where path = :path 
                        returning id;
                    """,
                    {'path': str(relative_path)}
                ).fetchall()
                deleted_ids = [x[0] for x in id_tuples]
                if deleted_ids:
                    in_clause = ', '.join(['?'] * len(deleted_ids))
                    db.execute(
                        f"""
                                delete from fts where rowid in ( {in_clause} );
                        """,
                        deleted_ids
                    )
                    # These are relatively expensive to compute, and accessible by their hash, so keep them around.
                    # db.execute(
                    #     f"""
                    #         delete from embedding
                    #         where object_id = ( {in_clause} );
                    #     """,
                    #     deleted_ids
                    # )
                deletion_markers.extend(deleted_ids)
            elif isinstance(event, Events.IndexFile):
                relative_path, file_bytes = event.path, event.content
                assert isinstance(relative_path, Path)
                assert isinstance(file_bytes, bytes)

                objects = parse_objects(relative_path, file_bytes)
                objects_by_id: dict[int, Object] = {}
                for obj in objects:
                    object_id, = db.execute(
                        """
                           insert into object
                           (path, name, language, context_before, context_after, kind, byte_range, coordinates)
                           values
                           (:path, :name, :language, :context_before, :context_after, :kind, :byte_range, :coordinates)
                           returning id;
                        """,
                        {
                            'path': str(obj.path),
                            'name': obj.name,
                            'language': obj.language,
                            'context_before': json.dumps(obj.context_before),
                            'context_after': json.dumps(obj.context_after),
                            'kind': obj.kind,
                            'byte_range': json.dumps(obj.byte_range),
                            'coordinates': json.dumps(obj.coordinates)
                        }

                    ).fetchone()
                    objects_by_id[object_id] = obj
                events.append(Events.IndexObjects(file_bytes, objects_by_id))
            elif isinstance(event, Events.IndexObjects):
                file_bytes = event.file_bytes
                objects_by_id = event.objects
                # dict[int, Object]
                assert isinstance(objects_by_id, dict)
                # vector stuff!
                lines = file_bytes.split(b'\n')
                requests_to_schedule = []
                for obj_id, obj in objects_by_id.items():
                    rendered = render_object(obj, in_lines=lines)
                    request = EmbeddingRequest(
                        object_id=obj_id,
                        content=rendered,
                        content_hash=hashlib.sha256(rendered.encode('utf-8')).hexdigest(),
                    )
                    requests_to_schedule.append(request)
                events.append(Events.ScheduleEmbeddingRequests(requests=requests_to_schedule))
                db.executemany(
                    """
                        insert into fts
                        (rowid, path, name, content)
                        values
                        (:object_id, :path, :name, :content);
                    """,
                    [
                        {
                            'object_id': obj_id,
                            'path': str(obj.path),
                            'name': obj.name,
                            'content': file_bytes[obj.byte_range[0]:obj.byte_range[1]]
                        }
                        for obj_id, obj in objects_by_id.items()
                    ]
                )
            elif isinstance(event, Events.ScheduleEmbeddingRequests):
                requests_to_schedule = event.requests
                embeddings_batch = []
                for request in requests_to_schedule:
                    existing_embedding = db.execute(
                        """
                            select data from embedding 
                            where content_sha256 = :content_sha256;
                        """,
                        {'content_sha256': request.content_hash}
                    ).fetchone()
                    if existing_embedding is not None:
                        embedding = Embedding(
                            object_id=request.object_id,
                            data=deserialize_embedding_data(existing_embedding['data']),
                            content_hash=request.content_hash
                        )
                        embeddings_batch.append(embedding)
                    else:
                        embeddings = dependencies.request_scheduler.schedule(request)
                        embeddings_batch.extend(embeddings)
                events.append(Events.StoreEmbeddings(embeddings=embeddings_batch))
            elif isinstance(event, Events.FlushEmbeddings):
                if 'request_scheduler' in dependencies.__dict__:
                    results = dependencies.request_scheduler.flush()
                    events.append(Events.StoreEmbeddings(embeddings=results))
            elif isinstance(event, Events.StoreEmbeddings):
                embeddings_batch = event.embeddings
                if not embeddings_batch:
                    continue
                db.executemany(
                    """
                            insert into embedding
                            (object_id, data, content_sha256)
                            values
                            (:object_id, :data, :content_sha256)
                            on conflict (object_id) do update 
                            set data = :data, 
                                content_sha256 = :content_sha256;
                        """,
                    [
                        {
                            'object_id': e1.object_id,
                            'data': serialize_embedding_data(e1.data),
                            'content_sha256': e1.content_hash
                        }
                        for e1 in embeddings_batch
                    ]
                )
                embeddings_to_index.extend(embeddings_batch)
            elif isinstance(event, Events.FaissInserts):
                if embeddings_to_index:
                    index.add_with_ids(
                        np.array([e.data for e in event.embeddings]),
                        [e.object_id for e in event.embeddings]
                    )
                event.embeddings.clear()
            elif isinstance(event, Events.FaissDeletes):
                delete_ids = event.ids
                if delete_ids:
                    index.remove_ids(np.array(delete_ids))
                delete_ids.clear()
            elif isinstance(event, Events.Commit):
                dependencies.search_cache.clear()
                db.commit()
                faiss.write_index(index, str(config.index_path))
            elif isinstance(event, Events.DeleteNotVisited):
                inverse_paths = [str(path) for path in event.paths]
                in_clause = ', '.join(['?'] * len(inverse_paths))
                id_tuples = dependencies.db.execute(
                    f"""
                        delete from object
                        where path not in ({in_clause})
                        returning id;
                    """,
                    inverse_paths
                ).fetchall()
                dependencies.db.execute(
                    f"""
                        delete from file
                        where path not in ( {in_clause} );
                    """,
                    inverse_paths
                )
                deleted_ids = [x[0] for x in id_tuples]
                in_clause = ', '.join(['?'] * len(deleted_ids))
                dependencies.db.execute(
                    f"""
                        delete from fts where rowid in ( {in_clause} );
                    """,
                    deleted_ids
                )
                deletion_markers.extend(deleted_ids)
            else:
                raise NotImplementedError(event)
        else:
            pass
    except:
        db.rollback()
        raise


@dataclasses.dataclass
class Flags:
    directory: Path
    background: bool
    # TODO: These conflict and suck.
    rebuild_faiss_index: bool
    cached_only: bool
    stats: bool
    semantic: bool
    full_text_search: bool
    top_k: int
    query: str
