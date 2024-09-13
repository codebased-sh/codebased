# Codebased

Codebased is the most powerful code search tool that runs on your computer.

Here's why it's great:

- Search, simplified: Combines semantic and full-text search to find what you're looking for, not just what you typed.
- Search code, not just text: Searches for complete code structures, not just lines of text, in 11 languages.
- Ignores uninteresting files: Respects your `.gitignore` file(s) and ignores hidden directories.
- Instant editing: Selecting a search result opens your favorite editor at the correct file and line number.
- Fast: Indexes your code in seconds, searches in milliseconds, and updates in real-time as you edit your code.
- Open-source, runs locally: No code is stored on remote servers.

## Getting Started

The fastest way to install Codebased is with [pipx](https://github.com/pypa/pipx?tab=readme-ov-file#install-pipx):

```shell
pipx install codebased
```

Verify the installation by running:

```shell
codebased --version
```

If this fails, please double-check your pipx installation.

Next, run the following command in a Git repository to start searching:

```shell
codebased search
```

The first time you run Codebased, it will create a configuration file at `~/.codebased/config.toml`.
It will prompt you for an OpenAI key, you can access a testing key on the [Discord](https://discord.gg/cQrQCAKZ).

Once this is finished, `codebased` will create an index of your codebase, stored in `.codebased` at the root of your
repository.
This takes seconds for most projects, but can take a few minutes for large projects.
Most of the time is spent creating embeddings using the OpenAI API.

Once the index is created, a terminal UI will open with a search bar.
At this point, you can start typing a search query and results will appear as you type.

- You can use the arrow keys and the mouse to navigate the results.
- A preview of the selected result is displayed.
- Pressing enter on the highlighted result opens the file in your editor at the correct line number.
- Pressing escape returns to the search bar.
- As you edit your code, the index will be updated in real-time, and future searches will reflect your changes.

Codebased will run `stat` on all non-ignored files in your repository, which can take a few seconds, but after that
will listen for filesystem events, so it's recommended to use the TUI.

# Development

If you'd like to contribute, bug fixes are welcome, as well as anything in the list
of [issues](https://github.com/codebased-sh/codebased/issues).

Especially welcome is support for your favorite language, as long as:

1. There's a tree-sitter grammar for it.
2. There are Python bindings for it maintained by the excellent [amaanq](https://pypi.org/user/amaanq/).

Also, if there's anything ripgrep does that Codebased doesn't, feel free to file an issue / PR.

Clone the repository:

```shell
git clone https://github.com/codebased-sh/codebased.git
```

Install the project's dependencies (requires [poetry](https://python-poetry.org), using a virtual environment is
recommended):

```shell
poetry install
```

Run the tests (some tests require an `OPENAI_API_KEY` environment variable, usage is de minimis):

```shell
poetry run pytest
```

# Appendix

## Languages

- [X] C
- [X] C#
- [X] C++
- [X] Go
- [X] Java
- [X] JavaScript
- [X] PHP
- [X] Python
- [X] Ruby
- [X] Rust
- [X] TypeScript
- [ ] HTML / CSS
- [ ] SQL
- [ ] Shell
- [ ] Swift
- [ ] Lua
- [ ] Kotlin
- [ ] Dart
- [ ] R
- [ ] Assembly language
- [ ] OCaml
- [ ] Zig
- [ ] Haskell
- [ ] Elixir
- [ ] Erlang
- [ ] TOML?
- [ ] YAML?