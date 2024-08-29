from __future__ import annotations

import dataclasses
import os
import queue
import subprocess
import time
from functools import lru_cache
from pathlib import Path

import watchdog.events
import watchdog.observers

from codebased.exceptions import BadFileException
from codebased.stats import STATS
from codebased.utils import get_content_hash


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
def get_file_bytes(path: Path, *, ensure_hash: str | None = None) -> bytes:
    with STATS.timer("codebased.get_file_bytes.duration"):
        try:
            with open(path, 'rb') as f:
                _bytes = f.read()
                STATS.increment("codebased.get_file_bytes.bytes_read", len(_bytes))
                if ensure_hash is not None:
                    content_hash = get_content_hash(_bytes)
                    if content_hash != ensure_hash:
                        raise BadFileException(path)
                return _bytes
        except FileNotFoundError:
            raise BadFileException(path)


@lru_cache(1)
def get_file_lines(path: Path, *, ensure_hash: str | None = None) -> list[bytes]:
    return get_file_bytes(path, ensure_hash=ensure_hash).split(b'\n')


_OBSERVER = watchdog.observers.Observer()


@dataclasses.dataclass
class EventWrapper:
    event: watchdog.events.FileSystemEvent
    time: float


class QueueEventHandler(watchdog.events.FileSystemEventHandler):
    def __init__(self, q: queue.Queue[EventWrapper]):
        self.q = q

    def on_any_event(self, event: watchdog.events.FileSystemEvent):
        self.q.put(EventWrapper(event, time.time()))


def get_filesystem_events_queue(root: Path) -> queue.Queue[EventWrapper]:
    observer = _OBSERVER
    q = queue.Queue()
    observer.schedule(QueueEventHandler(q), root, recursive=True)
    observer.start()
    return q
