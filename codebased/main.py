from __future__ import annotations

import hashlib
from pathlib import Path

import typer

from codebased.index import Config, Dependencies, index_paths, Flags
from codebased.parser import render_object
from codebased.search import CombinedSearchResult, search_once
from codebased.settings import Settings
from codebased.stats import STATS

VERSION = "0.0.1"


def print_results(
        config: Config,
        results: list[CombinedSearchResult]
):
    for result in results:
        abs_path = config.root / result.obj.path
        try:
            underlying_file_bytes = abs_path.read_bytes()
            actual_sha256 = hashlib.sha256(underlying_file_bytes).digest()
            if result.content_sha256 != actual_sha256:
                continue
            lines = underlying_file_bytes.split(b'\n')
            rendered = render_object(result.obj, in_lines=lines)
            print(rendered)
            print()
        except FileNotFoundError:
            continue


cli = typer.Typer(
    name="Codebased",
)


def version_callback(value: bool):
    if value:
        print(f"Codebased {VERSION}")  # Replace with actual version
        raise typer.Exit()


@cli.callback()
def main(
        version: bool = typer.Option(
            None,
            "-v", "-V", "--version",
            callback=version_callback,
            is_eager=True,
            help="Show the version and exit.",
        ),
):
    pass


@cli.command("search")
def search(
        query: str = typer.Argument(..., help="The search query"),
        directory: Path = typer.Option(
            Path.cwd(),
            "-d",
            "--directory",
            help="Directory to search in.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            resolve_path=True,
            allow_dash=True,
        ),
        rebuild_faiss_index: bool = typer.Option(
            False,
            help="Rebuild the FAISS index.",
            is_flag=True,
        ),
        cached_only: bool = typer.Option(
            False,
            help="Only read from cache. Avoids running stat on every file / reading files.",
            is_flag=True,
        ),
        stats: bool = typer.Option(
            False,
            help="Print stats.",
            is_flag=True,
        ),
        semantic: bool = typer.Option(
            True,
            "--semantic-search",
            help="Use semantic search.",
            is_flag=True,
        ),
        full_text: bool = typer.Option(
            True,
            "--full-text-search",
            help="Use full-text search.",
            is_flag=True,
        ),
        top_k: int = typer.Option(
            10,
            "-k",
            "--top-k",
            help="Number of results to return.",
            min=1,
        ),
):
    flags = Flags(
        directory=directory,
        rebuild_faiss_index=rebuild_faiss_index,
        cached_only=cached_only,
        query=query,
        background=False,
        stats=stats,
        semantic=semantic,
        full_text_search=full_text,
        top_k=top_k
    )
    config = Config(flags=flags)
    settings = Settings.always()
    dependencies = Dependencies(config=config, settings=settings)
    __ = config.root, dependencies.db, dependencies.index

    try:
        if not flags.cached_only:
            index_paths(dependencies, config, [config.root], total=True)
        if flags.query:
            results = search_once(dependencies, flags)
            print_results(config, results)
    finally:
        dependencies.db.close()
    if flags.stats:
        print(STATS.dumps())


if __name__ == '__main__':
    cli()
