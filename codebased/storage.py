from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import sqlite3
import struct
import textwrap
import typing as T
from pathlib import Path

from codebased.exceptions import NotFoundException, AlreadyExistsException
from codebased.models import Object, PersistentObject, FileRevision, PersistentFileRevision, Embedding, Repository, \
    PersistentRepository, ObjectHandle, FileRevisionHandle
from codebased.stats import STATS

logger = logging.getLogger(__name__)


def persist_object(db: sqlite3.Connection, obj: Object) -> PersistentObject:
    with STATS.timer("codebased.storage.persist_object.duration"):
        cursor = db.execute(
            """
            INSERT INTO object
             (file_revision_id, name, language, context_before, context_after, kind, byte_range, coordinates)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               RETURNING id
            """,
            (
                obj.file_revision_id,
                obj.name,
                obj.language,
                json.dumps(obj.context_before),
                json.dumps(obj.context_after),
                obj.kind,
                json.dumps(obj.byte_range),
                json.dumps(obj.coordinates)
            )
        )
        persistent_object = PersistentObject(**dataclasses.asdict(obj), id=cursor.lastrowid)
        return persistent_object


def fetch_objects(db: sqlite3.Connection, file_revision: FileRevision) -> T.Iterable[PersistentObject]:
    with STATS.timer("codebased.storage.fetch_objects.duration"):
        cursor = db.cursor()
        cursor.execute(
            textwrap.dedent("""
                        select
                            id,
                            file_revision_id,
                            name,
                            language,
                            context_before,
                            context_after,
                            kind,
                            byte_range,
                            coordinates
                        from object
                        where file_revision_id = (
                            select id from file_revision where repository_id = ? and path = ? and hash = ?
                        );
                    """),
            (
                file_revision.repository_id,
                str(file_revision.path),
                file_revision.hash
            )
        )
    for row in cursor.fetchall():
        yield deserialize_object_row(row)


class DatabaseMigrations:
    def __init__(self, db: sqlite3.Connection, migrations_directory: Path):
        self.db = db
        self.migrations_directory = migrations_directory

    def initialize(self):
        self.db.execute("CREATE TABLE IF NOT EXISTS migrations (version INTEGER PRIMARY KEY)")

    def get_current_version(self) -> int | None:
        cursor = self.db.execute("SELECT version FROM migrations ORDER BY version DESC LIMIT 1")
        version_row = cursor.fetchone()
        return version_row[0] if version_row else None

    def add_version(self, version: int):
        self.db.execute("INSERT INTO migrations (version) VALUES (?)", (version,))

    def migrate(self):
        migration_file_names = [f for f in os.listdir(self.migrations_directory) if f.endswith(".sql")]
        migration_paths = [self.migrations_directory / file for file in migration_file_names]

        def get_migration_version(path: Path) -> int:
            return int(re.match(r'(\d+)', path.name).group(1))

        migration_paths.sort(key=get_migration_version)
        current_version = self.get_current_version()
        for migration_path in migration_paths:
            version = get_migration_version(migration_path)
            if current_version is not None and current_version >= version:
                logger.debug(f"Skipping migration {migration_path}")
                continue
            logger.debug(f"Running migration {migration_path}")
            with open(migration_path) as f:
                migration_text = f.read()
            self.db.executescript(migration_text)
            self.add_version(version)
            self.db.commit()


def persist_file_revision(db: sqlite3.Connection, file_revision: FileRevision) -> PersistentFileRevision:
    with STATS.timer("codebased.storage.persist_file_revision.duration"):
        cursor = db.execute(
            """
            INSERT OR IGNORE INTO file_revision
             (repository_id, path, hash, size, last_modified)
              VALUES (?, ?, ?, ?, ?);
            """,
            (
                file_revision.repository_id,
                str(file_revision.path),
                file_revision.hash,
                file_revision.size,
                file_revision.last_modified
            ),
        )

        row_id = cursor.lastrowid
        was_inserted = cursor.rowcount == 1

        if was_inserted:
            persistent_revision = PersistentFileRevision(**dataclasses.asdict(file_revision), id=row_id)
            return persistent_revision
        raise AlreadyExistsException(row_id)


def serialize_embedding_data(vector: list[float]) -> bytes:
    dimension = len(vector)
    return struct.pack(f'{dimension}f', *vector)


def deserialize_embedding_data(data: bytes) -> list[float]:
    dimension = len(data) // struct.calcsize('f')
    return list(struct.unpack(f'{dimension}f', data))


def fetch_embedding(db: sqlite3.Connection, object_id: int) -> Embedding:
    with STATS.timer("codebased.storage.fetch_embedding.duration"):
        cursor = db.execute(
            """
            select
                id,
                object_id,
                embedding,
                content_hash
            from embedding
            where object_id = ?
            """,
            (object_id,)
        )
        row = cursor.fetchone()
        if row is None:
            raise NotFoundException(object_id)
        return Embedding(
            object_id=row['object_id'],
            data=deserialize_embedding_data(row['embedding']),
            content_hash=row['content_hash']
        )


def fetch_embedding_for_hash(db: sqlite3.Connection, content_hash: str) -> Embedding:
    with STATS.timer("codebased.storage.fetch_embedding_for_hash.duration"):
        cursor = db.execute(
            """
            select
                id,
                object_id,
                embedding,
                content_hash
            from embedding
            where content_hash = ?
            """,
            (content_hash,)
        )
        row = cursor.fetchone()
        if row is None:
            raise NotFoundException(content_hash)
        return Embedding(
            object_id=row['object_id'],
            data=deserialize_embedding_data(row['embedding']),
            content_hash=row['content_hash']
        )


def persist_embedding(db: sqlite3.Connection, embedding: Embedding) -> Embedding:
    with STATS.timer("codebased.storage.persist_embedding.duration"):
        db.execute(
            """
            INSERT INTO embedding
             (object_id, embedding, content_hash)
              VALUES (?, ?, ?)
               RETURNING id
            """,
            (
                embedding.object_id,
                serialize_embedding_data(embedding.data),
                embedding.content_hash
            )
        )
        persistent_embedding = Embedding(**dataclasses.asdict(embedding))
        return persistent_embedding


def persist_repository(db: sqlite3.Connection, repo_object: Repository) -> PersistentRepository:
    with STATS.timer("codebased.storage.persist_repository.duration"):
        cursor = db.execute(
            """
            INSERT OR IGNORE INTO repository
             (path, type)
              VALUES (?, ?)
              ON CONFLICT (path) DO UPDATE SET type = ?
               RETURNING id;
            """,
            (str(repo_object.path), repo_object.type, repo_object.type)
        )
        repo_id = cursor.fetchone()[0]
        persistent_repository = PersistentRepository(**dataclasses.asdict(repo_object), id=repo_id)
        return persistent_repository


def fetch_object_handle(db: sqlite3.Connection, object_id: int) -> ObjectHandle:
    with STATS.timer("codebased.storage.fetch_object_handle.duration"):
        obj = fetch_object(db, object_id)
        file_revision = fetch_file_revision(db, obj.file_revision_id)
        repository = fetch_repository(db, file_revision.repository_id)
        file_revision_handle = FileRevisionHandle(file_revision=file_revision, repository=repository)
        return ObjectHandle(
            file_revision=file_revision_handle,
            object=obj
        )


def fetch_object(db: sqlite3.Connection, object_id: int) -> PersistentObject:
    with STATS.timer("codebased.storage.fetch_object.duration"):
        cursor = db.execute(
            """
            select
                id,
                file_revision_id,
                name,
                language,
                context_before,
                context_after,
                kind,
                byte_range,
                coordinates
            from object
            where id = ?
            """,
            (object_id,)
        )
        row = cursor.fetchone()
        if row is None:
            raise NotFoundException(object_id)
        return deserialize_object_row(row)


def fetch_file_revision(db: sqlite3.Connection, file_revision_id: int) -> PersistentFileRevision:
    with STATS.timer("codebased.storage.fetch_file_revision.duration"):
        cursor = db.execute(
            """
            select
                id,
                repository_id,
                path,
                hash,
                size,
                last_modified
            from file_revision
            where id = ?
            """,
            (file_revision_id,)
        )
        row = cursor.fetchone()
        if row is None:
            raise NotFoundException(file_revision_id)
        return PersistentFileRevision(
            id=row['id'],
            repository_id=row['repository_id'],
            path=Path(row['path']),
            hash=row['hash'],
            size=row['size'],
            last_modified=row['last_modified']
        )


def fetch_repository(db: sqlite3.Connection, repository_id: int) -> PersistentRepository:
    with STATS.timer("codebased.storage.fetch_repository.duration"):
        cursor = db.execute(
            """
            select
                id,
                path,
                type
            from repository
            where id = ?
            """,
            (repository_id,)
        )
        row = cursor.fetchone()
        if row is None:
            raise NotFoundException(repository_id)
        return PersistentRepository(
            id=row['id'],
            path=Path(row['path']),
            type=row['type']
        )


def deserialize_object_row(row: sqlite3.Row) -> PersistentObject:
    return PersistentObject(
        id=row['id'],
        file_revision_id=row['file_revision_id'],
        name=row['name'],
        language=row['language'],
        context_before=json.loads(row['context_before']),
        context_after=json.loads(row['context_after']),
        kind=row['kind'],
        byte_range=json.loads(row['byte_range']),
        coordinates=json.loads(row['coordinates'])
    )
