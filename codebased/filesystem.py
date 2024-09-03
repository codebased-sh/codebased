from __future__ import annotations

import dataclasses
import queue
import time
from pathlib import Path

import watchdog.events
import watchdog.observers

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
