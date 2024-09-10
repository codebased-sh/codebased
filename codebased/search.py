from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import numpy as np
import re
import sqlite3
import time
import typing as T
from pathlib import Path

from codebased.utils import decode_text

if T.TYPE_CHECKING:
    from openai import OpenAI

from codebased.embeddings import create_ephemeral_embedding
from codebased.index import Dependencies, Flags, Config

from codebased.models import Object
from codebased.parser import render_object

import bisect


@dataclasses.dataclass(frozen=True)
class Query:
    phrases: list[str]
    keywords: list[str]
    original: str

    @classmethod
    def parse(cls, query: str) -> Query:
        original = query
        phrases = []
        keywords = []

        pattern = r'(?:"((?:[^"\\]|\\.)*)"|\S+)'
        matches = re.finditer(pattern, query)

        for match in matches:
            if match.group(1) is not None:
                phrase = match.group(1).replace('\\"', '"')
                if phrase:
                    phrases.append(phrase)
            else:
                keywords.append(match.group())

        return cls(phrases=phrases, keywords=keywords, original=original)


def get_offsets(text: str, byte_start: int) -> tuple[int, int]:
    return byte_start, ...


def find_highlights(query: Query, text: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    highlights = []

    # Create a list of newline positions
    newline_positions = [m.start() for m in re.finditer('\n', text)]

    def get_line_number(char_index):
        return bisect.bisect(newline_positions, char_index)

    # Highlight keywords
    for keyword in query.keywords:
        for match in re.finditer(re.escape(keyword), text, re.IGNORECASE):
            highlights.append(match.span())

    # Highlight phrases
    for phrase in query.phrases:
        for match in re.finditer(re.escape(phrase), text, re.IGNORECASE):
            highlights.append(match.span())

    # Sort and merge overlapping highlights
    highlights.sort(key=lambda x: x[0])
    merged = []
    for start, end in highlights:
        if merged and merged[-1][1] >= start:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    # Create parallel list of line numbers
    line_numbers = [(get_line_number(start), get_line_number(end - 1)) for start, end in merged]

    return merged, line_numbers


@dataclasses.dataclass
class SemanticSearchResult:
    obj: Object
    distance: float
    content_sha256: bytes


@dataclasses.dataclass
class FullTextSearchResult:
    obj: Object
    name_match: bool
    bm25: float
    content_sha256: bytes


def l2_is_close(l2: float) -> bool:
    return l2 < math.sqrt(2) * .9


@dataclasses.dataclass
class CombinedSearchResult:
    obj: Object
    l2: T.Union[float, None]
    bm25: T.Union[float, None]
    content_sha256: bytes


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

    start = time.perf_counter()
    placeholders = ','.join(['?'] * len(object_ids))
    query = f"""
        SELECT o.*,
               (SELECT sha256_digest FROM file WHERE path = o.path) AS file_sha256_digest
        FROM object o
        WHERE o.id IN ({placeholders})
    """
    object_rows = dependencies.db.execute(query, [int(object_id) for object_id in object_ids]).fetchall()
    end = time.perf_counter()
    times['sqlite'] = end - start

    # Create a mapping of object_id to its index in the search results
    id_to_index = {int(id): index for index, id in enumerate(object_ids)}

    # Sort the object_rows based on their order in the search results
    sorted_object_rows = sorted(object_rows, key=lambda row: id_to_index[row['id']])

    for object_row, distance in zip(sorted_object_rows, distances):
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


def rerank_results(query: str, results: list[CombinedSearchResult], oai_client: "OpenAI") -> list[CombinedSearchResult]:
    json_results = [
        {
            "id": r.obj.id,
            "path": str(r.obj.path),
            "name": r.obj.name,
            "kind": r.obj.kind,
            "line_length": r.obj.line_length,
            "byte_length": r.obj.byte_range[1] - r.obj.byte_range[0],
        }
        for r in results
    ]
    json_results = json.dumps(json_results)
    system_prompt = """
    You're acting as the reranking component in a search engine for code.
    The following is a list of results from a search query.
    Please respond with a JSON list of result IDs, in order of relevance, excluding irrelevant or low quality results.
    Implementations are typically better than tests/mocks/documentation, unless the query
    asked for these specifically.
    Prefer code elements like structs, classes, functions, etc. to entire files.
    Including any non-JSON content will cause the application to crash and / or increase latency, which is bad.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": f"Query: {query}\nResults: {json_results}"}
    ]
    response = oai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        # temperature=0.0,
    )
    content = response.choices[0].message.content
    cleaned_content = content[content.find('['):content.rfind(']') + 1]
    parsed_reranking_results = json.loads(cleaned_content)
    results_by_id = {r.obj.id: r for r in results}
    out = []
    for result_id in parsed_reranking_results:
        try:
            out.append(results_by_id.pop(result_id))
        except KeyError:
            continue
    return out


def full_text_search(dependencies: Dependencies, flags: Flags) -> tuple[list[FullTextSearchResult], dict[str, float]]:
    fts_results = []
    query = quote_fts_query(flags.query)
    times = {}
    start = time.perf_counter()
    object_rows = dependencies.db.execute(
        """
                    with name_matches as (
                               select rowid, true as name_match, rank
                               from fts
                               where name match :query
                               order by rank
                               limit :top_k
                    ),
                    content_matches as (
                               select rowid, false as name_match,  rank
                               from fts(:query)
                               order by rank
                               limit :top_k
                    ),
                    all_matches as (
                               select * from name_matches
                               union all
                               select * from content_matches
                    ),
                    min_rank_by_rowid as (
                               select 
                                rowid, 
                                max(name_match) as name_match,
                                min(rank) as rank
                               from all_matches
                               group by rowid
                               order by name_match desc, rank
                    ),
                    sorted_limited_results as (
                               select 
                                    rowid,
                                    name_match,
                                    rank
                               from min_rank_by_rowid
                               order by name_match desc, rank
                               limit :top_k
                    ),
                    ranked_objects as (
                               select o.id,
                                      o.path,
                                      o.name,
                                      o.language,
                                      o.context_before,
                                      o.context_after,
                                      o.kind,
                                      o.byte_range,
                                      o.coordinates,
                                      s.name_match,
                                      s.rank
                               from object o
                               inner join sorted_limited_results s on o.id = s.rowid
                    )
                    select *,
                           (select sha256_digest from file where path = o.path) as file_sha256_digest
                    from ranked_objects o
                    order by o.name_match desc, o.rank;
                """,
        {
            'query': query,
            'top_k': flags.top_k
        }
    ).fetchall()
    times['fts'] = time.perf_counter() - start
    for object_row in object_rows:
        obj = deserialize_object_row(object_row)
        fts_results.append(
            FullTextSearchResult(
                obj,
                object_row['name_match'],
                object_row['rank'],
                object_row['file_sha256_digest']
            )
        )
    return fts_results, times


def merge_results(
        semantic_results: list[SemanticSearchResult],
        full_text_results: list[FullTextSearchResult]
) -> list[CombinedSearchResult]:
    results: list[CombinedSearchResult] = []
    semantic_ids = {result.obj.id: i for i, result in enumerate(semantic_results)}
    full_text_ids = {result.obj.id: i for i, result in enumerate(full_text_results)}
    both = set(semantic_ids) & set(full_text_ids)
    name_matches = {x.obj.id for x in full_text_results if x.name_match}
    sort_key = {}
    for obj_id in both:
        semantic_index = semantic_ids.pop(obj_id)
        full_text_index = full_text_ids.pop(obj_id)
        semantic_result = semantic_results[semantic_index]
        full_text_result = full_text_results[full_text_index]
        assert semantic_result.content_sha256 == full_text_result.content_sha256
        result = CombinedSearchResult(
            semantic_result.obj,
            semantic_result.distance,
            full_text_result.bm25,
            semantic_result.content_sha256
        )
        sort_key[obj_id] = (
            0,
            min(
                semantic_index,
                full_text_index
            )
        )
        results.append(result)
    for obj_id, full_text_index in full_text_ids.items():
        full_text_result = full_text_results[full_text_index]
        results.append(
            CombinedSearchResult(
                full_text_result.obj,
                None,
                full_text_result.bm25,
                full_text_result.content_sha256
            )
        )
        sort_key[obj_id] = (1, full_text_index)
    for obj_id, semantic_index in semantic_ids.items():
        semantic_result = semantic_results[semantic_index]
        results.append(
            CombinedSearchResult(
                semantic_result.obj,
                semantic_result.distance,
                None,
                semantic_result.content_sha256
            )
        )
        sort_key[obj_id] = (1, semantic_index)
    for i, result in enumerate(full_text_results):
        obj_id = result.obj.id
        if obj_id in name_matches:
            sort_key[obj_id] = (-1, i)
        else:
            break
    return sorted(results, key=lambda r: sort_key[r.obj.id])


@dataclasses.dataclass
class SearchResults:
    results: list[CombinedSearchResult]
    times: dict[str, float]


def search_once(dependencies: Dependencies, flags: Flags) -> tuple[list[CombinedSearchResult], dict[str, float]]:
    try:
        return dependencies.search_cache[flags], {}
    except KeyError:
        pass
    semantic_results, semantic_times = semantic_search(dependencies, flags) if flags.semantic else ([], {})
    full_text_results, full_text_times = full_text_search(dependencies, flags) if flags.full_text_search else ([], {})
    results = merge_results(semantic_results, full_text_results)
    total_times = semantic_times
    if flags.rerank:
        rerank_start = time.perf_counter()
        results = rerank_results(flags.query, results, dependencies.openai_client)
        total_times['reranking'] = time.perf_counter() - rerank_start
    # results = results[:flags.top_k]
    dependencies.search_cache[flags] = results
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
    highlights: list[tuple[int, int]]
    highlighted_lines: list[int]


def render_result(
        config: Config,
        flags: Flags,
        result: CombinedSearchResult,
        **kwargs
) -> tuple[RenderedResult | None, dict[str, float]]:
    abs_path = config.root / result.obj.path
    times = {'disk': 0, 'render': 0}
    parsed = Query.parse(flags.query)
    try:
        # TODO: Memoize, at least within a search result set.
        start = time.perf_counter()
        underlying_file_bytes = abs_path.read_bytes()
        times['disk'] += time.perf_counter() - start
        actual_sha256 = hashlib.sha256(underlying_file_bytes).digest()
        if result.content_sha256 != actual_sha256:
            return None, times
        start = time.perf_counter()
        decoded_text = decode_text(underlying_file_bytes)
        if decoded_text is None:
            return None, times
        lines = decoded_text.splitlines()
        rendered = render_object(result.obj, lines, **kwargs)
        times['render'] += time.perf_counter() - start
        highlights, highlighted_lines = find_highlights(parsed, rendered)
        rendered_result = RenderedResult(
            obj=result.obj,
            l2=result.l2,
            bm25=result.bm25,
            content_sha256=result.content_sha256,
            content=rendered,
            file_bytes=underlying_file_bytes,
            highlights=highlights,
            highlighted_lines=sorted({y for x in highlighted_lines for y in x})
        )
        return rendered_result, times
    except FileNotFoundError:
        return None, times


def render_results(
        config: Config,
        flags: Flags,
        results: list[CombinedSearchResult],
        **kwargs
) -> tuple[list[RenderedResult], dict[str, float]]:
    rendered_results, times = [], {}
    for result in results:
        rendered_result, result_times = render_result(config, flags, result, **kwargs)
        rendered_results.append(rendered_result)
        for key, value in result_times.items():
            times[key] = times.get(key, 0) + value
    return rendered_results, times


def print_results(
        config: Config,
        flags: Flags,
        results: list[CombinedSearchResult]
):
    rendered_results, times = render_results(config, flags, results)
    for result in rendered_results:
        print(result.content)
        print()
