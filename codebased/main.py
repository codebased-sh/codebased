import argparse
import sys
from pathlib import Path

VERSION = "0.0.1"


def exit_with_error(
        message: str,
        *,
        exit_code: int = 1
):
    print(
        message,
        file=sys.stderr
    )
    sys.exit(
        exit_code
    )


def main():
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
        search_result_dir = find_root_git_repository(
            args.directory
        )
        if search_result_dir is None:
            exit_with_error(
                'Codebased must be run within a Git repository.'
            )
        print(
            f'Found Git repository {search_result_dir}'
        )


def find_root_git_repository(
        path: Path
):
    # copy to mutate
    search_current_dir = Path(
        path
    ).resolve()
    search_result_dir = None
    do_while_cond = True
    while do_while_cond:
        if (search_current_dir / '.git').is_dir():
            search_result_dir = search_current_dir
        search_current_dir = search_current_dir.parent.resolve()
        do_while_cond = search_current_dir != Path(
            '/'
        )
    return search_result_dir


if __name__ == '__main__':
    main()
