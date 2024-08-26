from __future__ import annotations

import argparse
import dataclasses
import hashlib
import os
import re
import sqlite3
import subprocess
import textwrap
from datetime import datetime
from pathlib import Path
import logging
import typing as T

import toml

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_SECRETS_FILE = textwrap.dedent("""
# Fill in your OpenAI API key, used for embeddings, etc. and never leaves your computer.
OPENAI_API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
""")

PACKAGE_DIR: Path = Path(__file__).parent


@dataclasses.dataclass
class Secrets:
    OPENAI_API_KEY: str = dataclasses.field(default_factory=lambda: os.environ.get("OPENAI_API_KEY"))

    def __post_init__(self):
        if not self.OPENAI_API_KEY:
            raise ValueError("Codebased requires an OpenAI API key for now. Ask Max if you'd like one to test with.")

    @classmethod
    def load_file(cls, path: Path):
        with open(path) as f:
            secrets = toml.load(f)
            return cls(**secrets)


@dataclasses.dataclass
class Config:
    """
    These are defaults etc. that are used across various commands.
    """

    @classmethod
    def load_file(cls, path: Path):
        with open(path) as f:
            config = toml.load(f)
            return cls(**config)  # type: ignore


@dataclasses.dataclass
class Settings:
    """
    These are long-lived across various commands, they're settings about settings.
    """
    application_directory: Path
    config_file: Path
    secrets_file: Path
    database_file: Path
    indexes_directory: Path

    @classmethod
    def from_application_directory(cls, directory: Path):
        return cls(
            application_directory=directory,
            config_file=directory / "config.toml",
            secrets_file=directory / "secrets.toml",
            database_file=directory / "codebased.db",
            indexes_directory=directory / "indexes",
        )

    @classmethod
    def default(cls):
        return cls.from_application_directory(Path.home() / ".codebased")

    def verify(self):
        if not self.application_directory.exists():
            raise NoApplicationDirectoryException(self.application_directory)

    def create_defaults(self):
        self.application_directory.mkdir(parents=True, exist_ok=True)
        self.config_file.touch()
        self.secrets_file.write_text(DEFAULT_SECRETS_FILE)
        self.database_file.touch()
        self.indexes_directory.mkdir(parents=True, exist_ok=True)

    def ensure_ok(self):
        try:
            self.verify()
        except NoApplicationDirectoryException as e:
            print(f"Looks like you're new here, setting up {self.application_directory}.")
            self.create_defaults()


@dataclasses.dataclass
class Context:
    secrets: Secrets
    config: Config
    db: sqlite3.Connection
    application_directory: Path
    indexes_directory: Path

    @classmethod
    def from_settings(cls, settings: Settings):
        return cls(
            secrets=Secrets.load_file(settings.secrets_file),
            config=Config.load_file(settings.config_file),
            db=sqlite3.connect(settings.database_file),
            application_directory=settings.application_directory,
            indexes_directory=settings.indexes_directory,
        )


class CodebasedException(Exception):
    """
    Differentiate between business logic exceptions and Knightian exceptions.
    """
    pass


class NoApplicationDirectoryException(CodebasedException):
    """
    Raised when the application directory is not found.
    """

    def __init__(self, application_directory: Path):
        self.application_directory = application_directory
        super().__init__(f"The application directory {str(application_directory)} was not found.")


def get_git_files(path: Path) -> list[Path]:
    proc = subprocess.Popen(['git', 'ls-files', '-c'], stdout=subprocess.PIPE, cwd=path)
    relative_paths = proc.stdout.read().decode('utf-8').split('\n')
    absolute_paths = [path / relative_path for relative_path in relative_paths]
    return [path for path in absolute_paths if path.is_file()]


@dataclasses.dataclass
class FileRevision:
    path: Path
    hash: str
    size: int
    last_modified: datetime

    @classmethod
    def from_path(cls, path: Path):
        content_hash = hashlib.sha1(path.read_bytes()).hexdigest()
        size = path.stat().st_size
        last_modified = datetime.fromtimestamp(path.stat().st_mtime)
        file_revision = FileRevision(path, content_hash, size, last_modified)
        return file_revision


@dataclasses.dataclass
class PersistentFileRevision(FileRevision):
    id: int


Coordinates = T.Tuple[T.Tuple[int, int], T.Tuple[int, int]]


@dataclasses.dataclass
class Chunk:
    file: str
    name: str
    language: str
    context_before: list[int]
    context_after: list[int]
    kind: str
    # text: bytes  # This field is an excellent candidate for removal / using a memoryview.
    byte_range: T.Tuple[int, int]  # [start, end)
    coordinates: Coordinates

    def __len__(self):
        start, end = self.byte_range
        return end - start

    @property
    def line_length(self) -> int:
        return self.coordinates[1][0] - self.coordinates[0][0] + 1


@dataclasses.dataclass
class PersistentChunk(Chunk):
    id: int


class DatabaseMigrations:
    def __init__(self, db: sqlite3.Connection, migrations_directory: Path):
        self.db = db
        self.migrations_directory = migrations_directory

    def initialize(self):
        self.db.execute("CREATE TABLE IF NOT EXISTS migrations (version INTEGER PRIMARY KEY)")

    def get_current_version(self) -> int | None:
        cursor = self.db.execute("SELECT version FROM migrations ORDER BY version DESC LIMIT 1")
        version_row = cursor.fetchone()
        return version_row[0] if version_row else None

    def add_version(self, version: int):
        self.db.execute("INSERT INTO migrations (version) VALUES (?)", (version,))

    def migrate(self):
        migration_file_names = [f for f in os.listdir(self.migrations_directory) if f.endswith(".sql")]
        migration_paths = [self.migrations_directory / file for file in migration_file_names]

        def get_migration_version(path: Path) -> int:
            return int(re.match(r'(\d+)', path.name).group(1))

        migration_paths.sort(key=get_migration_version)
        current_version = self.get_current_version()
        for migration_path in migration_paths:
            version = get_migration_version(migration_path)
            if current_version is not None and current_version >= version:
                logger.debug(f"Skipping migration {migration_path}")
                continue
            logger.debug(f"Running migration {migration_path}")
            with open(migration_path) as f:
                migration = f.read()
            self.db.execute(migration)
            self.add_version(version)
            self.db.commit()


def is_git_repository(path: Path) -> bool:
    return (path / '.git').exists()


def find_parent_git_repositories(path: Path) -> list[Path]:
    parent = path.parent
    repos = []
    while parent != Path('/'):
        if is_git_repository(parent):
            repos.append(parent)
        parent = parent.parent
    return repos


def find_child_git_repositories(root: Path) -> list[Path]:
    git_repos = []
    for dirpath, dirnames, _ in os.walk(root, topdown=True):
        if '.git' in dirnames:
            git_repos.append(Path(dirpath))
            dirnames.clear()  # Stop recursing once we find a Git repository
        else:
            # Optional: Remove hidden directories to avoid unnecessary searches
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
    return git_repos


def find_git_repositories(root: Path) -> list[Path]:
    parents = find_parent_git_repositories(root)
    children = find_child_git_repositories(root)
    return parents + children


def greet():
    with open(PACKAGE_DIR / "GREETING.txt") as f:
        print(f.read())


def gather_file_revisions(root: Path) -> T.Iterable[FileRevision]:
    for repo in find_git_repositories(root):
        for path in get_git_files(repo):
            file_revision = FileRevision.from_path(path)
            yield file_revision


def gather_and_persist_file_revisions(root: Path, context: Context) -> list[PersistentFileRevision]:
    for file_revision in gather_file_revisions(root):
        persistent_revision = persist_file_revision(file_revision, context)
        yield persistent_revision


def persist_file_revision(file_revision: FileRevision, context: Context) -> PersistentFileRevision:
    cursor = context.db.execute(
        """
        INSERT INTO file_revisions
         (path, hash, size, last_modified)
          VALUES (?, ?, ?, ?)
          ON CONFLICT (path, hash) DO UPDATE SET size = excluded.size, last_modified = excluded.last_modified
           RETURNING id
        """,
        (file_revision.path, file_revision.hash, file_revision.size, file_revision.last_modified),
    )
    context.db.commit()
    persistent_revision = PersistentFileRevision(**dataclasses.asdict(file_revision), id=cursor.lastrowid)
    return persistent_revision


def main(root: Path):
    greet()
    settings = Settings.default()
    settings.ensure_ok()
    context = Context.from_settings(settings)
    migrations = DatabaseMigrations(context.db, PACKAGE_DIR / "migrations")
    migrations.initialize()
    migrations.migrate()
    # Make an index for each repository.


def cli():
    parser = argparse.ArgumentParser(description="Codebased")
    parser.add_argument(
        "--root",
        type=Path,
        help="The directory to index.",
        default=os.getcwd(),
        required=False,
    )
    args = parser.parse_args()
    main(args.root)
