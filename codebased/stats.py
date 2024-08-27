import dataclasses
import threading
import time
import typing as T
from collections import defaultdict
from contextlib import contextmanager


@dataclasses.dataclass
class Stats:
    _lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)
    counters: dict[str, float] = dataclasses.field(default_factory=lambda: defaultdict(float))
    ratios: dict[str, tuple[int, int]] = dataclasses.field(default_factory=lambda: defaultdict(lambda: (0, 0)))

    def import_cache_info(self, key: str, cache_info):
        self.import_ratio(
            key,
            cache_info.hits,
            cache_info.hits + cache_info.misses
        )

    def import_ratio(self, key: str, num: int, denom: int):
        with self._lock:
            self.ratios[key] = (num, denom)

    def increment(self, key: str, by: T.Union[int, float] = 1):
        with self._lock:
            self.counters[key] += by

    @contextmanager
    def timer(self, key: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.increment(key, time.perf_counter() - start)

    def hit(self, key: str, yes: bool = True):
        with self._lock:
            if yes:
                self.ratios[key] = (self.ratios[key][0] + 1, self.ratios[key][1] + 1)
            else:
                self.ratios[key] = (self.ratios[key][0], self.ratios[key][1] + 1)

    @contextmanager
    def except_rate(self, key: str):
        try:
            yield
        except (Exception,):
            self.hit(key, yes=True)
        else:
            self.hit(key, yes=False)

    def dumps(self) -> str:
        lines = [f"Counters:"]
        for key, value in self.counters.items():
            lines.append(f"  {key}: {value}")
        lines.append(f"Ratios:")
        for key, (num,denom) in self.ratios.items():
            lines.append(f"  {key}: {num/denom:.3f}")
        return '\n'.join(lines)


STATS = Stats()
