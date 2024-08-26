from __future__ import annotations

import argparse
import os
from pathlib import Path

from codebased.core import main


def cli():
    parser = argparse.ArgumentParser(description="Codebased")
    parser.add_argument(
        "--root",
        type=Path,
        help="The directory to index.",
        default=os.getcwd(),
        required=False,
    )
    args = parser.parse_args()
    main(args.root)
