from __future__ import annotations

import dataclasses
import getpass
import logging
import os
import sqlite3
import sys
from pathlib import Path

import toml

from codebased.constants import DEFAULT_MODEL, DEFAULT_MODEL_DIMENSIONS, DEFAULT_EDITOR
from codebased.exceptions import MissingConfigFileException
from codebased.models import EDITOR

logger = logging.getLogger(__name__)
PACKAGE_DIR: Path = Path(__file__).parent
CONFIG_DIRECTORY = Path.home() / ".codebased"
CONFIG_FILE = CONFIG_DIRECTORY / "config.toml"


@dataclasses.dataclass
class EmbeddingsConfig:
    model: str = DEFAULT_MODEL
    dimensions: int = DEFAULT_MODEL_DIMENSIONS


@dataclasses.dataclass
class Settings:
    """
    Combined class for Settings, Config, and Secrets
    """
    embeddings: EmbeddingsConfig = dataclasses.field(default_factory=EmbeddingsConfig)
    editor: EDITOR = DEFAULT_EDITOR
    OPENAI_API_KEY: str = dataclasses.field(default_factory=lambda: os.environ.get("OPENAI_API_KEY"))

    @classmethod
    def always(cls) -> "Settings":
        try:
            cls.verify()
        except MissingConfigFileException:
            cls.create()
        return cls.load_file(CONFIG_FILE)

    def __post_init__(self):
        if not self.OPENAI_API_KEY:
            raise ValueError("Codebased requires an OpenAI API key for now."
                             "Join the Discord to access to a key for testing.")

    @staticmethod
    def verify():
        if not CONFIG_FILE.exists():
            raise MissingConfigFileException()

    @classmethod
    def create(cls):
        greet()
        CONFIG_DIRECTORY.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.touch()
        # TODO: Windows?
        if sys.stdin.isatty():
            effective_defaults = cls.from_prompt()
        else:
            effective_defaults = Settings()
        effective_defaults.save(CONFIG_FILE)

    def ensure_ok(self):
        try:
            self.verify()
        except MissingConfigFileException:
            if sys.stdin.isatty():
                print(f"Looks like you're new here, setting up {str(CONFIG_FILE)}.")
            self.create()

    @classmethod
    def load_file(cls, path: Path):
        with open(path) as f:
            data = toml.load(f)
            try:
                embeddings_config = EmbeddingsConfig(**data.pop('embeddings'))
            except KeyError:
                embeddings_config = EmbeddingsConfig()
            return cls(**data, embeddings=embeddings_config)

    @classmethod
    def from_prompt(cls):
        embedding_model = cls.prompt_default_model()
        dimensions = cls.prompt_default_dimensions()
        editor = cls.prompt_default_editor()
        env = os.getenv("OPENAI_API_KEY")
        if env:
            openai_api_key = getpass.getpass(f"What is your OpenAI API key? [OPENAI_API_KEY={env[:7]}]: ")
            if not openai_api_key:
                openai_api_key = env
        else:
            openai_api_key = getpass.getpass("What is your OpenAI API key? ")
        return cls(
            embeddings=EmbeddingsConfig(
                model=embedding_model,
                dimensions=dimensions
            ),
            editor=editor,
            OPENAI_API_KEY=openai_api_key
        )

    @classmethod
    def prompt_default_model(cls) -> str:
        embedding_model = input(f"What model do you want to use for embeddings? [{DEFAULT_MODEL}]: ")
        return embedding_model if embedding_model else DEFAULT_MODEL

    @classmethod
    def prompt_default_dimensions(cls) -> int:
        text = input(f"What dimensions do you want to use for embeddings? [{DEFAULT_MODEL_DIMENSIONS}]: ")
        dimensions = int(text) if text else DEFAULT_MODEL_DIMENSIONS
        return dimensions

    @classmethod
    def prompt_default_editor(cls):
        return input(f"What editor do you want to use? (vi|idea|code) [{DEFAULT_EDITOR}]: ") or DEFAULT_EDITOR

    def save(self, path: Path):
        with open(path, 'w') as f:
            toml.dump(dataclasses.asdict(self), f)


def get_db(database_file: Path) -> sqlite3.Connection:
    db = sqlite3.connect(database_file, check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


def greet():
    with open(PACKAGE_DIR / "GREETING.txt") as f:
        print(f.read())
