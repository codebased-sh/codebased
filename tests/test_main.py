from __future__ import annotations

import asyncio
import dataclasses
import os
import re
import sqlite3
import subprocess
import tempfile
import typing as T
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Union

import faiss

from codebased.index import find_root_git_repository, Flags, Config, Dependencies, index_paths
from codebased.main import VERSION
from codebased.settings import Settings

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


@dataclasses.dataclass
class CliTestCase:
    tree: T.Any
    objects: int
    files: int

    def create(self, path: Path):
        create_tree(self.tree, path)


SIMPLE_REPO_TEST_CASE = CliTestCase(tree=SIMPLE_REPO_TREE, objects=2, files=2)


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


def check_codebased_cli(
        *,
        cwd: Path,
        exit_code: int,
        stderr: StreamAssertion,
        stdout: StreamAssertion,
        args: list[str]
):
    proc = subprocess.run(
        ['python', '-m', 'codebased.main', *args],
        cwd=cwd.resolve(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
        # Pass through environment variables.
        env=os.environ,
    )
    if proc.returncode != 0:
        print(f'stdout: {proc.stdout.decode("utf-8")}')
        print(f'stderr: {proc.stderr.decode("utf-8")}')
    assert proc.returncode == exit_code, f'{proc.returncode} != {exit_code}, stdout: {proc.stdout}, stderr: {proc.stderr}'
    if isinstance(stdout, bytes):
        assert proc.stdout == stdout, f'{proc.stdout} != {stdout}'
    elif isinstance(stdout, re.Pattern):
        assert stdout.match(proc.stdout), proc.stdout
    if isinstance(stderr, bytes):
        assert proc.stderr == stderr, f'{proc.stderr} != {stderr}'
    elif isinstance(stderr, re.Pattern):
        assert stderr.match(proc.stderr), proc.stderr


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
            stderr = b""
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
            stderr = b""
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
                args=["--help"]
            )
            check_codebased_cli(
                cwd=path,
                exit_code=exit_code,
                stderr=stderr,
                stdout=re.compile(rb".*search.*QUERY.*", re.DOTALL | re.ASCII),
                args=["search", "--help"]
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
            stderr = b""

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
            stderr = b""
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

    def test_rebuild_faiss_index(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir).resolve()
            create_tree(SIMPLE_REPO_TREE, path)
            exit_code = 0
            stdout = re.compile(b"Found Git repository " + str(path).encode("utf-8") + b".*")
            stderr = b""
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
            stderr = b""
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
                    stderr=stderr,
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
            stderr = b""
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
            stderr = b""
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

    def test_hybrid_search(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir).resolve()
            create_tree(SIMPLE_REPO_TREE, path)
            exit_code = 0
            stdout = None
            stderr = b""
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


class AppTestBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.TemporaryDirectory(delete=False)
        path = Path(self.tempdir.name).resolve()
        self.flags = Flags(
            directory=path,
            rebuild_faiss_index=False,
            cached_only=False,
            query="Hello world",
            background=False,
            stats=False,
            semantic=True,
            full_text_search=True,
            top_k=10
        )
        self.settings = Settings()
        self.config = Config(flags=self.flags)
        self.dependencies = Dependencies(
            config=self.config,
            settings=self.settings
        )
        index_paths(
            self.dependencies,
            self.config,
            [self.config.root],
            total=True
        )

    def tearDown(self):
        super().tearDown()
        self.tempdir.cleanup()
        self.dependencies.db.close()
