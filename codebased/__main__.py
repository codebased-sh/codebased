from __future__ import annotations

import argparse
import curses
import os
from pathlib import Path
import threading
import faiss

from codebased.app import App, get_app
from codebased.models import SearchResult
from codebased.parser import render_object


def interactive_main(root: Path):
    app = get_app()
    faiss_index = app.create_index(root)
    curses.wrapper(lambda stdscr: interactive_loop(stdscr, app, faiss_index))


def interactive_loop(stdscr, app: App, faiss_index: faiss.Index):
    curses.curs_set(0)

    query = ""
    results = []
    active_index = 0

    # Initialize the screen with some content
    height, width = stdscr.getmaxyx()
    stdscr.clear()
    stdscr.addstr(0, 0, "Search: ")
    stdscr.addstr(2, 0, "Type to start searching...")
    stdscr.refresh()

    stdscr.nodelay(1)

    refresh_event = threading.Event()
    stop_event = threading.Event()

    def refresh_screen():
        while not stop_event.is_set():
            if refresh_event.wait(timeout=0.1):
                height, width = stdscr.getmaxyx()
                stdscr.clear()
                stdscr.addstr(0, 0, f"Search: {query}")
                display_interactive_results(stdscr, results, 2, height - 2, active_index)
                stdscr.refresh()
                refresh_event.clear()

    refresh_thread = threading.Thread(target=refresh_screen)
    refresh_thread.start()

    try:
        while True:
            key = stdscr.getch()

            if key == ord('\n'):  # Enter key
                break
            elif key == 27:  # Escape key
                break
            elif key == curses.KEY_BACKSPACE or key == 127:  # Backspace
                query = query[:-1]
            elif key == curses.KEY_UP:
                active_index = max(0, active_index - 1)
            elif key == curses.KEY_DOWN:
                active_index = min(len(results) - 1, active_index + 1)
            elif key != -1:
                query += chr(key)
            else:
                continue  # Skip refresh if no key was pressed

            results = app.perform_search(query, faiss_index)
            active_index = min(active_index, max(0, len(results) - 1))
            refresh_event.set()

    finally:
        stop_event.set()
        refresh_thread.join()


def display_interactive_results(stdscr, results: list[SearchResult], start_line: int, max_lines: int,
                                active_index: int):
    for i, result in enumerate(results):
        if start_line + i >= max_lines:
            break
        obj = result.object_handle
        score = result.score
        result_str = f"{'> ' if i == active_index else '  '}{obj.file_revision.path}:{obj.object.coordinates[0][0] + 1} {obj.object.name} = {score}"
        stdscr.addstr(start_line + i, 0, result_str[:curses.COLS - 1])

    if 0 <= active_index < len(results):
        active_result = results[active_index]
        detailed_info = get_detailed_info(active_result)
        render_start = start_line + len(results) + 1
        for i, line in enumerate(detailed_info.split('\n')):
            if render_start + i >= max_lines:
                break
            stdscr.addstr(render_start + i, 0, line[:curses.COLS - 1])


def get_detailed_info(result: SearchResult) -> str:
    return render_object(result.object_handle, context=True, file=False, line_numbers=True)


def cli():
    parser = argparse.ArgumentParser(description="Codebased")
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
    args = parser.parse_args()
    if args.i:
        interactive_main(args.root)
    else:
        main(args.root)


def display_results(results: list[SearchResult]) -> None:
    for result in results:
        obj = result.object_handle
        score = result.score
        print(f"{obj.file_revision.path}:{obj.object.coordinates[0][0] + 1} {obj.object.name} = {score}")
        print()
        print(render_object(obj, context=True, file=False, line_numbers=True))


def main(root: Path):
    app = get_app()
    faiss_index = app.create_index(root)
    less_interactive_loop(app, faiss_index)


def less_interactive_loop(app: App, faiss_index: faiss.Index):
    while True:
        query = input("What do you want to search for? ")
        results = app.perform_search(query, faiss_index)
        display_results(results)


if __name__ == '__main__':
    cli()
