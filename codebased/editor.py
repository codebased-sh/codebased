import curses
import subprocess
from pathlib import Path
from typing import Literal


def open_editor(editor: Literal["vi", "idea", "code"], *, file: Path, row: int, column: int):
    if editor == "vi":
        # Save the current terminal state
        curses.def_prog_mode()

        # Exit curses mode temporarily
        curses.endwin()

        # Clear the screen using ANSI escape sequence
        print("\033[2J\033[H", end="", flush=True)

        # Open Vim
        subprocess.run(["vi", str(file), f"+{row}"])

        # Clear the screen again
        print("\033[2J\033[H", end="", flush=True)

        # Restore the terminal to the state curses left it in
        curses.reset_prog_mode()

        # Refresh the screen
        curses.doupdate()

        # Force a redraw of the entire screen
        stdscr = curses.initscr()
        stdscr.clear()
        stdscr.refresh()
    elif editor == "idea":
        subprocess.run(["idea", "--line", str(row), str(file)])
    elif editor == "code":
        subprocess.run(["code", "--goto", f"{file}:{row}:{column}"])
    else:
        raise NotImplementedError(editor)