# Codebased

> Search at the speed of thought.

> Find what you're looking for, not just what you typed.

## Features

- Search for code elements, not just lines of text, using [Tree Sitter](https://github.com/tree-sitter/tree-sitter).
- Vector searches using [FAISS](https://github.com/facebookresearch/faiss).
- Creates an index for instant (<100ms) searches on large repositories, i.e. the Linux kernel.
- Interactive mode refreshes results on every keystroke and watches for changes to files.
- Performs [automatic filtering](https://github.com/BurntSushi/ripgrep/blob/master/GUIDE.md#automatic-filtering) similar to the amazing [ripgrep](https://github.com/BurntSushi/ripgrep).
- Integrates with editors like VSCode, IntelliJ, and Vim. 

## How it works

### The first time

Running `codebased init` in a new repo does a few things:
1. Creates a `.codebased.db` file in the repo root. This is a SQLite database.
2. Creates a `.cbignore` file in the repo root, used to filter files in addition to the `.gitignore` file.

If your project contains many large, uninteresting files, i.e. snapshot testing, you should add them here before proceeding.

### `codebased search`

When a query is specified, i.e. `codebased search "pseudoterminal"`, the query is run exactly once and results are
written to stdout. Additional information such as line numbers and file names are written to stderr.

Running `codebased search` without a query enters interactive mode.

Interactive mode has several benefits:
- Results are updated with every keystroke as you type.
- The index updates in the background as your files change.
- Search results update in the background as your files change.


## Acknowledgements

- [Ripgrep](https://github.com/BurntSushi/ripgrep) by [Andrew Gallant](https://blog.burntsushi.net/)
- [Livegrep](https://github.com/livegrep/livegrep) by [Nelson Elhage](https://nelhage.com/)
- [Google Code Search](https://github.com/google/codesearch) by [Russ Cox](https://swtch.com/~rsc/)
- The authors of [SQLite](https://www.sqlite.org) for being [unreasonably awesome](https://www.sqlite.org/fasterthanfs.html)  