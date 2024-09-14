from __future__ import annotations

import textwrap

import pytest
import string

import dataclasses
import os
import re
import sqlite3
import tempfile
import typing as T
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Union

import faiss
from rich.syntax import Syntax
from textual.widgets import Input, ListView, Static

from codebased.index import find_root_git_repository, Flags, Config, Dependencies, index_paths
from codebased.main import VERSION
from codebased.parser import parse_objects
from codebased.search import Query, find_highlights
from codebased.settings import Settings
from codebased.tui import Codebased, Id

SUBMODULE_REPO_TREE = (Path('.'), (
    (Path('README.md'), b'Hello, world!'),
    (Path('.git'), ()),
    (Path('submodule'), (
        (Path('README.md'), b'Hello, world!'),
        (
            Path('below-submodule'),
            (
                (
                    (
                        Path('code.py'),
                        b'print("Hello, world!")'),
                )
            )
        ),
        # Git submodules contain a .git **FILE**.
        # This points to a subdirectory of the parent .git/modules directory.
        (Path(
            '.git'
        ), b'')
    ))
)
                       )

SIMPLE_NOT_REPO_TREE = (
    Path('.'),
    (
        (Path('README.md'), b'Hello, world!'),
        (Path('a-directory'), (
            ((Path('code.py'), b'print("Hello, world!")'),)
        )),
    )
)

SIMPLE_REPO_TREE = (
    Path(
        '.'
    ),
    (
        (Path(
            'README.md'
        ), b'Hello, world!'),
        (Path(
            'a-directory'
        ), (
             ((Path(
                 'code.py'
             ), b'print("Hello, world!")'),)
         )),
        (Path(
            '.git'
        ), ()),
    )
)

# node_modules in .gitignore
GITIGNORE_FOLDER_TREE = (
    Path('.'),
    (
        (Path('README.md'), b'Hello, world!'),
        (Path('.git'), ()),
        (Path('.gitignore'), b'node_modules/'),
        (
            Path(
                'node_modules'
            ), (
                (
                    Path('slop'), (
                        (Path('slop.js'), b'console.log("Hello, world!");'),
                        (Path('slop.d.ts'), b'declare function slop(): void;'),
                    )
                ),
            )
        ),
        (Path('src'), (
            (Path('index.js'),
             b'const express = require("express");\nconst app = express();\napp.get("/", (req, res) => {\n  res.send("Hello, world!");\n});\n\napp.listen(3000, () => {\n  console.log("Server started on port 3000");\n});\n'),
        )),
        (
            Path('package.json'),
            b'{\n  "name": "test",\n  "version": "1.0.0",\n  "description": "",\n  "main": "index.js",\n  "scripts": {\n    "test": "echo "Error: no test specified" && exit 1"\n  },\n  "author": "",\n  "license": "ISC",\n  "dependencies": {\n    "slop": "^1.0.0"\n  }\n}\n'
        )
    )
)

HIDDEN_FOLDER_TREE = (
    (
        Path('.'),
        (
            (Path('.git'), ()),
            (Path('README.md'), b'Hello, world!'),
            (
                Path('a-directory'), (
                    (Path('code.py'), b'print("Hello, world!")'),
                )
            ),
            (
                Path('.venv'),
                (
                    (
                        Path('bin'),
                        (
                            (Path('activate'), b'this is a script of some sort'),
                        )
                    ),
                    (
                        Path('lib'),
                        (
                            (
                                Path('python3.10'),
                                (
                                    (
                                        Path('site-packages'),
                                        (
                                            (Path('slop'),
                                             ((
                                                  Path('site.py'),
                                                  b'print("Hello, world!")'
                                              ),),

                                             ),

                                        )
                                    ),
                                )
                            ),
                        )
                    ),
                )
            )
        )
    )
)

NESTED_GITIGNORE_TREE = (
    Path('.'),
    (
        (Path('.gitignore'), b'*.txt'),
        # Should be ignored.
        (Path('trash.txt'), b'Hello, world!'),
        (Path('README.md'), b'Hello, world!'),
        (Path('.git'), ()),
        (
            Path('app'), (
                (Path('.gitignore'), b'node_modules/'),
                # Should be ignored.
                (Path('trash.txt'), b'Hello, world!'),
                (Path('src'), (
                    (Path('index.d.ts'), b'console.log("Hello, world!")'),
                    (Path('index.js'), b'console.log("Hello, world!");'),
                )),
                (Path('package.json'), b'{"name": "slop"}'),
                # Should be ignored.
                (Path('node_modules'), (
                    (Path('slop'), (
                        (Path('slop.js'), b'console.log("Hello, world!");'),
                        (Path('slop.d.ts'), b'declare function slop(): void;'),
                    )),
                )),
            )
        ),
        (
            Path('server'), (
                (Path('.gitignore'), b'venv/\n__pycache__/'),
                # Should be ignored.
                (Path('trash.txt'), b'Hello, world!'),
                (Path('src'), (
                    (Path('__pycache__'), (
                        (Path('main.cpython-311.pyc'), b''),
                        (Path('__init__.cpython-311.pyc'), b''),
                    )),
                    (Path('main.py'), b'print("Hello, world!")'),
                    (Path('__init__.py'), b'from .main import *'),
                )),
                (Path('setup.py'), b'{"name": "slop"}'),
                # Should be ignored.
                (Path('venv'), (
                    (Path('slop'), (
                        (Path('slop.py'), b'slop = 1'),
                        (Path('__init__.py'), b''),
                    )),
                )),
            )
        ),
    )
)


@dataclasses.dataclass
class CliTestCase:
    tree: T.Any
    objects: int
    files: int

    def create(self, path: Path):
        create_tree(self.tree, path)


IGNORE_FOLDER_TEST_CASE = CliTestCase(tree=GITIGNORE_FOLDER_TREE, objects=6, files=4)

HIDDEN_FOLDER_TEST_CASE = CliTestCase(tree=HIDDEN_FOLDER_TREE, objects=2, files=2)

SIMPLE_REPO_TEST_CASE = CliTestCase(tree=SIMPLE_REPO_TREE, objects=2, files=2)
NESTED_GITIGNORE_TEST_CASE = CliTestCase(tree=NESTED_GITIGNORE_TREE, objects=10, files=10)


# Algebraically:
#  File = tuple[Path, bytes]
#  Directory = tuple[Path, tuple[DirEntry]]
#  DirEntry = Union[File | Directory]

def create_tree(dir_entry, relative_to: Path):
    path, contents = dir_entry
    absolute_path = relative_to / path
    if isinstance(contents, bytes):
        absolute_path.write_bytes(contents)
    else:
        absolute_path.mkdir(exist_ok=True)
        for entry in contents:
            create_tree(entry, absolute_path)


class TestGitDetection(unittest.TestCase):
    def test_in_a_regular_git_repository(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir).resolve()
            create_tree(SIMPLE_REPO_TREE, path)
            test_paths = [
                path,
                path / 'a-directory',
                path / 'a-directory' / 'code.py',
                path / '.git',
                path / 'README.md'
            ]
            for test_path in test_paths:
                with self.subTest(test_path=test_path):
                    result = find_root_git_repository(test_path)
                    self.assertEqual(result, path)

    def test_in_a_git_repository_with_submodules(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir).resolve()
            create_tree(SUBMODULE_REPO_TREE, path)
            test_paths = [
                path,
                path / 'submodule',
                path / 'submodule' / 'below-submodule',
                path / 'submodule' / 'below-submodule' / 'code.py',
                path / 'README.md'
            ]
            for test_path in test_paths:
                with self.subTest(test_path=test_path):
                    result = find_root_git_repository(test_path)
                    self.assertEqual(result, path)

    def test_run_outside_a_git_repository(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir).resolve()
            create_tree(SIMPLE_NOT_REPO_TREE, path)
            test_paths = [
                path / 'a-directory',
                path / 'a-directory' / 'code.py',
                path / 'README.md',
                path
            ]
            for test_path in test_paths:
                with self.subTest(test_path=test_path):
                    result = find_root_git_repository(test_path)
                    self.assertIs(result, None)

    def test_run_at_root(self):
        # You never know what's going to happen on someone's laptop.
        # Someone's probably managing their entire filesystem using Git.
        # That's how you *win* this test, instead of merely passing it.
        root = Path('/')
        if (root / '.git').is_dir():
            assert find_root_git_repository(root) == root
        else:
            assert find_root_git_repository(root) is None


StreamAssertion = Union[bytes, re.Pattern, None]

import re
import subprocess
from pathlib import Path
from typing import Union

StreamAssertion = Union[bytes, re.Pattern]


def check_codebased_cli(
        *,
        cwd: Path,
        exit_code: int,
        stderr: StreamAssertion,
        stdout: StreamAssertion,
        args: list[str],
        ascii_only: bool = False
):
    proc = subprocess.run(
        ['python', '-m', 'codebased.main', *args],
        cwd=cwd.resolve(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
        env=os.environ,
    )
    actual_stdout, actual_stderr = proc.stdout, proc.stderr

    if ascii_only:
        # Keep only ASCII characters (0x00-0x7F)
        ascii_pattern = re.compile(b"[^" + re.escape(string.printable.encode()) + b"]")
        actual_stdout = ascii_pattern.sub(b'', actual_stdout)
        actual_stderr = ascii_pattern.sub(b'', actual_stderr)

    if proc.returncode != 0 and proc.returncode != exit_code:
        print(f'stdout: {actual_stdout.decode("utf-8")}')
        print(f'stderr: {actual_stderr.decode("utf-8")}')

    assert proc.returncode == exit_code, f'{proc.returncode} != {exit_code}, stdout: {actual_stdout}, stderr: {actual_stderr}'

    if isinstance(stdout, bytes):
        assert actual_stdout == stdout, f'{actual_stdout} != {stdout}'
    elif isinstance(stdout, re.Pattern):
        assert stdout.search(actual_stdout), f'Pattern not found in stdout: {actual_stdout}'

    if isinstance(stderr, bytes):
        assert actual_stderr == stderr, f'{actual_stderr} != {stderr}'
    elif isinstance(stderr, re.Pattern):
        assert stderr.search(actual_stderr), f'Pattern not found in stderr: {actual_stderr}'

    return proc


def check_search_command(
        *,
        args: list[str],
        root: Path,
        cwd: Path,
        exit_code: int,
        stderr: StreamAssertion,
        stdout: StreamAssertion,
        expected_object_count: int | None = None,
        expected_file_count: int | None = None
):
    # working directory
    # root directory
    check_codebased_cli(
        cwd=cwd,
        exit_code=exit_code,
        stderr=stderr,
        stdout=stdout,
        args=args
    )
    codebased_dir = root / '.codebased'
    assert codebased_dir.exists()
    check_db(
        codebased_dir / 'codebased.db',
        expected_object_count=expected_object_count,
        expected_file_count=expected_file_count
    )
    check_faiss_index(
        codebased_dir / 'index.faiss',
        expected_object_count=expected_object_count
    )


def check_faiss_index(path: Path, *, expected_object_count: int | None):
    assert path.exists()
    if expected_object_count is not None:
        faiss_index = faiss.read_index(str(path))
        assert faiss_index.id_map.size() == expected_object_count


def check_db(
        db_path: Path,
        *,
        expected_object_count: int | None,
        expected_file_count: int | None
):
    assert db_path.exists()
    with sqlite3.connect(db_path) as db:
        if expected_object_count is not None:
            cursor = db.execute('select count(*) from object')
            actual_object_count = cursor.fetchone()[0]
            assert actual_object_count == expected_object_count
            cursor = db.execute('select count(*) from fts')
            actual_fts_object_count = cursor.fetchone()[0]
            assert actual_fts_object_count == expected_object_count
            cursor = db.execute('select count(*) from embedding where object_id in (select id from object)')
            actual_embedding_count = cursor.fetchone()[0]
            assert actual_embedding_count == expected_object_count
        if expected_file_count is not None:
            cursor = db.execute('select count(*) from file')
            actual_file_count = cursor.fetchone()[0]
            assert actual_file_count == expected_file_count


@contextmanager
def check_file_did_not_change(path: Path):
    stat = path.stat()
    try:
        yield
    finally:
        # May be overly strict.
        assert path.stat() == stat


class TestCli(unittest.TestCase):
    def test_debug(self):
        with tempfile.TemporaryDirectory() as tempdir:
            stdout = re.compile(
                b"Codebased: \d+\.\d+\.\d+.*Python: \d+\.\d+\.\d+.*SQLite: \d+\.\d+\.\d+.*FAISS: \d+\.\d+\.\d+.*OpenAI: \d+\.\d+\.\d+.*",
                re.ASCII | re.DOTALL
            )
            stderr = b""
            check_codebased_cli(
                args=["debug"],
                cwd=Path(tempdir).resolve(),
                exit_code=0,
                stderr=stderr,
                stdout=stdout,
            )

    def test_run_outside_a_git_repository(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir).resolve()
            create_tree(SIMPLE_NOT_REPO_TREE, path)
            exit_code = 1
            stdout = b""
            stderr = b"Codebased must be run within a Git repository.\n"
            check_codebased_cli(
                cwd=path,
                exit_code=exit_code,
                stderr=stderr,
                stdout=stdout,
                args=["search", "Hello world"]
            )

    def test_run_inside_a_git_repository(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir).resolve()
            # Simple repo has two files.
            SIMPLE_REPO_TEST_CASE.create(path)
            exit_code = 0
            stdout = re.compile(b"Found Git repository " + str(path).encode("utf-8") + b"\n", re.ASCII)
            stderr = re.compile(b".*Indexing " + path.name.encode("utf-8") + b".*", re.ASCII | re.DOTALL)
            check_search_command(
                args=["search", "Hello world"],
                root=path,
                cwd=path,
                exit_code=exit_code,
                stderr=stderr,
                stdout=stdout,
                expected_file_count=SIMPLE_REPO_TEST_CASE.files,
                expected_object_count=SIMPLE_REPO_TEST_CASE.objects
            )
            check_search_command(
                args=["search", "Hello world"],
                root=path,
                cwd=path / "a-directory",
                exit_code=exit_code,
                stderr=stderr,
                stdout=stdout,
                expected_file_count=SIMPLE_REPO_TEST_CASE.files,
                expected_object_count=SIMPLE_REPO_TEST_CASE.objects
            )

    def test_delete_files_between_runs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir).resolve()
            create_tree(SIMPLE_REPO_TREE, path)
            exit_code = 0
            stdout = re.compile(b"Found Git repository " + str(path).encode("utf-8") + b"\n", re.ASCII)
            stderr = re.compile(b".*Indexing " + path.name.encode("utf-8") + b".*", re.ASCII | re.DOTALL)
            search_args = ["search", "Hello world"]
            check_search_command(
                args=search_args,
                root=path,
                cwd=path,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                expected_file_count=SIMPLE_REPO_TEST_CASE.files,
                expected_object_count=SIMPLE_REPO_TEST_CASE.objects
            )
            code_dot_py_path = path / "a-directory" / "code.py"
            os.remove(code_dot_py_path)
            check_search_command(
                args=search_args,
                root=path,
                cwd=path,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                expected_file_count=SIMPLE_REPO_TEST_CASE.files - 1,
                expected_object_count=SIMPLE_REPO_TEST_CASE.objects - 1
            )

    def test_version(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir)
            exit_code = 0
            stdout = f"Codebased {VERSION}\n".encode("utf-8")
            stderr = b""
            check_codebased_cli(
                cwd=path,
                exit_code=exit_code,
                stderr=stderr,
                stdout=stdout,
                args=["--version"]
            )

    @pytest.mark.xfail
    def test_help(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir)
            exit_code = 0
            stderr = b""
            check_codebased_cli(
                cwd=path,
                exit_code=exit_code,
                stderr=stderr,
                stdout=re.compile(
                    rb".*COMMAND.*--version.*-V.*--help.*Commands.*search.*",
                    re.DOTALL | re.ASCII
                ),
                args=["--help"],
                ascii_only=True
            )
            check_codebased_cli(
                cwd=path,
                exit_code=exit_code,
                stderr=stderr,
                stdout=re.compile(rb".*search.*QUERY.*", re.DOTALL | re.ASCII),
                args=["search", "--help"],
                ascii_only=True
            )
            # Note: We"re not checking the exact help output as it might change and be system-dependent

    def test_directory_argument(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir).resolve()
            create_tree(SIMPLE_REPO_TREE, path)
            exit_code = 0
            stdout = re.compile(
                b"Found Git repository " + str(path).encode("utf-8") + b".*",
                re.ASCII | re.DOTALL
            )
            stderr = re.compile(b".*Indexing " + path.name.encode("utf-8") + b".*", re.ASCII | re.DOTALL)

            # Test with -d argument
            workdir = Path.cwd()
            assert workdir != path
            check_search_command(
                cwd=workdir,
                root=path,
                exit_code=exit_code,
                stderr=stderr,
                stdout=stdout,
                args=["search", "Hello world", "-d", str(path)],
                expected_file_count=SIMPLE_REPO_TEST_CASE.files,
                expected_object_count=SIMPLE_REPO_TEST_CASE.objects
            )

            # Test with --directory argument
            check_search_command(
                root=path,
                cwd=workdir,
                exit_code=exit_code,
                stderr=stderr,
                stdout=stdout,
                args=["search", "Hello world", "--directory", str(path)],
                expected_file_count=SIMPLE_REPO_TEST_CASE.files,
                expected_object_count=SIMPLE_REPO_TEST_CASE.objects
            )

    def test_with_gitignore(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir).resolve()
            create_tree(SIMPLE_REPO_TREE, path)
            gitignore_path = path / ".gitignore"
            gitignore_path.write_text("*.py\n")
            exit_code = 0
            stdout = re.compile(b"Found Git repository " + str(path).encode("utf-8") + b".*")
            stderr = re.compile(b".*Indexing " + path.name.encode("utf-8") + b".*", re.ASCII | re.DOTALL)
            check_search_command(
                args=["search", "Hello world"],
                root=path,
                cwd=path,
                exit_code=exit_code,
                stderr=stderr,
                stdout=stdout,
                # +1 for the gitignore file
                # -1 for the .py file because it"s ignored
                expected_file_count=SIMPLE_REPO_TEST_CASE.files - 1 + 1,
                expected_object_count=SIMPLE_REPO_TEST_CASE.objects - 1 + 1
            )

    def test_with_nested_gitignore(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir).resolve()
            create_tree(NESTED_GITIGNORE_TREE, path)
            exit_code = 0
            stdout = re.compile(b"Found Git repository " + str(path).encode("utf-8") + b".*")
            stderr = re.compile(b".*Indexing " + path.name.encode("utf-8") + b".*", re.ASCII | re.DOTALL)
            check_search_command(
                args=["search", "Hello world"],
                root=path,
                cwd=path,
                exit_code=exit_code,
                stderr=stderr,
                stdout=stdout,
                # +1 for the gitignore file
                # -1 for the .py file because it"s ignored
                expected_file_count=NESTED_GITIGNORE_TEST_CASE.files,
                expected_object_count=NESTED_GITIGNORE_TEST_CASE.objects
            )

    def test_rebuild_faiss_index(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir).resolve()
            create_tree(SIMPLE_REPO_TREE, path)
            exit_code = 0
            stdout = re.compile(b"Found Git repository " + str(path).encode("utf-8") + b".*")
            stderr = re.compile(b".*Indexing " + path.name.encode("utf-8") + b".*", re.ASCII | re.DOTALL)
            search_args = ["search", "Hello world"]
            check_search_command(
                args=search_args,
                root=path,
                cwd=path,
                exit_code=exit_code,
                stderr=stderr,
                stdout=stdout,
                expected_file_count=SIMPLE_REPO_TEST_CASE.files,
                expected_object_count=SIMPLE_REPO_TEST_CASE.objects
            )
            check_search_command(
                args=search_args + ["--rebuild-faiss-index"],
                root=path,
                cwd=path,
                exit_code=exit_code,
                stderr=stderr,
                stdout=stdout,
                expected_file_count=SIMPLE_REPO_TEST_CASE.files,
                expected_object_count=SIMPLE_REPO_TEST_CASE.objects
            )

    def test_cached_only(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir).resolve()
            create_tree(SIMPLE_REPO_TREE, path)
            exit_code = 0
            stdout = re.compile(b"Found Git repository " + str(path).encode("utf-8") + b".*")
            stderr = re.compile(b".*Indexing " + path.name.encode("utf-8") + b".*", re.ASCII | re.DOTALL)
            search_args = ["search", "Hello world"]
            check_search_command(
                args=search_args,
                root=path,
                cwd=path,
                exit_code=exit_code,
                stderr=stderr,
                stdout=stdout,
                expected_file_count=SIMPLE_REPO_TEST_CASE.files,
                expected_object_count=SIMPLE_REPO_TEST_CASE.objects
            )
            with check_file_did_not_change(path / ".codebased" / "codebased.db"), \
                    check_file_did_not_change(path / ".codebased" / "index.faiss"):
                check_search_command(
                    args=search_args + ["--cached-only"],
                    root=path,
                    cwd=path,
                    exit_code=exit_code,
                    stderr=b"",
                    stdout=stdout,
                    expected_file_count=SIMPLE_REPO_TEST_CASE.files,
                    expected_object_count=SIMPLE_REPO_TEST_CASE.objects
                )

    def test_cache_only_without_warm_cache(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir).resolve()
            create_tree(SIMPLE_REPO_TREE, path)
            exit_code = 0
            stdout = re.compile(b"Found Git repository " + str(path).encode("utf-8") + b".*")
            stderr = b""
            search_args = ["search", "--cached-only", "Hello world"]
            check_search_command(
                args=search_args,
                root=path,
                cwd=path,
                exit_code=exit_code,
                stderr=stderr,
                stdout=stdout,
                expected_file_count=0,
                expected_object_count=0
            )
            # Check that we only touched the index the first time because it didn"t exist.
            with check_file_did_not_change(path / ".codebased" / "codebased.db"), \
                    check_file_did_not_change(path / ".codebased" / "index.faiss"):
                check_search_command(
                    args=search_args,
                    root=path,
                    cwd=path,
                    exit_code=exit_code,
                    stderr=stderr,
                    stdout=stdout,
                    expected_file_count=0,
                    expected_object_count=0
                )

    def test_semantic_search(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir).resolve()
            create_tree(SIMPLE_REPO_TREE, path)
            exit_code = 0
            stdout = None
            stderr = re.compile(b".*Indexing " + path.name.encode("utf-8") + b".*", re.ASCII | re.DOTALL)
            search_args = ["search", "--semantic-search", "Hello world"]
            check_search_command(
                args=search_args,
                root=path,
                cwd=path,
                exit_code=exit_code,
                stderr=stderr,
                stdout=stdout,
                expected_file_count=SIMPLE_REPO_TEST_CASE.files,
                expected_object_count=SIMPLE_REPO_TEST_CASE.objects
            )

    def test_full_text_search(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir).resolve()
            create_tree(SIMPLE_REPO_TREE, path)
            exit_code = 0
            stdout = None
            stderr = re.compile(b".*Indexing " + path.name.encode("utf-8") + b".*", re.ASCII | re.DOTALL)
            search_args = ["search", "Hello world", "--full-text-search"]
            check_search_command(
                args=search_args,
                root=path,
                cwd=path,
                exit_code=exit_code,
                stderr=stderr,
                stdout=stdout,
                expected_file_count=SIMPLE_REPO_TEST_CASE.files,
                expected_object_count=SIMPLE_REPO_TEST_CASE.objects
            )

    def test_full_text_search_bad_characters(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir).resolve()
            create_tree(SIMPLE_REPO_TREE, path)
            exit_code = 0
            stdout = None
            stderr = re.compile(b".*Indexing " + path.name.encode("utf-8") + b".*", re.ASCII | re.DOTALL)
            search_args = ["search", """print('print("Hello world");');""", "--full-text-search"]
            check_search_command(
                args=search_args,
                root=path,
                cwd=path,
                exit_code=exit_code,
                stderr=stderr,
                stdout=stdout,
                expected_file_count=SIMPLE_REPO_TEST_CASE.files,
                expected_object_count=SIMPLE_REPO_TEST_CASE.objects
            )

    def test_hybrid_search(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir).resolve()
            create_tree(SIMPLE_REPO_TREE, path)
            exit_code = 0
            stdout = None
            stderr = re.compile(b".*Indexing " + path.name.encode("utf-8") + b".*", re.ASCII | re.DOTALL)
            search_args = ["search", "Hello world"]
            check_search_command(
                args=search_args,
                root=path,
                cwd=path,
                exit_code=exit_code,
                stderr=stderr,
                stdout=stdout,
                expected_file_count=SIMPLE_REPO_TEST_CASE.files,
                expected_object_count=SIMPLE_REPO_TEST_CASE.objects
            )

    def test_ignore_folder(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir).resolve()
            create_tree(GITIGNORE_FOLDER_TREE, path)
            exit_code = 0
            stdout = re.compile(b"Found Git repository " + str(path).encode("utf-8") + b"\n", re.ASCII)
            stderr = re.compile(b".*Indexing " + path.name.encode("utf-8") + b".*", re.ASCII | re.DOTALL)
            search_args = ["search", "Server started"]
            check_search_command(
                args=search_args,
                root=path,
                cwd=path,
                exit_code=exit_code,
                stderr=stderr,
                stdout=stdout,
                expected_file_count=IGNORE_FOLDER_TEST_CASE.files,
                expected_object_count=IGNORE_FOLDER_TEST_CASE.objects
            )

    def test_ignore_hidden_folder(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir).resolve()
            create_tree(HIDDEN_FOLDER_TREE, path)
            exit_code = 0
            stdout = re.compile(b"Found Git repository " + str(path).encode("utf-8") + b"\n", re.ASCII)
            stderr = re.compile(b".*Indexing " + path.name.encode("utf-8") + b".*", re.ASCII | re.DOTALL)
            search_args = ["search", "Hello world"]
            check_search_command(
                args=search_args,
                root=path,
                cwd=path,
                exit_code=exit_code,
                stderr=stderr,
                stdout=stdout,
                expected_file_count=HIDDEN_FOLDER_TEST_CASE.files,
                expected_object_count=HIDDEN_FOLDER_TEST_CASE.objects
            )


class TestQueryParsing(unittest.TestCase):
    def test_empty_query(self):
        query = Query.parse('')
        self.assertEqual(query.phrases, [])
        self.assertEqual(query.keywords, [])
        self.assertEqual(query.original, '')
        query = Query.parse('""')
        self.assertEqual(query.phrases, [])
        self.assertEqual(query.keywords, [])
        self.assertEqual(query.original, '""')

    def test_escape_double_quotes(self):
        query = Query.parse('"print(\\\"hello world\\\")"')
        self.assertEqual(query.phrases, ['print("hello world")'])

    def test_parse_basic(self):
        query = Query.parse('hello "world" how are you')
        self.assertEqual(query.phrases, ['world'])
        self.assertEqual(query.keywords, ['hello', 'how', 'are', 'you'])
        self.assertEqual(query.original, 'hello "world" how are you')

    def test_parse_multiple_exact_phrases(self):
        query = Query.parse('"hello world" test "foo bar" baz')
        self.assertEqual(query.phrases, ['hello world', 'foo bar'])
        self.assertEqual(query.keywords, ['test', 'baz'])
        self.assertEqual(query.original, '"hello world" test "foo bar" baz')

    def test_parse_empty_query(self):
        query = Query.parse('')
        self.assertEqual(query.phrases, [])
        self.assertEqual(query.keywords, [])
        self.assertEqual(query.original, '')

    def test_parse_only_exact_phrase(self):
        query = Query.parse('"this is a test"')
        self.assertEqual(query.phrases, ['this is a test'])
        self.assertEqual(query.keywords, [])
        self.assertEqual(query.original, '"this is a test"')

    def test_parse_with_special_characters(self):
        query = Query.parse('hello! "world?" how_are_you')
        self.assertEqual(query.phrases, ['world?'])
        self.assertEqual(query.keywords, ['hello!', 'how_are_you'])
        self.assertEqual(query.original, 'hello! "world?" how_are_you')

    def test_parse_pathological_input(self):
        # This test case creates a pathological input that could cause exponential backtracking
        pathological_input = '"' + 'a' * 100 + '" ' + 'b' * 100
        import time
        start_time = time.time()
        query = Query.parse(pathological_input)
        end_time = time.time()
        parsing_time = end_time - start_time

        self.assertEqual(query.phrases, ['a' * 100])
        self.assertEqual(query.keywords, ['b' * 100])
        self.assertEqual(query.original, pathological_input)

        # Assert that parsing time is reasonable (e.g., less than 1 second)
        self.assertLess(parsing_time, 1.0, "Parsing took too long, possible exponential backtracking")


class TestHighlighting(unittest.TestCase):
    def test_empty_query(self):
        query = Query.parse('')
        self.assertEqual(find_highlights(query, ''), ([], []))
        self.assertEqual(find_highlights(query, '""'), ([], []))
        query = Query.parse('""')
        self.assertEqual(find_highlights(query, ''), ([], []))
        self.assertEqual(find_highlights(query, '""'), ([], []))

    def test_highlights(self):
        query = Query.parse('hello "world" how are you')
        highlights, lines = find_highlights(query, 'hello "world" how are you')
        self.assertEqual(
            highlights,
            [(0, 5), (7, 12), (14, 17), (18, 21), (22, 25)]
        )
        self.assertEqual(lines, [(0, 0)] * len(highlights))
        highlights, lines = find_highlights(query, "hello world how are you")
        self.assertEqual(
            highlights,
            [(0, 5), (6, 11), (12, 15), (16, 19), (20, 23)]
        )
        self.assertEqual(lines, [(0, 0)] * len(highlights))

    def test_out_of_order_highlights(self):
        query = Query.parse('hello "world" how are you')
        text = 'you are how hello world'
        highlights, lines = find_highlights(query, text)
        self.assertEqual(
            highlights,
            [(0, 3), (4, 7), (8, 11), (12, 17), (18, 23)]
        )
        self.assertEqual(lines, [(0, 0), (0, 0), (0, 0), (0, 0), (0, 0)])
        query = Query.parse('"sea world"')
        text = "have you been to sea world?"
        highlights, lines = find_highlights(query, text)
        self.assertEqual(
            highlights,
            [(17, 26)]
        )
        self.assertEqual(lines, [(0, 0)])
        text = "world seap"
        highlights, lines = find_highlights(query, text)
        self.assertEqual(
            highlights,
            []
        )
        self.assertEqual(lines, [])

    def test_multiline_highlights(self):
        query = Query.parse('hello "world" how are you')
        text = 'hello\nworld\nhow\nare\nyou'
        highlights, lines = find_highlights(query, text)
        self.assertEqual(
            highlights,
            [(0, 5), (6, 11), (12, 15), (16, 19), (20, 23)]
        )
        self.assertEqual(lines, [(i, i) for i in range(5)])
        text = '\nhello\nworld\n'
        highlights, lines = find_highlights(query, text)
        self.assertEqual(
            highlights,
            [(1, 6), (7, 12)]
        )
        self.assertEqual(
            lines,
            [(1, 1), (2, 2)]
        )
        query = Query.parse('"hello world"')
        highlights, lines = find_highlights(query, text)
        self.assertEqual(
            highlights,
            []
        )

    def test_case_insensitive_highlights(self):
        query = Query.parse('HELLO "WoRlD" how ARE you')

        text = 'hello world HOW are YOU'
        actual_highlights, lines = find_highlights(query, text)
        self.assertEqual(
            actual_highlights,
            [(0, 5), (6, 11), (12, 15), (16, 19), (20, 23)]
        )
        self.assertEqual(lines, [(0, 0)] * 5)

    def test_partial_phrase_match(self):
        query = Query.parse('"hello world" python')
        text = 'hello worlds of python'
        actual_highlights, lines = find_highlights(query, text)
        self.assertEqual(
            actual_highlights,
            [(0, 11), (16, 22)]
        )

    def test_overlapping_highlights(self):
        query = Query.parse('overlapping overlap lap')
        text = 'this is an overlapping text'
        actual_highlights, lines = find_highlights(query, text)
        left = text.index('overlapping')
        self.assertEqual(
            actual_highlights,
            [(left, left + len('overlapping'))]
        )
        query = Query.parse('overlapping overlap lap over ping')
        text = 'this is an overlapping text'
        actual_highlights, lines = find_highlights(query, text)
        left = text.index('overlapping')
        self.assertEqual(
            actual_highlights,
            [(left, left + len('overlapping'))]
        )
        query = Query.parse('overlapping "an over"')
        text = 'this is an overlapping text'
        actual_highlights, lines = find_highlights(query, text)
        left = text.index('an')
        self.assertEqual(
            actual_highlights,
            [(left, left + len('an overlapping'))]
        )


class AppTestBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name).resolve()
        SIMPLE_REPO_TEST_CASE.create(path)
        self.flags = Flags(
            rerank=False,
            directory=path,
            rebuild_faiss_index=False,
            cached_only=False,
            query="Hello world",
            background=False,
            stats=False,
            semantic=True,
            full_text_search=True,
            top_k=10,
            radius=1.0
        )
        self.settings = Settings()
        self.config = Config(flags=self.flags)
        self.dependencies = Dependencies(
            config=self.config,
            settings=self.settings
        )
        self.setUpIndex()
        self.app = Codebased(flags=self.flags, config=self.config, dependencies=self.dependencies)

    def setUpIndex(self):
        index_paths(
            self.dependencies,
            self.config,
            [self.config.root],
            total=True
        )

    @pytest.mark.xfail
    async def test_search(self):
        async with self.app.run_test() as pilot:
            query = "Hello world"
            for i in range(11):
                this = query[i]
                await pilot.press(this)
                so_far = query[:i + 1]
                search_bar = self.app.query_one(f"#{Id.SEARCH_INPUT}", Input)
                self.assertEqual(search_bar.value, so_far)
            result_list = self.app.query_one(f"#{Id.RESULTS_LIST}", ListView)
            # There should be 2 items.
            self.assertEqual(len(result_list.children), 2)
            preview = self.app.query_one(f"#{Id.PREVIEW}", Static)
            preview_text = preview.renderable
            self.assertIsInstance(preview_text, Syntax)
            code = preview_text.code
            self.assertEqual(code, "Hello, world!")
            self.assertEqual(preview_text.lexer.name, "Markdown")
            focused = self.app.focused
            self.assertEqual(focused.id, Id.SEARCH_INPUT.value)
            await pilot.press("enter")
            focused = self.app.focused
            self.assertEqual(focused.id, Id.RESULTS_LIST.value)
            await pilot.press("tab")
            focused = self.app.focused
            self.assertEqual(focused.id, Id.PREVIEW_CONTAINER.value)
            await pilot.press("escape")
            focused = self.app.focused
            self.assertEqual(focused.id, Id.SEARCH_INPUT.value)
            await pilot.press("r")
            await pilot.press("f")
            # await pilot.press("f")
            # await pilot.press("d")

    def tearDown(self):
        super().tearDown()
        self.tempdir.cleanup()
        self.dependencies.db.close()


@pytest.mark.parametrize("file_type", ["ts", "js", "jsx", "tsx"])
def test_javascript_top_level_variable_declarations(file_type):
    source = textwrap.dedent(
        """
        let stringData = "Hello, world!";
        export const numberData = 123;
        const booleanData = true;
        export const nullData = null;
        export let undefinedData = undefined;
        export var objectData = { id: 1, name: 'John', age: 30 };
        var arrayData = [
            { id: 1, name: 'John', age: 30 },
            { id: 2, name: 'Jane', age: 25 },
            { id: 3, name: 'Bob', age: 35 },
        ];
        
        export const hidePII = (datum) => {
            return {id: datum.id};
        };
        function maskPII(datum) {
            return {
                id: datum.id,
                name: datum.name.replace(/./g, '*'),
                age: string(datum.age).replace(/./g, '*'),
            };
        }
        
        export const sanitizedData = hidePII(objectData);
        """
    ).encode()
    file_name = f'src/constants.{file_type}'
    objects = parse_objects(
        Path(file_name),
        source
    )
    assert len(objects) == 11
    file_o, string_o, number_o, boolean_o, null_o, undefined_o = objects[:6]
    object_o, array_o, hide_pii_o, mask_pii_o, sanitized_o = objects[6:]
    assert file_o.name == file_name
    assert file_o.kind == 'file'
    assert string_o.name == 'stringData'
    assert string_o.kind == 'definition.constant'
    assert number_o.name == 'numberData'
    assert number_o.kind == 'definition.constant'
    assert boolean_o.name == 'booleanData'
    assert boolean_o.kind == 'definition.constant'
    assert null_o.name == 'nullData'
    assert null_o.kind == 'definition.constant'
    assert undefined_o.name == 'undefinedData'
    assert undefined_o.kind == 'definition.constant'
    assert object_o.name == 'objectData'
    assert object_o.kind == 'definition.constant'
    assert array_o.name == 'arrayData'
    assert array_o.kind == 'definition.constant'
    assert hide_pii_o.name == 'hidePII'
    assert hide_pii_o.kind == 'definition.function'
    assert mask_pii_o.name == 'maskPII'
    assert mask_pii_o.kind == 'definition.function'
    assert sanitized_o.name == 'sanitizedData'
    assert sanitized_o.kind == 'definition.constant'


def test_parse_cxx_header_file():
    file_name = 'src/shapes.h'
    source = textwrap.dedent(
        """
        #ifndef SHAPES_H
        #define SHAPES_H
        
        #include <iostream>
        
        struct Point {
            double x;
            double y;
        };
        
        class Shape {
        public:
            Shape();
            virtual ~Shape();
            virtual double area() = 0;
        };
        
        class Circle : public Shape {
        public:
            Circle(double radius);
            double area() override;
        private:
            double radius_;
        };
        
        class Rectangle : public Shape {
        public:
            Rectangle(double width, double height);
            double area() override;
        private:
            double width_;
            double height_;
        };
        
        #endif
        """
    ).encode()
    source_lines = source.splitlines()
    objects = parse_objects(
        Path(file_name),
        source
    )
    assert len(objects) == 8

    file, point, shape, shape_area, circle, circle_area, rectangle, rectangle_area = objects

    assert file.name == file_name
    assert file.kind == 'file'
    assert file.language == 'cpp'
    assert file.context_before == []
    assert file.context_after == []

    ifndef_start, ifndef_end = source_lines.index(b'#ifndef SHAPES_H'), source_lines.index(b'#endif')

    assert point.name == 'Point'
    assert point.kind == 'definition.struct'
    assert point.context_before == [ifndef_start]
    assert point.context_after == [ifndef_end]

    assert shape.name == 'Shape'
    assert shape.kind == 'definition.class'
    assert shape.context_before == [ifndef_start]
    assert shape.context_after == [ifndef_end]

    shape_start = shape.coordinates[0][0]
    shape_end = shape.coordinates[1][0]

    assert shape_area.name == 'area'
    assert shape_area.kind == 'definition.method'
    assert shape_area.context_before == [ifndef_start, shape_start]
    assert shape_area.context_after == [ifndef_end, shape_end]

    assert circle.name == 'Circle'
    assert circle.kind == 'definition.class'
    assert circle.context_before == [ifndef_start]
    assert circle.context_after == [ifndef_end]

    circle_start = circle.coordinates[0][0]
    circle_end = circle.coordinates[1][0]

    assert circle_area.name == 'area'
    assert circle_area.kind == 'definition.method'
    assert circle_area.context_before == [ifndef_start, circle_start]
    assert circle_area.context_after == [ifndef_end, circle_end]

    assert rectangle.name == 'Rectangle'
    assert rectangle.kind == 'definition.class'
    assert rectangle.context_before == [ifndef_start]
    assert rectangle.context_after == [ifndef_end]

    rectangle_start = rectangle.coordinates[0][0]
    rectangle_end = rectangle.coordinates[1][0]

    assert rectangle_area.name == 'area'
    assert rectangle_area.kind == 'definition.method'
    assert rectangle_area.context_before == [ifndef_start, rectangle_start]
    assert rectangle_area.context_after == [ifndef_end, rectangle_end]
