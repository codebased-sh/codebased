from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import sqlite3
import textwrap
import typing as T
from pathlib import Path

import numpy as np

from codebased.models import Object, PersistentObject, FileRevision, PersistentFileRevision, Embedding

logger = logging.getLogger(__name__)


def persist_object(db: sqlite3.Connection, obj: Object) -> PersistentObject:
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
    cursor = db.cursor()
    cursor.execute(textwrap.dedent("""
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
                        select id from file_revision where path = ? and hash = ?
                    );
                """), (file_revision.path, file_revision.hash))
    for row in cursor.fetchall():
        yield PersistentObject(
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
    cursor = db.execute(
        """
        INSERT INTO file_revisions
         (path, hash, size, last_modified)
          VALUES (?, ?, ?, ?)
           RETURNING id
        """,
        (file_revision.path, file_revision.hash, file_revision.size, file_revision.last_modified),
    )
    persistent_revision = PersistentFileRevision(**dataclasses.asdict(file_revision), id=cursor.lastrowid)
    return persistent_revision


def fetch_embedding(db: sqlite3.Connection, object_id: int) -> Embedding:
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
    return Embedding(
        object_id=row['object_id'],
        embedding=np.frombuffer(row['embedding'], dtype=np.float32),
        content_hash=row['content_hash']
    )


def persist_embedding(db: sqlite3.Connection, embedding: Embedding) -> Embedding:
    cursor = db.execute(
        """
        INSERT INTO embedding
         (object_id, embedding, content_hash)
          VALUES (?, ?, ?)
           RETURNING id
        """,
        (
            embedding.object_id,
            embedding.embedding.tobytes(),
            embedding.content_hash
        )
    )
    persistent_embedding = Embedding(**dataclasses.asdict(embedding))
    return persistent_embedding
