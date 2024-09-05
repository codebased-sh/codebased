from __future__ import annotations

import contextlib
import dataclasses
import threading
import time
from enum import Enum
from typing import TypeVar, Generic

from rich.syntax import Syntax
from textual import work, events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.reactive import var
from textual.widgets import Input, Footer, Header, Static, ListView, ListItem

from codebased.editor import open_editor, suspends
from codebased.index import Flags, Config, Dependencies
from codebased.search import search_once, render_results, RenderedResult


class Id(str, Enum):
    LATENCY = "latency"
    PREVIEW_CONTAINER = "preview-container"
    SEARCH_INPUT = "search-input"
    RESULTS_LIST = "results-list"
    RESULTS_CONTAINER = "results-container"
    PREVIEW = "preview"

    @property
    def selector(self) -> str:
        return "#" + self.value


V = TypeVar('V')


@dataclasses.dataclass
class HWM(Generic[V]):
    key: float = float('-inf')
    _value: V | None = None
    _lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)

    @property
    def value(self) -> V:
        with self._lock:
            return self._value

    def set(self, key: float, value: V) -> bool:
        with self._lock:
            if key > self.key:
                self.key = key
                self._value = value
                return True
            return False


class Codebased(App):
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("escape", "focus_search", "Focus search"),
        ("tab", "focus_preview", "Focus preview"),
        ("down", "focus_results", "Focus results"),
        ("f", "full_text_search", "Toggle full text search"),
        ("s", "semantic_search", "Toggle semantic search"),
    ]

    show_results = var(False)

    CSS = """
    #results-container {
        width: 100%;
        height: 100%;
    }

    #results-list {
        width: 30%;
        border-right: solid green;
    }

    #preview-container {
        width: 70%;
    }
    """

    def __init__(
            self,
            flags: Flags,
            config: Config,
            dependencies: Dependencies,
    ):
        super().__init__()
        self.flags = flags
        self.config = config
        self.dependencies = dependencies
        self.rendered_results: HWM[list[RenderedResult]] = HWM()
        self.rendered_results.set(0, [])

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="Enter your search query", id=Id.SEARCH_INPUT.value)
        yield Static(id=Id.LATENCY.value, shrink=True)
        with Horizontal(id=Id.RESULTS_CONTAINER.value):
            yield ListView(id=Id.RESULTS_LIST.value, initial_index=0)
            with VerticalScroll(id=Id.PREVIEW_CONTAINER.value):
                yield Static(id=Id.PREVIEW.value, expand=True)
        yield Footer()

    def on_mount(self):
        self.query_one(Id.SEARCH_INPUT.selector).focus()

    async def on_input_changed(self, event: Input.Changed):
        query = event.value
        if len(query) >= 3:
            self.search_background(event.value, time.monotonic())
        else:
            await self.clear_results()

    def action_full_text_search(self):
        self.flags = dataclasses.replace(self.flags, full_text_search=not self.flags.full_text_search)

    def action_semantic_search(self):
        self.flags = dataclasses.replace(self.flags, semantic=not self.flags.semantic)

    @work(thread=True)
    def search_background(self, query: str, start_time: float):
        self.flags = dataclasses.replace(self.flags, query=query)
        results, times = search_once(self.dependencies, self.flags)
        rendered_results, render_times = render_results(self.config, results)
        total_times = times
        for key, value in render_times.items():
            total_times[key] = total_times.get(key, 0) + value
        self.post_message(self.SearchCompleted(rendered_results, start_time, time.monotonic(), total_times))

    class SearchCompleted(Message):
        def __init__(self, results: list[RenderedResult], start: float, finish: float, times: dict[str, float]):
            self.results = results
            self.start = start
            self.finish = finish
            self.times = times
            super().__init__()

        @property
        def latency(self) -> float:
            return self.finish - self.start

    def on_key(self, event: events.Key):
        if event.key == "enter":
            self.select_result()
        elif event.key == "up":
            focused = self.focused
            if isinstance(focused, ListView) and focused.id == Id.RESULTS_LIST.value:
                if focused.index == 0:
                    self.action_focus_search()

    def select_result(self):
        focused = self.focused
        if isinstance(focused, ListView) and focused.id == Id.RESULTS_LIST.value:
            try:
                result = self.rendered_results.value[focused.index]
                self.open_result_in_editor(result)
            except IndexError:
                return
        elif focused and focused.id == Id.SEARCH_INPUT.value:
            self.query_one(Id.RESULTS_LIST.selector, ListView).focus()

    def open_result_in_editor(self, result: RenderedResult):
        file_path = self.config.root / result.obj.path
        row, col = result.obj.coordinates[0]
        contextlib.nullcontext()
        editor = self.dependencies.settings.editor
        with self.suspend() if suspends(editor) else contextlib.nullcontext():
            open_editor(editor, file=file_path, row=row, column=col)

    def action_focus_search(self):
        self.query_one(Id.SEARCH_INPUT.selector, Input).focus()

    def action_focus_preview(self):
        self.query_one(Id.PREVIEW.selector, Static).focus()

    def action_focus_results(self):
        if self.focused and self.focused.id == Id.SEARCH_INPUT.value:
            self.query_one(Id.RESULTS_LIST.selector, ListView).focus()

    async def on_codebased_search_completed(self, message: SearchCompleted):
        def print_latency(total: float, times: dict[str, float]) -> str:
            filtered = {k: v for k, v in times.items() if v >= 0.001}
            breakdown = " + ".join(f"{k}: {v:.3f}s" for k, v in filtered.items())
            return f"Completed in {total:.3f}s" + (f" ({breakdown})" if breakdown else "")

        if not self.rendered_results.set(message.start, message.results):
            return
        self.query_one(Id.LATENCY.selector, Static).update(print_latency(message.latency, message.times))
        results_list = await self.clear_results()
        for result in self.rendered_results.value:
            obj = result.obj
            item_text = f"{str(obj.path)}" if obj.kind == 'file' else f"{str(obj.path)} {obj.name}"
            await results_list.append(ListItem(Static(item_text), id=f"result-{obj.id}"))

        self.show_results = True
        if self.rendered_results.value:
            try:
                self.update_preview(self.rendered_results.value[0])
            except IndexError:
                return

    async def clear_results(self):
        results_list = self.query_one(Id.RESULTS_LIST.selector, ListView)
        await results_list.clear()
        return results_list

    def on_list_view_selected(self, event: ListView.Selected):
        result_id = int(event.item.id.split("-")[1])
        result = next(r for r in self.rendered_results.value if r.obj.id == result_id)
        self.update_preview(result)

    def update_preview(self, result):
        preview = self.query_one(Id.PREVIEW.selector, Static)
        start_line, end_line = result.obj.coordinates[0][0], result.obj.coordinates[1][0]
        try:
            code = result.file_bytes.decode('utf-8')
        except UnicodeDecodeError:
            code = result.file_bytes.decode('utf-16')
        lexer = Syntax.guess_lexer(str(result.obj.path), code)
        highlight_lines = set(range(start_line + 1, end_line + 2))
        syntax = Syntax(
            code,
            lexer,
            theme="dracula",
            line_numbers=True,
            line_range=(min(highlight_lines), max(highlight_lines)),
            highlight_lines=highlight_lines,
            word_wrap=True
        )
        preview.update(syntax)

    def watch_show_results(self, show_results: bool):
        self.query_one(Id.RESULTS_CONTAINER.selector).display = show_results
