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

        # Open Vim
        subprocess.run(["vi", str(file), f"+{row}"])

        # Restore the terminal to the state curses left it in
        curses.reset_prog_mode()

        # Refresh the screen
        curses.doupdate()
    elif editor == "idea":
        subprocess.run(["idea", f"--line {row}", str(file)])
    elif editor == "code":
        subprocess.run(["code", "--goto", f"{file}:{row}:{column}"])
    else:
        raise NotImplementedError(editor)
