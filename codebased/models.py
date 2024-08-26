from __future__ import annotations

import dataclasses
import hashlib
import typing as T
from datetime import datetime
from pathlib import Path

import numpy as np


@dataclasses.dataclass
class FileRevision:
    path: Path
    hash: str
    size: int
    last_modified: datetime

    @classmethod
    def from_path(cls, path: Path):
        content_hash = hashlib.sha1(path.read_bytes()).hexdigest()
        size = path.stat().st_size
        last_modified = datetime.fromtimestamp(path.stat().st_mtime)
        file_revision = FileRevision(path, content_hash, size, last_modified)
        return file_revision


@dataclasses.dataclass
class PersistentFileRevision(FileRevision):
    id: int


@dataclasses.dataclass
class Object:
    file_revision_id: int
    name: str
    language: str
    context_before: list[int]
    context_after: list[int]
    kind: str
    # text: bytes  # This field is an excellent candidate for removal / using a memoryview.
    byte_range: T.Tuple[int, int]  # [start, end)
    coordinates: Coordinates

    def __len__(self):
        start, end = self.byte_range
        return end - start

    @property
    def line_length(self) -> int:
        return self.coordinates[1][0] - self.coordinates[0][0] + 1


@dataclasses.dataclass
class PersistentObject(Object):
    id: int


@dataclasses.dataclass
class Embedding:
    object_id: int
    embedding: np.ndarray
    content_hash: str


Coordinates = T.Tuple[T.Tuple[int, int], T.Tuple[int, int]]
