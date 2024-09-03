from __future__ import annotations

import logging
import os
import re
import sqlite3
import struct
from pathlib import Path

logger = logging.getLogger(__name__)


def serialize_embedding_data(vector: list[float]) -> bytes:
    dimension = len(vector)
    return struct.pack(f'{dimension}f', *vector)


def deserialize_embedding_data(data: bytes) -> list[float]:
    dimension = len(data) // struct.calcsize('f')
    return list(struct.unpack(f'{dimension}f', data))


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
