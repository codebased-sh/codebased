import argparse
import os
from pathlib import Path

HOME = Path(__file__).parent


def main(root: Path):
    greet()
    # Find all files.


def greet():
    with open(HOME / "GREETING.txt") as f:
        print(f.read())


if __name__ == '__main__':
    __parser = argparse.ArgumentParser(description="Codebased")
    __parser.add_argument(
        "root",
        type=Path,
        help="The directory to index.",
        default=os.getcwd(),
        required=False
    )
    __args = __parser.parse_args()
    main(__args.root)
