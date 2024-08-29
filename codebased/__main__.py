from __future__ import annotations

import argparse
import atexit
import curses
import logging
import os
import textwrap
from pathlib import Path
from colorama import init

from codebased.app import get_app, App
from codebased.core import Flags
from codebased.filesystem import get_file_bytes
from codebased.models import SearchResult
from codebased.stats import STATS
from codebased.ui import logger, restore_terminal, interactive_loop, print_search_result

init(autoreset=True)  # Initialize colorama
LOG_FILE = Path.home() / ".codebased/codebased.log"
try:
    os.mkdir(LOG_FILE.parent)
except FileExistsError:
    pass
logging.basicConfig(level=logging.DEBUG, filename=LOG_FILE)


def cli():
    parser = argparse.ArgumentParser(
        description="Codebased",
        epilog=textwrap.dedent("""
        How to Use
        
        Run `codebased` in the directory of the project you're working on, ideally in an IDE terminal.
        If you don't want to switch to a directory before running Codebased, pass `--root`.
        
        The first time you run codebased, it will ask you to do some configuration.
        Where provided, the defaults are very good.
        
        Once you're set up.
        
        If you're looking for some code, open the codebased session and just start
        typing what comes into your head, watching the results for what you're looking for.
        
        The best matches to your search are displayed in ranked order.
        
        You can switch between matches using the up / down arrow.
        
        A preview of the best match is displayed at the bottom.
        
        To open the match in your editor, navigate to it, using up/down arrows
        and press Enter.
        
        How It Works
        
        When you run codebased, it indexes your codebase, with several layers of caching.
        
        1. It extracts objects like structs, functions, classes, interfaces, types, variables, etc.
        from your codebase.
        2. It computes a vector embedding for each object, including relevant context such as the
        file path, parent class, etc.
        3. It loads this embedding into an embedded vector database using FAISS.
        4. When you run searches, it finds the most similar objects, reads their code from disk, and
        displays them.
        5. In interactive mode, this happens in real time as you type.
        
        The biggest shortcoming of Codebased right now is that it doesn't update the index
        as files change within a single run and takes too long to startup. This is my highest
        priority to fix.
        
        Currently, codebased indexes the following languages:
        - Python
        - Rust
        - C/C++
        - C#
        - Go
        - Java
        - JavaScript
        - PHP
        - Ruby
        - TypeScript
        If you'd like a new language, please message the Discord: https://discord.gg/CA6Jzn2S
        or text Max Conradt @ +1 (913) 808-7343, it's often quite doable.
        """)
    )
    parser.add_argument(
        'query',
        nargs='?',
        default=None,
        help="If provided, the program will run in non-interactive mode and run this query against the index.",
    )
    parser.add_argument(
        '-i',
        action='store_true',
        help="If set, runs in interactive mode, allowing live-updating searches against the index.",
    )
    cwd = Path.cwd()
    parser.add_argument(
        "--root",
        type=Path,
        help=textwrap.dedent("""
        The directory to index. 
        Defaults to the current working directory.
        If the root is in a Git repository, codebased indexes the entire repository.
        Otherwise, codebased indexes all Git repositories under the root.
        """),
        default=cwd,
        required=False,
    )
    parser.add_argument(
        "-n",
        type=int,
        default=10,
        help="Number of results to display (default: 10)",
    )
    parser.add_argument(
        "--no-background",
        action='store_true',
        help="Don't run the background indexing worker.",
    )
    args = parser.parse_args()
    interactive = args.i or args.query is None
    logger.debug(f"Started w/ args: {args}")
    root = args.root.resolve()
    flags = Flags(n=args.n, interactive=interactive, query=args.query, root=root, background=not args.no_background)
    if interactive:
        interactive_main(flags)
    else:
        noninteractive_main(flags)


def display_results(results: list[SearchResult]) -> None:
    for result in results:
        print_search_result(result)


def noninteractive_main(flags: Flags):
    app = get_app()
    app.create_index(flags.root, background=False)
    try:
        results = app.perform_search(flags.query, n=flags.n)
        for result in results:
            print_search_result(result)
    finally:
        logger.debug(STATS.dumps())


def interactive_main(flags: Flags):
    with STATS.timer("codebased.startup.duration"):
        with STATS.timer("codebased.startup.app.duration"):
            app = get_app()
        with STATS.timer("codebased.startup.index.duration"):
            faiss_index = app.create_index(flags.root, background=flags.background)
    try:
        atexit.register(restore_terminal)
        curses.wrapper(lambda stdscr: interactive_loop(stdscr, app, faiss_index, flags))
    except KeyboardInterrupt:
        pass
    finally:
        STATS.import_cache_info(
            "codebased.get_file_bytes.lru_cache_hit_rate",
            get_file_bytes.cache_info(),
        )
        STATS.import_cache_info(
            "codebased.perform_search.lru_cache_hit_rate",
            App.perform_search.cache_info(),
        )
        logger.debug(STATS.dumps())
