from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import sqlite3
import time
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


def semantic_search(dependencies: Dependencies, flags: Flags) -> tuple[list[SemanticSearchResult], dict[str, float]]:
    times = {}
    semantic_results: list[SemanticSearchResult] = []
    start = time.perf_counter()
    emb = create_ephemeral_embedding(
        dependencies.openai_client,
        flags.query,
        dependencies.settings.embeddings
    )
    end = time.perf_counter()
    times['embedding'] = end - start
    start = time.perf_counter()
    distances, object_ids = dependencies.index.search(
        np.array([emb]),
        k=flags.top_k
    )
    end = time.perf_counter()
    times['vss'] = end - start
    distances, object_ids = distances[0], object_ids[0]
    times['sqlite'] = 0
    for object_id, distance in zip(object_ids, distances):
        start = time.perf_counter()
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
        end = time.perf_counter()
        times['sqlite'] += end - start
        obj = deserialize_object_row(object_row)
        result = SemanticSearchResult(obj, float(distance), object_row['file_sha256_digest'])
        semantic_results.append(result)
    return semantic_results, times


_quote_fts_re = re.compile(r'\s+|(".*?")')


def quote_fts_query(query: str) -> str:
    if query.count('"') % 2:
        query += '"'
    bits = _quote_fts_re.split(query)
    bits = [b for b in bits if b and b != '""']
    query = " ".join(
        '"{}"'.format(bit) if not bit.startswith('"') else bit for bit in bits
    )
    return query


def full_text_search(dependencies: Dependencies, flags: Flags) -> tuple[list[FullTextSearchResult], dict[str, float]]:
    fts_results = []
    query = quote_fts_query(flags.query)
    times = {}
    start = time.perf_counter()
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
            'query': query,
            'top_k': flags.top_k
        }
    ).fetchall()
    times['fts'] = time.perf_counter() - start
    for object_row in object_rows:
        obj = deserialize_object_row(object_row)
        fts_results.append(FullTextSearchResult(obj, object_row['rank'], object_row['file_sha256_digest']))
    return fts_results, times


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


@dataclasses.dataclass
class SearchResults:
    results: list[CombinedSearchResult]
    times: dict[str, float]


def search_once(dependencies: Dependencies, flags: Flags) -> tuple[list[CombinedSearchResult], dict[str, float]]:
    try:
        return dependencies.search_cache[flags.query], {}
    except KeyError:
        pass
    semantic_results, semantic_times = semantic_search(dependencies, flags) if flags.semantic else ([], {})
    full_text_results, full_text_times = full_text_search(dependencies, flags) if flags.full_text_search else ([], {})
    results = merge_results(semantic_results, full_text_results)
    results = results[:flags.top_k]
    dependencies.search_cache[flags.query] = results
    total_times = semantic_times
    for key, value in full_text_times.items():
        total_times[key] = total_times.get(key, 0) + value
    return results, total_times


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
) -> tuple[RenderedResult | None, dict[str, float]]:
    abs_path = config.root / result.obj.path
    times = {'disk': 0, 'render': 0}
    try:
        # TODO: Memoize, at least within a search result set.
        start = time.perf_counter()
        underlying_file_bytes = abs_path.read_bytes()
        times['disk'] += time.perf_counter() - start
        actual_sha256 = hashlib.sha256(underlying_file_bytes).digest()
        if result.content_sha256 != actual_sha256:
            return None, times
        start = time.perf_counter()
        lines = underlying_file_bytes.split(b'\n')
        rendered = render_object(result.obj, in_lines=lines)
        times['render'] += time.perf_counter() - start
        rendered_result = RenderedResult(
            obj=result.obj, l2=result.l2, bm25=result.bm25, content_sha256=result.content_sha256, content=rendered,
            file_bytes=underlying_file_bytes
        )
        return rendered_result, times
    except FileNotFoundError:
        return None, times


def render_results(
        config: Config,
        results: list[CombinedSearchResult]
) -> tuple[list[RenderedResult], dict[str, float]]:
    rendered_results, times = [], {}
    for result in results:
        rendered_result, result_times = render_result(config, result)
        rendered_results.append(rendered_result)
        for key, value in result_times.items():
            times[key] = times.get(key, 0) + value
    return rendered_results, times


def print_results(
        config: Config,
        results: list[CombinedSearchResult]
):
    rendered_results, times = render_results(config, results)
    for result in rendered_results:
        print(result.content)
        print()
