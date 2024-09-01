import sys
from pathlib import Path


def exit_with_error(message: str, *, exit_code: int = 1):
    print(message, file=sys.stderr)
    sys.exit(exit_code)


def main():
    workdir = Path.cwd()
    search_result_dir = find_root_git_repository(workdir)
    if search_result_dir is None:
        exit_with_error('Codebased must be run within a Git repository.')
    print(f'Found Git repository {search_result_dir}')


def find_root_git_repository(path: Path):
    # copy to mutate
    search_current_dir = Path(path).resolve()
    search_result_dir = None
    do_while_cond = True
    while do_while_cond:
        if (search_current_dir / '.git').is_dir():
            search_result_dir = search_current_dir
        search_current_dir = search_current_dir.parent.resolve()
        # How would this work on Windows?
        do_while_cond = search_current_dir != Path('/')
    return search_result_dir


if __name__ == '__main__':
    main()
