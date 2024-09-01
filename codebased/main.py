import argparse
import sqlite3
import sys
import typing as T
from pathlib import Path

import faiss

VERSION = "0.0.1"


def find_root_git_repository(path: Path):
    # copy to mutate
    search_current_dir = Path(path).resolve()
    done = False
    while not done:
        if (search_current_dir / '.git').is_dir():
            return search_current_dir
        search_current_dir = search_current_dir.parent.resolve()
        done = search_current_dir == Path('/')
    return None


def exit_with_error(message: str, *, exit_code: int = 1) -> T.NoReturn:
    print(message, file=sys.stderr)
    sys.exit(exit_code)


def get_db(database_file: Path) -> sqlite3.Connection:
    db = sqlite3.connect(database_file, check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


def main():
    # TODO: OpenAI API key / authentication to Codebased API.
    parser = argparse.ArgumentParser(
        description="Codebased CLI tool",
        usage="Codebased [-h | --version] {search} ..."
    )
    parser.add_argument(
        '--version',
        action='version',
        version=f'Codebased {VERSION}'
    )
    subparsers = parser.add_subparsers(
        dest='command',
        required=True
    )

    search_parser = subparsers.add_parser(
        'search',
        help='Search for Git repository',
    )
    # Example: Add an argument to the search command
    search_parser.add_argument(
        '-d', '--directory',
        help='Specify the directory to start the search from',
        default=Path.cwd(),
        type=Path
    )

    args = parser.parse_args()

    if args.command == 'search':
        git_repository = find_root_git_repository(args.directory)
        if git_repository is None:
            exit_with_error('Codebased must be run within a Git repository.')
        git_repository: Path = git_repository
        print(f'Found Git repository {git_repository}')
        codebased_directory = git_repository / '.codebased'
        if not codebased_directory.exists():
            codebased_directory.mkdir()
        db_path = codebased_directory / 'codebased.db'
        index_path = codebased_directory / 'index.faiss'
        # This should create the file if it doesn't exist.
        db = get_db(db_path)
        if index_path.exists():
            index = faiss.read_index(str(index_path))
        else:
            index = faiss.IndexIDMap2(faiss.IndexFlatL2(256))


if __name__ == '__main__':
    main()
