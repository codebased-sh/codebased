# Codebased

> Find what you're looking for, not just what you typed.

Codebased is a command-line code search tool that helps you find what you're looking for, not just what you typed.

After making thousands of painstakingly crafted searches with regular expressions, path globbing, etc.
using [rg](https://github.com/BurntSushi/ripgrep)
and [GitHub Code Search](https://github.com/features/code-search) on a [~1 million-line codebase](https://chalk.ai/), I
decided there had to be a better way.

Codebased is the better way.

It's a game changer for medium-sized codebases (>10k lines),
especially with multiple developers.

Here's why it's great:

- Finds entire functions, classes, and variables vs. single lines.
- Incrementally searches as you type, you might not need to type as much as you thought.
- Resilient to typos: mixing up a few characters or even a totally different word won't hurt.
- Opens the code in your favorite editor when you press enter.

## Installation

Simply run:

```shell
pip install codebased
```

## Configuration

When you run `codebased` for the first time, it will create a configuration file at `~/.codebased/config.toml`.
You'll be prompted to enter your OpenAI API key if it's not set via the `OPENAI_API_KEY` environment variable.
You can also choose the editor command.

## Usage

### Interactive mode

To open the interactive search interface, run:

```shell
codebased
```

The first time you run `codebased`, it will create an index of your codebase.

The first index will take O(seconds) to build for medium-sized (>10k lines) codebases and O(minutes) for large
codebases (>100k lines),
but the index is cached for future runs.

Once the index is ready, you'll see a window open up with a search bar:

![Empty Search Bar](https://github.com/codebased-sh/codebased/blob/5c3f4845d54a392503bf0f0f5c9af1b1cd471ed2/assets/empty_search.png?raw=true)

Once you start typing, you'll see a list of results appear:

![Search Bar with Results](https://github.com/codebased-sh/codebased/blob/5c3f4845d54a392503bf0f0f5c9af1b1cd471ed2/assets/search.png?raw=true)

- You can navigate them using the up/down arrow keys.
- A preview of the code for the selected result will be shown.
- You can press enter to open the selected result in your favorite editor. This really works. And it's awesome.
- Press Ctrl+C to exit.

![Open Editor](https://github.com/codebased-sh/codebased/blob/5c3f4845d54a392503bf0f0f5c9af1b1cd471ed2/assets/editor.png?raw=true)

### Non-interactive mode

To make a single query, run:

```shell
codebased "What are you looking for?"
```