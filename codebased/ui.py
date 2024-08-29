from __future__ import annotations

import curses
import logging
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from colorama import Fore, Style

from codebased.app import App
from codebased.core import Flags
from codebased.editor import open_editor
from codebased.exceptions import BadFileException
from codebased.models import SearchResult
from codebased.parser import render_object

logger = logging.getLogger(__name__)


def restore_terminal():
    """Restore the terminal to a sane state."""
    curses.nocbreak()
    curses.echo()
    curses.curs_set(1)  # Show cursor
    curses.reset_shell_mode()
    curses.endwin()
    # Exit alternate screen
    sys.stdout.write("\033[?1049l")
    sys.stdout.flush()


@dataclass
class SharedState:
    query: str = ""
    results: list = field(default_factory=list)
    active_index: int = 0
    scroll_position: int = 0
    needs_refresh: bool = True
    latest_completed_search_id: int = -1
    current_search_id: int = 0


def perform_search(search_id, app, query, shared_state, state_lock, n):
    try:
        results = app.perform_search(query, n=n)
        with state_lock:
            shared_state.results = results
            shared_state.latest_completed_search_id = search_id
            shared_state.needs_refresh = True
    except Exception as e:
        logger.exception(f"Error in search: {e}")
        raise


def interactive_loop(stdscr, app: App, flags: Flags):
    curses.curs_set(0)
    stdscr.nodelay(1)

    shared_state = SharedState()
    state_lock = threading.Lock()

    def refresh_screen():
        while True:
            with state_lock:
                height, width = stdscr.getmaxyx()
                stdscr.clear()
                if shared_state.query:
                    stdscr.addstr(0, 0, f"Search: {shared_state.query}")
                else:
                    stdscr.addstr(0, 0, "Search: Try typing something...")
                display_interactive_results(stdscr, shared_state.results, 2, height - 2,
                                            shared_state.active_index, shared_state.scroll_position)
                stdscr.refresh()
            time.sleep(0.05)

    refresh_thread = threading.Thread(target=refresh_screen, daemon=True)
    refresh_thread.start()
    executor = ThreadPoolExecutor()

    while True:
        key = stdscr.getch()

        with state_lock:
            if key == ord('\n'):  # Enter key
                if shared_state.results:
                    selected_result = shared_state.results[shared_state.active_index]
                    start_coord = selected_result.object_handle.object.coordinates[0]
                    open_editor(
                        editor=app.context.config.editor,
                        file=selected_result.object_handle.file_revision.path,
                        row=start_coord[0] + 1,
                        column=start_coord[1] + 1
                    )
                    shared_state.needs_refresh = True
                continue
            elif key == 27:  # Escape key
                pass
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
                shared_state.query = shared_state.query.replace('ƚ', '')
            else:
                continue  # Skip refresh if no key was pressed

            executor.submit(
                perform_search,
                shared_state.current_search_id,
                app,
                shared_state.query,
                shared_state,
                state_lock,
                n=flags.n
            )

            if shared_state.latest_completed_search_id > shared_state.current_search_id:
                # New results are available
                shared_state.current_search_id = shared_state.latest_completed_search_id
                shared_state.active_index = min(shared_state.active_index, max(0, len(shared_state.results) - 1))
                shared_state.needs_refresh = True

    raise RuntimeError("Should never exit.")


def display_interactive_results(stdscr, results: list[SearchResult], start_line: int, max_lines: int, active_index: int,
                                scroll_position: int):
    height, width = stdscr.getmaxyx()

    # Display results
    for i, result in enumerate(results):
        if i >= max_lines // 2:
            break
        obj = result.object_handle
        score = result.score
        result_str = f"{'> ' if i == active_index else '  '}{obj.file_revision.file_revision.path}:{obj.object.coordinates[0][0] + 1} {obj.object.name}"
        stdscr.addstr(start_line + i, 0, result_str[:width - 1])

    # Display detailed information for the active result
    if 0 <= active_index < len(results):
        active_result = results[active_index]
        try:
            detailed_info = get_detailed_info(active_result)
        except BadFileException as e:
            return
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
    return render_object(result.object_handle, context=True, file=False, line_numbers=True,
                         ensure_hash=result.object_handle.file_revision.file_revision.hash)


def is_stdout_piped():
    return not os.isatty(sys.stdout.fileno())


def print_search_result(result: SearchResult) -> None:
    obj = result.object_handle
    is_piped = is_stdout_piped()

    try:
        rendered_content = render_object(
            obj,
            context=True,
            file=False,
            line_numbers=True,
            ensure_hash=obj.file_revision.file_revision.hash
        )
    except BadFileException:
        return

    if not is_piped:
        # Print metadata to stderr only if not piped
        print(
            f"{Fore.MAGENTA}{obj.file_revision.file_revision.path}:{obj.object.coordinates[0][0] + 1} {obj.object.name}{Style.RESET_ALL}",
            file=sys.stderr
        )

    # Render the object with line numbers

    # Print content to stdout and optionally line numbers to stderr
    for line, code in re.findall(r'^(\s*\d+)\s(.*)$', rendered_content, re.MULTILINE):
        if not is_piped:
            print(f"{Fore.GREEN}{line}{Style.RESET_ALL}", file=sys.stderr, end='')
        print(code)  # This goes to stdout

    if not is_piped:
        print(file=sys.stderr)  # Add a newline after the result for better separation
