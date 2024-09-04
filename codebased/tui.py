from rich.syntax import Syntax
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.reactive import var
from textual.widgets import Input, Footer, Header, Static, ListView, ListItem

from codebased.index import Flags, Config, Dependencies
from codebased.search import search_once, render_results


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
        yield Input(placeholder="Enter your search query", id="search-input")
        with Horizontal(id="results-container"):
            yield ListView(id="results-list")
            with VerticalScroll(id="preview-container"):
                yield Static(id="preview", expand=True)
        yield Footer()

    def on_mount(self):
        self.query_one("#search-input").focus()

    def on_input_submitted(self, event: Input.Submitted):
        query = event.value
        self.flags.query = query
        results = search_once(self.dependencies, self.flags)
        self.rendered_results = render_results(self.config, results)

        results_list = self.query_one("#results-list", ListView)
        results_list.clear()
        for result in self.rendered_results:
            obj = result.obj
            print(obj)
            item_text = f"{obj.path.name}:{obj.coordinates[0][0]} - {obj.kind} ({obj.language})"
            results_list.append(ListItem(item_text, id=f"result-{obj.id}"))

        self.show_results = True

    def on_list_view_selected(self, event: ListView.Selected):
        result_id = int(event.item.id.split("-")[1])
        result = next(r for r in self.rendered_results if r.obj.id == result_id)
        preview = self.query_one("#preview", Static)

        # WARNING: The following code assumes that the coordinates are correct and represent
        # the entire object. This might not always be the case, especially for multi-line objects.
        start_line, end_line = result.obj.coordinates[0][0], result.obj.coordinates[1][0]
        code_lines = result.file_bytes.decode('utf-8').splitlines()[start_line - 1:end_line]
        code = '\n'.join(code_lines)

        syntax = Syntax(
            code,
            result.obj.language,
            theme="monokai",
            line_numbers=True,
            start_line=start_line,
            highlight_lines=set(range(start_line, end_line + 1)),
            word_wrap=True
        )
        preview.update(syntax)

    def watch_show_results(self, show_results: bool):
        """Called when show_results is modified."""
        self.query_one("#results-container").display = show_results

    def on_unmount(self):
        if self.dependencies:
            self.dependencies.db.close()
