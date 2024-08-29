from __future__ import annotations

import atexit
import curses
import logging
import os
import re
import sys
import threading
import typing as T
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
    latest_completed_search_id: int = -1
    current_search_id: int = 0


def perform_search(search_id, app, query, shared_state, state_lock, n):
    try:
        results = app.perform_search(query, n=n)
        with state_lock:
            shared_state.results = results
            shared_state.latest_completed_search_id = search_id
    except Exception as e:
        logger.exception(f"Error in search: {e}")
        raise


@dataclass
class SharedState:
    query: str = ""
    results: list[SearchResult] = field(default_factory=list)
    active_index: int = 0
    scroll_position: int = 0
    refresh_condition: threading.Condition = field(default_factory=threading.Condition)
    latest_completed_search_id: int = -1
    current_search_id: int = 0


class InteractiveSearch:
    def __init__(self, app: App, flags: Flags):
        self.app = app
        self.flags = flags
        self.shared_state = SharedState()
        self.state_lock = threading.Lock()
        self.executor = ThreadPoolExecutor()
        self._shutdown = threading.Event()

    def restore_terminal(self):
        curses.nocbreak()
        curses.echo()
        curses.curs_set(1)
        curses.reset_shell_mode()
        curses.endwin()
        sys.stdout.write("\033[?1049l")
        sys.stdout.flush()

    def perform_search(self, search_id: int, query: str):
        try:
            results = self.app.perform_search(query, n=self.flags.n)
            with self.state_lock:
                self.shared_state.results = results
                self.shared_state.latest_completed_search_id = search_id
        except Exception as e:
            logger.exception(f"Error in search: {e}")
            raise

    def _index_coordinator_worker(self) -> T.NoReturn:
        try:
            while not self._shutdown.is_set():
                # This will make sure the screen updates when the index changes.
                with self.app.index_updated_condition:
                    self.app.index_updated_condition.wait()
                logger.debug("Index updated, scheduling search task")
                self.submit_search_task()
        except Exception as e:
            logger.exception(f"Error in index coordinator: {e}")
            raise

    def refresh_screen(self, stdscr):
        while not self._shutdown.is_set():
            with self.state_lock:
                height, width = stdscr.getmaxyx()
                stdscr.clear()
                if self.shared_state.query:
                    stdscr.addstr(0, 0, f"Search: {self.shared_state.query}")
                else:
                    stdscr.addstr(0, 0, "Search: Try typing something...")
                self.display_interactive_results(stdscr, 2, height - 2)
                stdscr.refresh()
            time.sleep(0.05)

    def display_interactive_results(self, stdscr, start_line: int, max_lines: int):
        height, width = stdscr.getmaxyx()

        for i, result in enumerate(self.shared_state.results):
            if i >= max_lines // 2:
                break
            obj = result.object_handle
            result_str = f"{'> ' if i == self.shared_state.active_index else '  '}{obj.file_revision.file_revision.path}:{obj.object.coordinates[0][0] + 1} {obj.object.name}"
            stdscr.addstr(start_line + i, 0, result_str[:width - 1])

        if 0 <= self.shared_state.active_index < len(self.shared_state.results):
            active_result = self.shared_state.results[self.shared_state.active_index]
            try:
                detailed_info = self.get_detailed_info(active_result)
            except BadFileException:
                return
            render_start = start_line + len(self.shared_state.results[:max_lines // 2]) + 1
            detailed_lines = detailed_info.split('\n')

            for i, line in enumerate(detailed_lines[self.shared_state.scroll_position:]):
                if render_start + i >= height:
                    break
                stdscr.addstr(render_start + i, 0, line[:width - 1])

            if len(detailed_lines) > height - render_start:
                scroll_percentage = min(100, int(100 * self.shared_state.scroll_position / (
                        len(detailed_lines) - (height - render_start))))
                stdscr.addstr(height - 1, width - 5, f"{scroll_percentage:3d}%")

    def get_detailed_info(self, result: SearchResult) -> str:
        return render_object(result.object_handle, context=True, file=False, line_numbers=True,
                             ensure_hash=result.object_handle.file_revision.file_revision.hash)

    def handle_key_input(self, key: int) -> bool:
        if key == ord('\n'):
            if self.shared_state.results:
                selected_result = self.shared_state.results[self.shared_state.active_index]
                start_coord = selected_result.object_handle.object.coordinates[0]
                open_editor(
                    editor=self.app.context.config.editor,
                    file=selected_result.object_handle.file_revision.path,
                    row=start_coord[0] + 1,
                    column=start_coord[1] + 1
                )
            return True
        elif key == 27:  # Escape key
            return False
        elif key in (curses.KEY_BACKSPACE, 127):
            self.shared_state.query = self.shared_state.query[:-1]
        elif key == curses.KEY_UP:
            self.shared_state.active_index = max(0, self.shared_state.active_index - 1)
            self.shared_state.scroll_position = 0
        elif key == curses.KEY_DOWN:
            self.shared_state.active_index = min(len(self.shared_state.results) - 1, self.shared_state.active_index + 1)
            self.shared_state.scroll_position = 0
        elif key == curses.KEY_PPAGE:
            self.shared_state.scroll_position = max(0, self.shared_state.scroll_position - 10)
        elif key == curses.KEY_NPAGE:
            self.shared_state.scroll_position += 10
        elif key != -1:
            self.shared_state.query += chr(key)
            self.shared_state.query = self.shared_state.query.replace('ƚ', '')
        else:
            return True

        self.submit_search_task()

        if self.shared_state.latest_completed_search_id > self.shared_state.current_search_id:
            self.shared_state.current_search_id = self.shared_state.latest_completed_search_id
            self.shared_state.active_index = min(
                self.shared_state.active_index,
                max(0, len(self.shared_state.results) - 1)
            )

        return True

    def submit_search_task(self):
        self.executor.submit(
            self.perform_search,
            self.shared_state.current_search_id,
            self.shared_state.query
        )

    def run(self, stdscr):
        curses.curs_set(0)
        stdscr.nodelay(1)

        atexit.register(self.restore_terminal)

        refresh_thread = threading.Thread(target=self.refresh_screen, args=(stdscr,), daemon=True)
        refresh_thread.start()
        index_coordinator_thread = threading.Thread(target=self._index_coordinator_worker, daemon=True)
        index_coordinator_thread.start()
        try:
            while not self._shutdown.is_set():
                key = stdscr.getch()
                with self.state_lock:
                    if not self.handle_key_input(key):
                        break
        finally:
            # will already be set unless there was an exception.
            self._shutdown.set()
            index_coordinator_thread.join()
            refresh_thread.join()


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
