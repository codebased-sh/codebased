from enum import Enum

from rich.syntax import Syntax
from textual import work, events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.reactive import var
from textual.widgets import Input, Footer, Header, Static, ListView, ListItem

from codebased.editor import open_editor
from codebased.index import Flags, Config, Dependencies
from codebased.search import search_once, render_results


class Id(str, Enum):
    PREVIEW_CONTAINER = "preview-container"
    SEARCH_INPUT = "search-input"
    RESULTS_LIST = "results-list"
    RESULTS_CONTAINER = "results-container"
    PREVIEW = "preview"

    def html(self):
        return f"#{self.value}"


class Codebased(App):
    BINDINGS = [("q", "quit", "Quit")]

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
        self.rendered_results = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="Enter your search query", id=Id.SEARCH_INPUT.value)
        with Horizontal(id=Id.RESULTS_CONTAINER.value):
            yield ListView(id=Id.RESULTS_LIST.value, initial_index=0)
            with VerticalScroll(id=Id.PREVIEW_CONTAINER.value):
                yield Static(id=Id.PREVIEW.value, expand=True)
        yield Footer()

    def on_mount(self):
        self.query_one(Id.SEARCH_INPUT.html()).focus()

    async def on_input_changed(self, event: Input.Changed):
        query = event.value
        if len(query) >= 3:
            self.search_background(event.value)
        else:
            await self.clear_results()

    @work(exclusive=True, thread=True)
    def search_background(self, query: str):
        self.flags.query = query
        results = search_once(self.dependencies, self.flags)
        rendered_results = render_results(self.config, results)
        self.post_message(self.SearchCompleted(rendered_results))

    class SearchCompleted(Message):
        def __init__(self, results):
            self.results = results
            super().__init__()

    def on_key(self, event: events.Key) -> None:
        # Check these keys. Where are they documented?
        focused = self.focused
        if event.key == "enter":
            if focused and focused.id == Id.RESULTS_LIST.value and isinstance(focused, ListView):
                result = self.rendered_results[focused.index]
                config = self.config
                file_path = config.root / result.obj.path
                row, col = result.obj.coordinates[0]
                with self.suspend():
                    open_editor(self.dependencies.settings.editor, file=file_path, row=row, column=col)
            else:
                self.query_one(Id.RESULTS_LIST.html(), ListView).focus()
            return
        elif event.key == "escape":
            self.query_one(Id.SEARCH_INPUT.html(), Input).focus()
            return
        elif event.key == "tab":
            self.query_one(Id.PREVIEW.html(), Static).focus()
        elif event.key == "down":
            if focused and focused.id == Id.RESULTS_CONTAINER.value:
                self.query_one(Id.RESULTS_LIST.html(), ListView).focus()

    async def on_codebased_search_completed(self, message: SearchCompleted):
        self.rendered_results = message.results

        results_list = await self.clear_results()
        for result in self.rendered_results:
            obj = result.obj
            item_text = f"{str(obj.path)}" if obj.kind == 'file' else f"{str(obj.path)} {obj.name}"
            await results_list.append(ListItem(Static(item_text), id=f"result-{obj.id}"))

        self.show_results = True
        if self.rendered_results:
            self.update_preview(self.rendered_results[0])

    async def clear_results(self):
        results_list = self.query_one(Id.RESULTS_LIST.html(), ListView)
        await results_list.clear()
        return results_list

    def on_list_view_selected(self, event: ListView.Selected):
        result_id = int(event.item.id.split("-")[1])
        result = next(r for r in self.rendered_results if r.obj.id == result_id)
        self.update_preview(result)

    def update_preview(self, result):
        preview = self.query_one(Id.PREVIEW.html(), Static)
        # WARNING: The following code assumes that the coordinates are correct and represent
        # the entire object. This might not always be the case, especially for multi-line objects.
        start_line, end_line = result.obj.coordinates[0][0], result.obj.coordinates[1][0]
        try:
            code = result.file_bytes.decode('utf-8')
        except UnicodeDecodeError:
            code = result.file_bytes.decode('utf-16')
        lexer = Syntax.guess_lexer(str(result.obj.path), code)
        highlight_lines = set(range(start_line + 1, end_line + 1 + 1))
        syntax = Syntax(
            code,
            lexer,
            theme="vs",
            line_numbers=True,
            line_range=(min(highlight_lines), max(highlight_lines)),
            highlight_lines=highlight_lines,
            word_wrap=True
        )
        preview.update(syntax)

    def watch_show_results(self, show_results: bool):
        """Called when show_results is modified."""
        self.query_one(Id.RESULTS_CONTAINER.html()).display = show_results
