from __future__ import annotations

import dataclasses
import typing as T
from datetime import datetime
from pathlib import Path
from typing import Literal


@dataclasses.dataclass
class Repository:
    path: Path
    type: T.Literal["git"]


@dataclasses.dataclass
class PersistentRepository(Repository):
    id: int


@dataclasses.dataclass
class FileRevision:
    repository_id: int
    path: Path
    hash: str
    size: int
    last_modified: datetime


@dataclasses.dataclass
class PersistentFileRevision(FileRevision):
    id: int


@dataclasses.dataclass
class FileRevisionHandle:
    repository: PersistentRepository
    file_revision: PersistentFileRevision

    @property
    def path(self) -> Path:
        return self.repository.path / self.file_revision.path


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
class ObjectHandle:
    file_revision: FileRevisionHandle
    object: PersistentObject


@dataclasses.dataclass
class EmbeddingRequest:
    object_id: int
    content: str
    content_hash: str
    token_count: int


@dataclasses.dataclass
class Embedding:
    object_id: int
    data: list[float]
    content_hash: str


Coordinates = T.Tuple[T.Tuple[int, int], T.Tuple[int, int]]


@dataclasses.dataclass
class SearchResult:
    object_handle: ObjectHandle
    score: float


EDITOR = Literal["vi", "idea", "code"]
