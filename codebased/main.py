import argparse
import dataclasses
import hashlib
import json
import os
import sqlite3
import sys
import typing as T
from collections import namedtuple
from pathlib import Path

import faiss
import gitignore_parser
import numpy as np
import tiktoken
from openai import OpenAI

from codebased.core import Secrets, EmbeddingsConfig
from codebased.embeddings import create_openai_embeddings_sync_batched
from codebased.models import EmbeddingRequest, Embedding, Object
from codebased.parser import parse_objects, render_object
from codebased.storage import DatabaseMigrations, deserialize_embedding_data

VERSION = "0.0.1"


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
    db = sqlite3.connect(database_file, check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


class Events:
    Commit = namedtuple('Commit', [])
    FaissInserts = namedtuple('FaissInserts', ['embeddings'])
    IndexObjects = namedtuple('IndexObjects', ['file_bytes', 'objects'])
    EmbeddingRequests = namedtuple('EmbeddingRequests', ['requests'])
    FaissDeletes = namedtuple('FaissDeletes', ['ids'])
    IndexFile = namedtuple('IndexFile', ['path', 'content'])  # tuple[Path, bytes]
    Directory = namedtuple('DirEnter', ['path'])
    File = namedtuple('File', ['path'])


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


def get_request_scheduler(
        oai_client: OpenAI,
        embedding_config: EmbeddingsConfig
) -> T.Callable[[EmbeddingRequest], T.Iterable[Embedding]]:
    batch, batch_tokens = [], 0
    # TODO: Check token count.
    batch_size_limit = 256
    # Observed through testing.
    batch_token_limit = 400_000
    encoding = tiktoken.encoding_for_model(embedding_config.model)

    def run_requests() -> T.Iterable[Embedding]:
        nonlocal batch
        if not batch:
            return []
        return create_openai_embeddings_sync_batched(
            oai_client,
            batch,
            embedding_config
        )

    def schedule_request(req: EmbeddingRequest) -> T.Iterable[Embedding]:
        nonlocal batch, batch_size_limit, batch_tokens
        batch.append(req)
        request_tokens = encoding.encode(req.content, disallowed_special=())
        batch_tokens += len(request_tokens)
        # This is incorrect.
        if len(batch) >= batch_size_limit or batch_tokens >= batch_token_limit:
            results = run_requests()
            batch.clear()
            return results
        else:
            return []

    schedule_request.finish = run_requests

    return schedule_request


def main():
    # TODO: OpenAI API key / authentication to Codebased API.
    parser = argparse.ArgumentParser(
        description="Codebased CLI tool",
        usage="Codebased [-h | --version] {search} ..."
    )
    parser.add_argument(
        '--version',
        action='version',
        version=f'Codebased {VERSION}'
    )
    subparsers = parser.add_subparsers(
        dest='command',
        required=True
    )

    search_parser = subparsers.add_parser(
        'search',
        help='Search for Git repository',
    )
    # Example: Add an argument to the search command
    search_parser.add_argument(
        '-d', '--directory',
        help='Specify the directory to start the search from',
        default=Path.cwd(),
        type=Path
    )

    args = parser.parse_args()

    if args.command == 'search':
        git_repository_dir = find_root_git_repository(args.directory)
        if git_repository_dir is None:
            exit_with_error('Codebased must be run within a Git repository.')
        git_repository_dir: Path = git_repository_dir
        print(f'Found Git repository {git_repository_dir}')

        codebased_directory = git_repository_dir / '.codebased'
        if not codebased_directory.exists():
            codebased_directory.mkdir()
        db_path = codebased_directory / 'codebased.db'
        index_path = codebased_directory / 'index.faiss'
        # This should create the file if it doesn't exist.
        db = get_db(db_path)
        secrets_ = Secrets.magic()

        oai_client = OpenAI(api_key=secrets_.OPENAI_API_KEY)
        embedding_config = EmbeddingsConfig()

        if index_path.exists():
            index = faiss.read_index(str(index_path))
        else:
            index = faiss.IndexIDMap2(faiss.IndexFlatL2(embedding_config.dimensions))
        migrations = DatabaseMigrations(db, Path(__file__).parent / 'migrations')
        migrations.initialize()
        migrations.migrate()
        root_gitignore_path = git_repository_dir / '.gitignore'
        try:
            ignore = gitignore_parser.parse_gitignore(root_gitignore_path, base_dir=git_repository_dir)
        except FileNotFoundError:
            ignore = lambda _: False
        db.execute("begin;")
        # We can actually be sure we visit each file at most once.
        # Also, we don't need O(1) contains checks.
        # So use a list instead of a set.
        # May be useful to see what the traversal order was too.
        embeddings_to_persist: list[Embedding] = []
        deletion_markers = []
        events = [
            Events.Commit(),
            # Add to FAISS after deletes, because SQLite can reuse row ids.
            Events.FaissInserts(embeddings_to_persist),
            Events.FaissDeletes(deletion_markers),
            Events.Directory(git_repository_dir)
        ]
        paths_visited = []

        embedding_scheduler = get_request_scheduler(oai_client, embedding_config)

        while events:
            event = events.pop()
            if isinstance(event, Events.Directory):
                path = event.path
                for entry in os.scandir(path):
                    entry_path = Path(entry.path)
                    if ignore(entry_path):
                        continue
                    if entry.is_symlink():
                        continue
                    if entry.is_dir():
                        events.append(Events.Directory(entry_path))
                    elif entry.is_file():
                        events.append(Events.File(entry_path))
            elif isinstance(event, Events.File):
                path = event.path
                assert isinstance(path, Path)
                assert path.is_file()
                # TODO: This is hilariously slow.
                relative_path = path.relative_to(git_repository_dir)
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
                    (str(relative_path),)
                ).fetchone()

                stat = path.stat()
                if result is not None:
                    size, last_modified, previous_sha256_digest = result
                    if stat.st_size == size and stat.st_mtime == last_modified:
                        continue
                else:
                    previous_sha256_digest = None

                file_bytes = path.read_bytes()
                # Ignore binary files.
                if is_binary(file_bytes):
                    continue
                # TODO: See how long this takes on large repos.
                if not (is_utf8(file_bytes) or is_utf16(file_bytes)):
                    continue
                # TODO: We might want to memoize the "skip" results if this is an issue.
                real_sha256_digest = hashlib.sha256(file_bytes).digest()
                # TODO: To support incremental indexing, i.e. allowing this loop to make progress if it's interrupted
                #  before finishing, we would need to wait until the objects, embeddings, FTS index, etc. are computed.
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
                    continue
                # Actually schedule the file.
                events.append(Events.IndexFile(relative_path, file_bytes))
                continue
            elif isinstance(event, Events.IndexFile):
                relative_path, file_bytes = event.path, event.content
                assert isinstance(relative_path, Path)
                assert isinstance(file_bytes, bytes)
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
                    db.executemany(
                        """
                                delete from fts where rowid = ?;
                        """,
                        (deleted_ids,)
                    )
                deletion_markers.extend(deleted_ids)
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
                events.append(Events.EmbeddingRequests(requests=requests_to_schedule))
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
            elif isinstance(event, Events.EmbeddingRequests):
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
                        embedding = dataclasses.replace(
                            Embedding(
                                object_id=request.object_id,
                                data=existing_embedding['data'],
                                content_hash=request.content_hash
                            )
                        )
                        embeddings_batch.append(embedding)
                    else:
                        embeddings = embedding_scheduler(request)
                        embeddings_batch.extend(embeddings)
                persist_embeddings(embeddings_batch, db)
                embeddings_to_persist.extend(embeddings_batch)
            elif isinstance(event, Events.FaissInserts):
                embeddings_to_persist = event.embeddings
                if embeddings_to_persist:
                    index.add_with_ids(
                        np.array([e.data for e in embeddings_to_persist]),
                        [e.object_id for e in embeddings_to_persist]
                    )
                embeddings_to_persist.clear()
            elif isinstance(event, Events.FaissDeletes):
                deletion_markers = event.ids
                if deletion_markers:
                    index.remove_ids(np.array(deletion_markers))
                deletion_markers.clear()
            elif isinstance(event, Events.Commit):
                db.commit()
                faiss.write_index(index, str(index_path))


def persist_embeddings(embeddings: T.Iterable[Embedding], db: sqlite3.Connection):
    db.executemany(
        """
            insert into embedding
            (object_id, data, content_sha256)
            values
            (:object_id, :data, :content_sha256)
        """,
        [
            {
                'object_id': e.object_id,
                'data': e.data,
                'content_sha256': e.content_hash
            }
            for e in embeddings
        ]
    )


if __name__ == '__main__':
    main()
