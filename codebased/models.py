from __future__ import annotations

import dataclasses
import typing as T
from pathlib import Path
from typing import Literal


@dataclasses.dataclass
class Object:
    path: Path
    name: str
    language: str
    context_before: list[int]
    context_after: list[int]
    kind: str
    # text: bytes  # This field is an excellent candidate for removal / using a memoryview.
    byte_range: T.Tuple[int, int]  # [start, end)
    coordinates: Coordinates
    id: int | None = None

    def __len__(self):
        start, end = self.byte_range
        return end - start

    @property
    def line_length(self) -> int:
        return self.coordinates[1][0] - self.coordinates[0][0] + 1


@dataclasses.dataclass
class EmbeddingRequest:
    object_id: int
    content: str
    content_hash: str


@dataclasses.dataclass
class Embedding:
    object_id: int
    data: list[float]
    content_hash: str


Coordinates = T.Tuple[T.Tuple[int, int], T.Tuple[int, int]]

EDITOR = Literal["vi", "idea", "code"]
