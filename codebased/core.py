from __future__ import annotations

import dataclasses
import getpass
import logging
import os
import sqlite3
import textwrap
from functools import cached_property
from pathlib import Path

import tiktoken
import toml
from openai import OpenAI
from tiktoken import Encoding

from codebased.constants import DEFAULT_MODEL, DEFAULT_MODEL_DIMENSIONS
from codebased.exceptions import NoApplicationDirectoryException

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

    @classmethod
    def from_prompt(cls):
        env = os.getenv("OPENAI_API_KEY")
        if env:
            openai_api_key = getpass.getpass(f"What is your OpenAI API key? [OPENAI_API_KEY={env[:7]}]: ")
            if not openai_api_key:
                openai_api_key = env
        else:
            openai_api_key = getpass.getpass("What is your OpenAI API key? ")
        return cls(OPENAI_API_KEY=openai_api_key)


@dataclasses.dataclass
class EmbeddingsConfig:
    model: str = 'text-embedding-3-large'
    dimensions: int = 1536
    similarity_threshold: float = 0.8


@dataclasses.dataclass
class Config:
    """
    These are defaults etc. that are used across various commands.
    """
    embeddings: EmbeddingsConfig = EmbeddingsConfig()

    @classmethod
    def load_file(cls, path: Path):
        with open(path) as f:
            config = toml.load(f)
            try:
                embeddings_config = EmbeddingsConfig(**config.pop('embeddings'))
            except KeyError:
                embeddings_config = EmbeddingsConfig()
            return cls(**config, embeddings=embeddings_config)  # type: ignore

    @classmethod
    def from_prompt(cls):
        embedding_model = cls.prompt_default_model()
        dimensions = cls.prompt_default_dimensions()
        return cls(
            embeddings=EmbeddingsConfig(
                model=embedding_model,
                dimensions=dimensions
            )
        )

    @classmethod
    def prompt_default_model(cls) -> str:
        embedding_model = input("What model do you want to use for embeddings? [text-embedding-ada-002]: ")
        return embedding_model if embedding_model else DEFAULT_MODEL

    @classmethod
    def prompt_default_dimensions(cls) -> int:
        text = input("What dimensions do you want to use for embeddings? [1536]: ")
        dimensions = int(text) if text else DEFAULT_MODEL_DIMENSIONS
        return dimensions

    def save(self, path: Path):
        with open(path, 'w') as f:
            toml.dump(dataclasses.asdict(self), f)


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
        if not all([self.application_directory.exists(), self.config_file.exists(), self.secrets_file.exists()]):
            raise NoApplicationDirectoryException(self.application_directory)

    def create_defaults(self):
        self.application_directory.mkdir(parents=True, exist_ok=True)
        self.config_file.touch()
        Config.from_prompt().save(self.config_file)
        self.secrets_file.touch()
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

    @cached_property
    def embedding_model_encoding(self) -> Encoding:
        return tiktoken.encoding_for_model(self.config.embeddings.model)

    @cached_property
    def openai_client(self) -> OpenAI:
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


def greet():
    with open(PACKAGE_DIR / "GREETING.txt") as f:
        print(f.read())
