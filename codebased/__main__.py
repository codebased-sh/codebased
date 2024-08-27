from __future__ import annotations

import argparse
import curses
import os
import time
from dataclasses import field, dataclass
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


@dataclass
class SharedState:
    query: str = ""
    results: list = field(default_factory=list)
    active_index: int = 0
    scroll_position: int = 0
    needs_refresh: bool = True


def interactive_loop(stdscr, app: App, faiss_index: faiss.Index) -> SearchResult | None:
    curses.curs_set(0)
    stdscr.nodelay(1)

    shared_state = SharedState()
    state_lock = threading.Lock()

    def refresh_screen():
        while True:
            with state_lock:
                if shared_state.needs_refresh:
                    height, width = stdscr.getmaxyx()
                    stdscr.clear()
                    stdscr.addstr(0, 0, f"Search: {shared_state.query}")
                    display_interactive_results(stdscr, shared_state.results, 2, height - 2,
                                                shared_state.active_index, shared_state.scroll_position)
                    stdscr.refresh()
                    shared_state.needs_refresh = False

            time.sleep(0.05)  # Short sleep to reduce CPU usage

    refresh_thread = threading.Thread(target=refresh_screen, daemon=True)
    refresh_thread.start()

    while True:
        key = stdscr.getch()

        with state_lock:
            if key == ord('\n'):  # Enter key
                if shared_state.results:
                    return shared_state.results[shared_state.active_index]
                break
            elif key == 27:  # Escape key
                break
            elif key == curses.KEY_BACKSPACE or key == 127:  # Backspace
                shared_state.query = shared_state.query[:-1]
            elif key == curses.KEY_UP:
                shared_state.active_index = max(0, shared_state.active_index - 1)
                shared_state.scroll_position = 0
            elif key == curses.KEY_DOWN:
                shared_state.active_index = min(len(shared_state.results) - 1, shared_state.active_index + 1)
                shared_state.scroll_position = 0
            elif key == curses.KEY_PPAGE:  # Page Up
                shared_state.scroll_position = max(0, shared_state.scroll_position - 10)
            elif key == curses.KEY_NPAGE:  # Page Down
                shared_state.scroll_position += 10
            elif key != -1:
                shared_state.query += chr(key)
            else:
                continue  # Skip refresh if no key was pressed

            shared_state.results = app.perform_search(shared_state.query, faiss_index)
            shared_state.active_index = min(shared_state.active_index, max(0, len(shared_state.results) - 1))
            shared_state.needs_refresh = True

    return None


def display_interactive_results(stdscr, results: list[SearchResult], start_line: int, max_lines: int, active_index: int,
                                scroll_position: int):
    height, width = stdscr.getmaxyx()

    # Display results
    for i, result in enumerate(results):
        if i >= max_lines // 2:
            break
        obj = result.object_handle
        score = result.score
        result_str = f"{'> ' if i == active_index else '  '}{obj.file_revision.path}:{obj.object.coordinates[0][0] + 1} {obj.object.name} = {score}"
        stdscr.addstr(start_line + i, 0, result_str[:width - 1])

    # Display detailed information for the active result
    if 0 <= active_index < len(results):
        active_result = results[active_index]
        detailed_info = get_detailed_info(active_result)
        render_start = start_line + len(results[:max_lines // 2]) + 1
        detailed_lines = detailed_info.split('\n')

        for i, line in enumerate(detailed_lines[scroll_position:]):
            if render_start + i >= height:
                break
            stdscr.addstr(render_start + i, 0, line[:width - 1])

        # Display scroll indicator
        if len(detailed_lines) > height - render_start:
            scroll_percentage = min(100, int(100 * scroll_position / (len(detailed_lines) - (height - render_start))))
            stdscr.addstr(height - 1, width - 5, f"{scroll_percentage:3d}%")


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
