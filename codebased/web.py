from __future__ import annotations

from collections import Counter
from os import getenv
from pathlib import Path
from typing import Union

from pydantic import BaseModel, Field
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from codebased.index import Flags, Dependencies, Config
from codebased.search import RenderedResult, search_once, render_result
from codebased.settings import Settings
from codebased.index import index_paths


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=3)
    semantic: Union[bool, None] = None
    full_text: Union[bool, None] = None
    rerank: Union[bool, None] = None
    top_k: Union[int, None] = Field(default=None, ge=1, le=100)


class SearchResponse(BaseModel):
    data: list[dict]
    times: dict[str, float]


def get_flags(request: SearchRequest, config: Config) -> Flags:
    return Flags(
        directory=config.flags.directory,
        background=False,
        rebuild_faiss_index=False,
        cached_only=False,
        stats=False,
        semantic=request.semantic if request.semantic is not None else config.flags.semantic,
        full_text_search=request.full_text if request.full_text is not None else config.flags.full_text_search,
        top_k=request.top_k if request.top_k is not None else config.flags.top_k,
        query=request.query,
        rerank=request.rerank if request.rerank is not None else config.flags.rerank
    )


def create_app():
    settings = Settings.always()
    home_directory = getenv("CODEBASED_HOME")
    config = Config(
        flags=Flags(
            directory=Path(home_directory),
            background=False,
            rebuild_faiss_index=False,
            cached_only=True,
            stats=False,
            semantic=True,
            full_text_search=True,
            top_k=32,
            query="",
            rerank=True
        )
    )
    dependencies = Dependencies(
        config=config,
        settings=settings
    )

    app = FastAPI()

    @app.on_event("startup")
    def startup_event():
        if not config.flags.cached_only:
            index_paths(dependencies, config, [config.flags.directory], total=True)

    @app.post("/search", response_model=SearchResponse)
    def search(request: SearchRequest) -> SearchResponse:
        try:
            results, times = search_once(
                dependencies,
                get_flags(request, config)
            )
            total_times = Counter(times)
            rendered_results = []
            for result in results:
                rendered, times = render_result(config, result)
                total_times.update(times)
                rendered_results.append(rendered)
            dicts = [
                {
                    "content": rendered_result.content,
                    "name": rendered_result.obj.name,
                    "path": rendered_result.obj.path,
                    "language": rendered_result.obj.language,
                    "line_length": rendered_result.obj.line_length,
                    "byte_length": rendered_result.obj.byte_range[1] - rendered_result.obj.byte_range[0],
                    "coordinates": rendered_result.obj.coordinates,
                }
                for rendered_result in rendered_results
            ]
            return SearchResponse(data=dicts, times=total_times)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.exception_handler(HTTPException)
    def http_exception_handler(request, exc):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    return app
