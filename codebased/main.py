from __future__ import annotations

import sqlite3
import sys
import threading
from pathlib import Path

import typer

from codebased.background_worker import background_worker
from codebased.filesystem import get_filesystem_events_queue
from codebased.index import Config, Dependencies, index_paths, Flags
from codebased.search import search_once, print_results
from codebased.settings import Settings
from codebased.stats import STATS
from codebased.tui import Codebased

VERSION = "0.4.15"

cli = typer.Typer(
    name="Codebased CLI",
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


@cli.command("debug")
def debug():
    import faiss
    import openai
    import sqlite3
    components: dict[str, str] = {
        "Codebased": VERSION,
        "Python": sys.version,
        "SQLite": sqlite3.sqlite_version,
        "FAISS": faiss.__version__,
        "OpenAI": openai.__version__,
    }
    lines = [f"{key}: {value}" for key, value in components.items()]
    typer.echo("\n".join(lines))


@cli.command("search")
def search(
        query: str = typer.Argument("", help="The search query"),
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
            "--semantic-search/--no-semantic-search",
            help="Use semantic search.",
        ),
        full_text: bool = typer.Option(
            True,
            "--full-text-search/--no-full-text-search",
            help="Use full-text search.",
        ),
        top_k: int = typer.Option(
            10,
            "-k",
            "--top-k",
            help="Number of results to return.",
            min=1,
        ),
        background: bool = typer.Option(
            True,
            "--background/--no-background",
            help="Run in the background.",
        ),
):
    sqlite_version = tuple(map(int, sqlite3.sqlite_version.split('.')))
    if sqlite_version < (3, 34, 0):
        typer.echo(f"Codebased requires SQLite 3.9.0 or higher, found {sqlite3.sqlite_version}.", err=True)
        raise typer.Exit(1)
    flags = Flags(
        directory=directory,
        rebuild_faiss_index=rebuild_faiss_index,
        cached_only=cached_only,
        query=query,
        # We gather the set of gitignore files during startup and don't specially cache these.
        # So if we ran the background worker without gathering the .gitignore files, we would not properly ignore
        # changed files.
        background=background and not cached_only,
        stats=stats,
        semantic=semantic,
        full_text_search=full_text,
        top_k=top_k,
        rerank=False
    )
    config = Config(flags=flags)
    settings = Settings.always()
    dependencies = Dependencies(config=config, settings=settings)
    __ = config.root, dependencies.db, dependencies.index
    fs_events = get_filesystem_events_queue(config.root)
    shutdown_event = threading.Event()
    if flags.background:
        thread = threading.Thread(
            target=background_worker,
            args=(dependencies, config, shutdown_event, fs_events),
            daemon=True
        )
    else:
        thread = None

    try:
        if not flags.cached_only:
            index_paths(dependencies, config, [config.root], total=True)
        if thread is not None:
            thread.start()
        if flags.query:
            results, times = search_once(dependencies, flags)
            print_results(config, flags, results)
        else:
            Codebased(flags=flags, config=config, dependencies=dependencies).run()
    finally:
        dependencies.db.close()
        shutdown_event.set()
        if thread is not None:
            thread.join()
    if flags.stats:
        print(STATS.dumps())


if __name__ == '__main__':
    cli()
