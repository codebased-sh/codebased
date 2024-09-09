import subprocess
from pathlib import Path
from typing import Literal

VIM_STYLE_EDITORS = {"vi", "nvim", "vim"}


def suspends(editor: Literal["vi", "idea", "code"]) -> bool:
    return editor in VIM_STYLE_EDITORS


def open_editor(editor: Literal["vi", "idea", "code"], *, file: Path, row: int, column: int):
    line_number = row + 1
    if editor in VIM_STYLE_EDITORS:
        subprocess.run([editor, str(file), f"+{line_number}"])
    elif editor == "idea":
        subprocess.run(["idea", "--line", str(line_number), str(file)])
    elif editor == "code":
        subprocess.run(["code", "--goto", f"{file}:{line_number}:{column}"])
    else:
        raise NotImplementedError(editor)
