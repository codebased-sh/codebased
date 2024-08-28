from __future__ import annotations

import argparse
import logging
import os
import re
import sys
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
    parser = argparse.ArgumentParser(description="Codebased")
    parser.add_argument(
        'query',
        nargs='?',
        default=None,
        help="Optional query string. If provided, the program will run in non-interactive mode with this query.",
    )
    parser.add_argument(
        '-i',
        action='store_true',
        help="Interactive mode. If set, the program will run in interactive mode.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        help="The directory to index.",
        default=os.getcwd(),
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
    if interactive:
        interactive_main(args.root, args.n)
    else:
        noninteractive_main(args.root, args.query, args.n)


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
