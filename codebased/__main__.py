from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import textwrap
from pathlib import Path
from colorama import init, Fore, Style

from codebased.app import get_app
from codebased.models import SearchResult
from codebased.parser import render_object
from codebased.stats import STATS
from codebased.ui import logger, interactive_main, is_stdout_piped

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
        The defaults are very good, except for your OpenAI API key.
        
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
    args = parser.parse_args()
    interactive = args.i or args.query is None
    logger.debug(f"Started w/ args: {args}")
    root = args.root.resolve()
    if interactive:
        interactive_main(root, args.n)
    else:
        noninteractive_main(root, args.query, args.n)


def print_search_result(result: SearchResult) -> None:
    obj = result.object_handle
    is_piped = is_stdout_piped()

    if not is_piped:
        # Print metadata to stderr only if not piped
        print(
            f"{Fore.MAGENTA}{obj.file_revision.path}:{obj.object.coordinates[0][0] + 1} {obj.object.name}{Style.RESET_ALL}",
            file=sys.stderr
        )

    # Render the object with line numbers
    rendered_content = render_object(obj, context=True, file=False, line_numbers=True)

    # Print content to stdout and optionally line numbers to stderr
    for line, code in re.findall(r'^(\s*\d+)\s(.*)$', rendered_content, re.MULTILINE):
        if not is_piped:
            print(f"{Fore.GREEN}{line}{Style.RESET_ALL}", file=sys.stderr, end='')
        print(code)  # This goes to stdout

    if not is_piped:
        print(file=sys.stderr)  # Add a newline after the result for better separation


def display_results(results: list[SearchResult]) -> None:
    for result in results:
        print_search_result(result)


def noninteractive_main(root: Path, query: str, n: int):
    app = get_app()
    faiss_index = app.create_index(root)
    try:
        results = app.perform_search(query, faiss_index, n=n)
        display_results(results)
    finally:
        logger.debug(STATS.dumps())
