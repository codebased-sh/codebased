from __future__ import annotations

import dataclasses
import hashlib
import logging
import os
import sqlite3
import textwrap
import typing as T
from datetime import datetime
from pathlib import Path

import faiss
import toml
from openai import OpenAI

from codebased.exceptions import NoApplicationDirectoryException, NotFoundException
from codebased.filesystem import get_git_files, find_git_repositories
from codebased.models import FileRevision, PersistentObject, Embedding
from codebased.parser import parse_objects
from codebased.storage import persist_object, fetch_objects, DatabaseMigrations, persist_file_revision, fetch_embedding

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
class EmbeddingsConfig:
    model: str = 'text-embedding-3-large'
    dimensions: int = 1536


@dataclasses.dataclass
class Config:
    """
    These are defaults etc. that are used across various commands.
    """
    embeddings: EmbeddingsConfig

    @classmethod
    def load_file(cls, path: Path):
        with open(path) as f:
            config = toml.load(f)
            try:
                embeddings_config = EmbeddingsConfig(**config.pop('embeddings'))
            except KeyError:
                embeddings_config = EmbeddingsConfig()
            return cls(**config, embeddings=embeddings_config)  # type: ignore


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

    def get_openai_client(self) -> OpenAI:
        return OpenAI(api_key=self.secrets.OPENAI_API_KEY)

    @classmethod
    def from_settings(cls, settings: Settings):
        return cls(
            secrets=Secrets.load_file(settings.secrets_file),
            config=Config.load_file(settings.config_file),
            db=get_db(settings.database_file),
            application_directory=settings.application_directory,
            indexes_directory=settings.indexes_directory,
        )


def get_db(database_file: Path) -> sqlite3.Connection:
    db = sqlite3.connect(database_file)
    db.row_factory = sqlite3.Row
    return db


class Main:
    def __init__(self, context: Context):
        self.context = context

    def gather_objects(self, root: Path) -> T.Iterable[PersistentObject]:
        for repo in find_git_repositories(root):
            for path in get_git_files(repo):
                content = get_file_content

                content_hash = hashlib.sha1(content).hexdigest()
                size = path.stat().st_size
                last_modified = datetime.fromtimestamp(path.stat().st_mtime)
                file_revision = FileRevision(path, content_hash, size, last_modified)
                try:
                    self.context.db.execute("begin;")
                    persistent_file_revision = persist_file_revision(self.context.db, file_revision)
                    objects = parse_objects(persistent_file_revision)
                    tmp = []
                    for obj in objects:
                        persistent_object = persist_object(self.context.db, obj)
                        tmp.append(persistent_object)
                    self.context.db.execute("commit;")
                    yield from tmp
                except sqlite3.IntegrityError:
                    yield from fetch_objects(self.context.db, file_revision)

    def gather_embeddings(self, root_path: Path) -> T.Iterable[Embedding]:
        q = []

        def enqueue(o: PersistentObject):
            q.append(o)

        def should_drain() -> bool:
            return len(q) > 100

        def drain() -> T.Iterable[Embedding]:
            nonlocal q
            for o in q:
                yield o
            q.clear()

        for obj in self.gather_objects(root_path):
            # Don't create an embedding if one already exists.
            # It might be nice to store all the embeddings for a file atomically.
            try:
                embedding = fetch_embedding(self.context.db, obj.id)
                yield embedding
            except NotFoundException:
                enqueue(obj)
                if should_drain():
                    yield from drain()
        else:
            yield from drain()

    def create_index(self, root: Path) -> faiss.Index:
        pass


def greet():
    with open(PACKAGE_DIR / "GREETING.txt") as f:
        print(f.read())


def main(root: Path):
    greet()
    settings = Settings.default()
    settings.ensure_ok()
    context = Context.from_settings(settings)
    migrations = DatabaseMigrations(context.db, PACKAGE_DIR / "migrations")
    migrations.initialize()
    migrations.migrate()
    m = Main(context)
    m.gather_objects(root)
    # Make an index for each repository.
