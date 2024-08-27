from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

from codebased.stats import STATS


def get_git_files(path: Path) -> list[Path]:
    proc = subprocess.Popen(['git', 'ls-files', '-c'], stdout=subprocess.PIPE, cwd=path)
    relative_paths = proc.stdout.read().decode('utf-8').split('\n')
    return [Path(p) for p in relative_paths if (path / p).is_file()]


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


# This is a hack to avoid threading through the file contents.
# Since we have a streaming pipeline the different stages should be able to share the file.
# i.e. we have computing the SHA1 of the file revision, parsing the file, and creating the embeddings.
# But we should stop memoizing stuff: https://www.youtube.com/watch?v=IroPQ150F6c.
@lru_cache(1)
def get_file_bytes(path: Path) -> bytes:
    with STATS.timer("codebased.get_file_bytes.duration"):
        with open(path, 'rb') as f:
            _bytes = f.read()
            STATS.increment("codebased.get_file_bytes.bytes_read", len(_bytes))
            return _bytes


@lru_cache(1)
def get_file_lines(path: Path) -> list[bytes]:
    return get_file_bytes(path).split(b'\n')
