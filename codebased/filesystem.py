from __future__ import annotations

import os
import subprocess
from pathlib import Path


def get_git_files(path: Path) -> list[Path]:
    proc = subprocess.Popen(['git', 'ls-files', '-c'], stdout=subprocess.PIPE, cwd=path)
    relative_paths = proc.stdout.read().decode('utf-8').split('\n')
    absolute_paths = [path / relative_path for relative_path in relative_paths]
    return [path for path in absolute_paths if path.is_file()]


def is_git_repository(path: Path) -> bool:
    return (path / '.git').exists()


def find_parent_git_repositories(path: Path) -> list[Path]:
    parent = path.parent
    repos = []
    while parent != Path('/'):
        if is_git_repository(parent):
            repos.append(parent)
        parent = parent.parent
    return repos


def find_child_git_repositories(root: Path) -> list[Path]:
    git_repos = []
    for dirpath, dirnames, _ in os.walk(root, topdown=True):
        if '.git' in dirnames:
            git_repos.append(Path(dirpath))
            dirnames.clear()  # Stop recursing once we find a Git repository
        else:
            # Optional: Remove hidden directories to avoid unnecessary searches
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
    return git_repos


def find_git_repositories(root: Path) -> list[Path]:
    parents = find_parent_git_repositories(root)
    children = find_child_git_repositories(root)
    return parents + children
