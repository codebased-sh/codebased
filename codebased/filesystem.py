from __future__ import annotations

import dataclasses
import queue
from pathlib import Path

import watchdog.events
import watchdog.observers

_OBSERVER = watchdog.observers.Observer()


@dataclasses.dataclass
class EventWrapper:
    event: watchdog.events.FileSystemEvent
    time: float


class QueueEventHandler(watchdog.events.FileSystemEventHandler):
    def __init__(self, q: queue.Queue[Path]):
        self.q = q

    def on_any_event(self, event: watchdog.events.FileSystemEvent):
        if event.is_directory:
            return
        for path in get_paths(event):
            self.q.put(path)


def get_filesystem_events_queue(root: Path) -> queue.Queue[Path]:
    observer = _OBSERVER
    q = queue.Queue()
    observer.schedule(QueueEventHandler(q), root, recursive=True)
    observer.start()
    return q


def get_paths(
        event: watchdog.events.FileSystemEvent
) -> list[Path]:
    abs_paths: list[str] = []
    if event.event_type == 'moved':
        abs_paths = [event.src_path, event.dest_path]
    elif event.event_type == 'created':
        abs_paths = [event.src_path]
    elif event.event_type == 'deleted':
        abs_paths = [event.src_path]
    elif event.event_type == 'modified':
        abs_paths = [event.src_path]
    return [Path(path) for path in abs_paths]
