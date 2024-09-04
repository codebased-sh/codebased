from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
from pathlib import Path

import math
import numpy as np

from codebased.embeddings import create_ephemeral_embedding
from codebased.index import Dependencies, Flags, Config

from codebased.models import Object
from codebased.parser import render_object


@dataclasses.dataclass
class SemanticSearchResult:
    obj: Object
    distance: float
    content_sha256: bytes


@dataclasses.dataclass
class FullTextSearchResult:
    obj: Object
    bm25: float
    content_sha256: bytes


def l2_is_close(l2: float) -> bool:
    return l2 < math.sqrt(2) * .9


@dataclasses.dataclass
class CombinedSearchResult:
    obj: Object
    l2: float | None
    bm25: float | None
    content_sha256: bytes

    @property
    def _sort_key(self) -> tuple[float, float]:
        # Exact + semantic matches first.
        if self.l2 is not None and self.bm25 is not None:
            return 0, self.l2
        # Close semantic matches next.
        if self.l2 is not None and self.l2 < math.sqrt(2) * .9:
            return 1, self.l2
        # All exact matches next.
        if self.bm25 is not None:
            return 2, self.bm25
        # All semantic matches next.
        if self.l2 is not None:
            return 3, self.l2
        # This should never happen.
        return float('inf'), 0

    def __lt__(self, other: CombinedSearchResult):
        return self._sort_key < other._sort_key

    def __gt__(self, other: CombinedSearchResult):
        return self._sort_key > other._sort_key

    def __eq__(self, other: CombinedSearchResult):
        return self._sort_key == other._sort_key


def semantic_search(dependencies: Dependencies, flags: Flags) -> list[SemanticSearchResult]:
    semantic_results: list[SemanticSearchResult] = []
    emb = create_ephemeral_embedding(
        dependencies.openai_client,
        flags.query,
        dependencies.settings.embeddings
    )
    distances, object_ids = dependencies.index.search(
        np.array([emb]),
        k=flags.top_k
    )
    distances, object_ids = distances[0], object_ids[0]
    for object_id, distance in zip(object_ids, distances):
        object_row: sqlite3.Row | None = dependencies.db.execute(
            """
                      select
                          id,
                          path,
                          name,
                          language,
                          context_before,
                          context_after,
                          kind,
                          byte_range,
                          coordinates,
                          (select sha256_digest from file where path = o.path) as file_sha256_digest
                      from object o
                      where id = :id;
               """,
            {'id': int(object_id)}
        ).fetchone()
        if object_row is None:
            continue
        obj = deserialize_object_row(object_row)
        result = SemanticSearchResult(obj, float(distance), object_row['file_sha256_digest'])
        semantic_results.append(result)
    return semantic_results


def full_text_search(dependencies: Dependencies, flags: Flags) -> list[FullTextSearchResult]:
    fts_results = []
    object_rows = dependencies.db.execute(
        """
                    with ranked_results as (
                        select rowid, rank
                        from fts(:query)
                        order by rank
                        limit :top_k
                    ),
                    ranked_objects as (
                        select
                            o.id,
                            o.path,
                            o.name,
                            o.language,
                            o.context_before,
                            o.context_after,
                            o.kind,
                            o.byte_range,
                            o.coordinates,
                            r.rank
                        from object o
                        inner join ranked_results r on o.id = r.rowid
                    )
                    select 
                        *,
                        (select sha256_digest from file where path = o.path) as file_sha256_digest
                    from ranked_objects o
                    order by o.rank;
                """,
        {
            'query': flags.query,
            'top_k': flags.top_k
        }
    ).fetchall()
    for object_row in object_rows:
        obj = deserialize_object_row(object_row)
        fts_results.append(FullTextSearchResult(obj, object_row['rank'], object_row['file_sha256_digest']))
    return fts_results


def merge_results(
        semantic_results: list[SemanticSearchResult],
        full_text_results: list[FullTextSearchResult]
) -> list[CombinedSearchResult]:
    results: list[CombinedSearchResult] = []
    semantic_ids = {result.obj.id: result for result in semantic_results}
    full_text_ids = {result.obj.id: result for result in full_text_results}
    both = set(semantic_ids.keys()) & set(full_text_ids.keys())
    for obj_id in both:
        semantic_result = semantic_ids.pop(obj_id)
        full_text_result = full_text_ids.pop(obj_id)
        assert semantic_result.content_sha256 == full_text_result.content_sha256
        result = CombinedSearchResult(
            semantic_result.obj,
            semantic_result.distance,
            full_text_result.bm25,
            semantic_result.content_sha256
        )
        results.append(result)
    for full_text_result in full_text_ids.values():
        results.append(
            CombinedSearchResult(
                full_text_result.obj,
                None,
                full_text_result.bm25,
                full_text_result.content_sha256
            )
        )
    for semantic_result in semantic_ids.values():
        results.append(
            CombinedSearchResult(
                semantic_result.obj,
                semantic_result.distance,
                None,
                semantic_result.content_sha256
            )
        )
    return sorted(results)


def search_once(dependencies: Dependencies, flags: Flags) -> list[CombinedSearchResult]:
    semantic_results = semantic_search(dependencies, flags) if flags.semantic else []
    full_text_results = full_text_search(dependencies, flags) if flags.full_text_search else []
    results = merge_results(semantic_results, full_text_results)
    return results[:flags.top_k]


def deserialize_object_row(object_row: sqlite3.Row) -> Object:
    return Object(
        id=object_row['id'],
        path=Path(object_row['path']),
        name=object_row['name'],
        language=object_row['language'],
        context_before=json.loads(object_row['context_before']),
        context_after=json.loads(object_row['context_after']),
        kind=object_row['kind'],
        byte_range=json.loads(object_row['byte_range']),
        coordinates=json.loads(object_row['coordinates'])
    )


@dataclasses.dataclass
class RenderedResult(CombinedSearchResult):
    content: str
    file_bytes: bytes


def render_result(
        config: Config,
        result: CombinedSearchResult
) -> RenderedResult | None:
    abs_path = config.root / result.obj.path
    try:
        # TODO: Memoize, at least within a search result set.
        underlying_file_bytes = abs_path.read_bytes()
        actual_sha256 = hashlib.sha256(underlying_file_bytes).digest()
        if result.content_sha256 != actual_sha256:
            return None
        lines = underlying_file_bytes.split(b'\n')
        rendered = render_object(result.obj, in_lines=lines)
        return RenderedResult(
            obj=result.obj,
            l2=result.l2,
            bm25=result.bm25,
            content_sha256=result.content_sha256,
            content=rendered,
            file_bytes=underlying_file_bytes
        )
    except FileNotFoundError:
        return None


def render_results(
        config: Config,
        results: list[CombinedSearchResult]
) -> list[RenderedResult]:
    return [
        rendered
        for result in results
        if (rendered := render_result(config, result))
    ]


def print_results(
        config: Config,
        results: list[CombinedSearchResult]
):
    rendered_results = render_results(config, results)
    for result in rendered_results:
        print(result.content)
        print()
