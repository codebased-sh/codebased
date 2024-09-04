import queue
import threading
import time
from pathlib import Path

from codebased.index import Dependencies, Flags, Config, index_paths
from codebased.stats import STATS


def background_worker(
        dependencies: Dependencies,
        flags: Flags,
        config: Config,
        shutdown_event: threading.Event,
        event_queue: queue.Queue[Path],
):
    while not shutdown_event.is_set():
        # Wait indefinitely for an event.
        events: list[Path] = [event_queue.get()]
        start = time.monotonic()
        loop_timeout = .1
        while time.monotonic() - start < loop_timeout:
            try:
                events.append(event_queue.get(timeout=loop_timeout))
            except queue.Empty:
                break
        try:
            while not event_queue.empty():
                events.append(event_queue.get(block=False))
        except queue.Empty:
            pass
        # Don't create events when we write to the index, especially from this thread.
        events = [event for event in events if not event.is_relative_to(config.codebased_directory)]
        if not events:
            continue
        if shutdown_event.is_set():
            break
        STATS.increment("codebased.background_worker.updates.total")
        for event in events:
            STATS.increment("codebased.background_worker.updates.event")
            STATS.increment(str(event))
        index_paths(dependencies, config, events, total=False)
        STATS.increment("codebased.background_worker.updates.index")
