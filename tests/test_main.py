from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from codebased.main import find_root_git_repository, VERSION


# Algebraically:
#  File = tuple[Path, bytes]
#  Directory = tuple[Path, tuple[DirEntry]]
#  DirEntry = Union[File | Directory]

def create_tree(
        dir_entry,
        relative_to: Path
):
    path, contents = dir_entry
    absolute_path = relative_to / path
    if isinstance(
            contents,
            bytes
    ):
        absolute_path.write_bytes(
            contents
        )
    else:
        absolute_path.mkdir(
            exist_ok=True
        )
        for entry in contents:
            create_tree(
                entry,
                absolute_path
            )


class TestGitDetection(
    unittest.TestCase
):
    def test_in_a_regular_git_repository(
            self
    ):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(
                tempdir
            ).resolve()
            create_tree(
                (
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
                ),
                path
            )
            test_paths = [
                path,
                path / 'a-directory',
                path / 'a-directory' / 'code.py',
                path / '.git',
                path / 'README.md'
            ]
            for test_path in test_paths:
                with self.subTest(
                        test_path=test_path
                ):
                    result = find_root_git_repository(
                        test_path
                    )
                    self.assertEqual(
                        result,
                        path
                    )

    def test_in_a_git_repository_with_submodules(
            self
    ):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(
                tempdir
            ).resolve()
            create_tree(
                (
                    Path(
                        '.'
                    ),
                    (
                        (Path(
                            'README.md'
                        ), b'Hello, world!'),
                        (Path(
                            '.git'
                        ), ()),
                        (Path(
                            'submodule'
                        ), (
                             (Path(
                                 'README.md'
                             ), b'Hello, world!'),
                             (Path(
                                 'below-submodule'
                             ), (
                                  ((Path(
                                      'code.py', ), b'print("Hello, world!")'),)
                              )),
                             # Git submodules contain a .git **FILE**.
                             # This points to a subdirectory of the parent .git/modules directory.
                             (Path(
                                 '.git'
                             ), b'')
                         ))
                    )
                ),
                path
            )
            test_paths = [
                path,
                path / 'submodule',
                path / 'submodule' / 'below-submodule',
                path / 'submodule' / 'below-submodule' / 'code.py',
                path / 'README.md'
            ]
            for test_path in test_paths:
                with self.subTest(
                        test_path=test_path
                ):
                    result = find_root_git_repository(
                        test_path
                    )
                    self.assertEqual(
                        result,
                        path
                    )

    def test_run_outside_a_git_repository(
            self
    ):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(
                tempdir
            ).resolve()
            create_tree(
                (
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
                    )
                ),
                path
            )
            test_paths = [
                path / 'a-directory',
                path / 'a-directory' / 'code.py',
                path / 'README.md',
                path
            ]
            for test_path in test_paths:
                with self.subTest(
                        test_path=test_path
                ):
                    result = find_root_git_repository(
                        test_path
                    )
                    self.assertIs(
                        result,
                        None
                    )
            result2 = find_root_git_repository(
                path / 'a-directory' / 'code.py'
            )
            self.assertIs(
                result2,
                None
            )
            result3 = find_root_git_repository(
                path / 'README.md'
            )
            self.assertIs(
                result3,
                None
            )
            result4 = find_root_git_repository(
                path
            )
            self.assertIs(
                result4,
                None
            )

    def test_run_at_root(
            self
    ):
        # You never know what's going to happen on someone's laptop.
        # Someone's probably managing their entire filesystem using Git.
        # That's how you *win* this test, instead of merely passing it.
        root = Path(
            '/'
        )
        if (root / '.git').is_dir():
            assert find_root_git_repository(
                root
            ) == root
        else:
            assert find_root_git_repository(
                root
            ) is None


def check_codebased_cli(
        *,
        cwd: Path,
        exit_code: int,
        stderr: bytes | re.Pattern,
        stdout: bytes | re.Pattern,
        args: list[str]
):
    proc = subprocess.run(
        ['python', '-m', 'codebased.main', *args],
        cwd=cwd.resolve(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE
    )
    assert proc.returncode == exit_code, f'{proc.returncode} != {exit_code}'
    if isinstance(
            stdout,
            bytes
    ):
        assert proc.stdout == stdout, f'{proc.stdout} != {stdout}'
    else:
        assert stdout.match(
            proc.stdout
        ), proc.stdout
    if isinstance(
            stderr,
            bytes
    ):
        assert proc.stderr == stderr, f'{proc.stderr} != {stderr}'
    else:
        assert stderr.match(
            proc.stderr
        ), proc.stderr


class TestCli(
    unittest.TestCase
):
    def test_run_outside_a_git_repository(
            self
    ):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(
                tempdir
            ).resolve()
            create_tree(
                (
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
                        # (Path('.git'), ()),
                    )
                ),
                path
            )
            exit_code = 1
            stdout = b''
            stderr = b'Codebased must be run within a Git repository.\n'
            check_codebased_cli(
                cwd=path,
                exit_code=exit_code,
                stderr=stderr,
                stdout=stdout,
                args=['search']
            )

    def test_run_inside_a_git_repository(
            self
    ):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(
                tempdir
            ).resolve()
            create_tree(
                (
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
                ),
                path
            )
            exit_code = 0
            stdout = b'Found Git repository ' + str(
                path
            ).encode(
                'utf-8'
            ) + b'\n'
            stderr = b''
            check_codebased_cli(
                cwd=path,
                exit_code=exit_code,
                stderr=stderr,
                stdout=stdout,
                args=['search']
            )
            check_codebased_cli(
                cwd=path / 'a-directory',
                exit_code=exit_code,
                stderr=stderr,
                stdout=stdout,
                args=['search']
            )

    def test_version(
            self
    ):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(
                tempdir
            )
            exit_code = 0
            stdout = f'Codebased {VERSION}\n'.encode(
                'utf-8'
            )
            stderr = b''
            check_codebased_cli(
                cwd=path,
                exit_code=exit_code,
                stderr=stderr,
                stdout=stdout,
                args=['--version']
            )

    def test_help(
            self
    ):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(
                tempdir
            )
            exit_code = 0
            stderr = b''
            check_codebased_cli(
                cwd=path,
                exit_code=exit_code,
                stderr=stderr,
                stdout=re.compile(re.escape(b'usage: main.py [-h] [--version] {search} ...'), re.ASCII),
                args=['--help']
            )
            # Note: We're not checking the exact help output as it might change and be system-dependent
