import subprocess
from pathlib import Path
from typing import Literal


def open_editor(editor: Literal["vi", "idea", "code"], *, file: Path, row: int, column: int):
    if editor == "vi":
        subprocess.run(["vi", str(file), f"+{row}"])
    elif editor == "idea":
        subprocess.run(["idea", f"--line {row}", str(file)])
    elif editor == "code":
        subprocess.run(["code", "--goto", f"{file}:{row}:{column}"])
    else:
        raise NotImplementedError(editor)
