from __future__ import annotations
import base64
import dataclasses
import getpass
import logging
import os
import sqlite3
from pathlib import Path

import toml

from codebased.constants import DEFAULT_MODEL, DEFAULT_MODEL_DIMENSIONS, DEFAULT_EDITOR
from codebased.exceptions import NoApplicationDirectoryException
from codebased.models import EDITOR

logger = logging.getLogger(__name__)
PACKAGE_DIR: Path = Path(__file__).parent


@dataclasses.dataclass
class Secrets:
    OPENAI_API_KEY: str = dataclasses.field(default_factory=lambda: os.environ.get("OPENAI_API_KEY"))

    def __post_init__(self):
        if not self.OPENAI_API_KEY:
            raise ValueError("Codebased requires an OpenAI API key for now. Ask Max if you'd like one to test with.")

    @classmethod
    def magic(cls):
        # Hardcode API key, but it has fairly aggressive rate limits and limits on the models used as well.
        bb = b'YzJzdGNISnZhaTB6ZW5CNllrbGxXRlZ6WjJVNFdGTm5iRko0UkZWYVRrOVJZMkpWUVVkMmMzVnVTWE5oTWtocVdtcDNRMUI1TW1sTmRWOVFWakZMTXpsbFJFcDRhR3haZFU1algydENRWFZVZUZRelFteGlhMFpLZURnek9FUXRaMEU0WldWTGNIZzVlREl4WlZCMU5tdEZNVW96U1hSM2NEbExTbEEyWkZKMWJIVlNTMXBVUm0xc05HZFpiVFZoUmt4bmIycFJTV0pzZDFwMFNubHhjRWxmV1VFPQ=='
        return cls(OPENAI_API_KEY=base64.b64decode(base64.b64decode(bb)).decode('utf-8'))

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

    def save(self, secrets_file: Path):
        with open(secrets_file, 'w') as f:
            toml.dump(dataclasses.asdict(self), f)


@dataclasses.dataclass(frozen=True)
class EmbeddingsConfig:
    model: str = DEFAULT_MODEL
    dimensions: int = DEFAULT_MODEL_DIMENSIONS


@dataclasses.dataclass
class Config:
    """
    These are defaults etc. that are used across various commands.
    """
    embeddings: EmbeddingsConfig = EmbeddingsConfig()
    editor: EDITOR = DEFAULT_EDITOR

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
            ),
            editor=cls.prompt_default_editor()
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

    def save(self, path: Path):
        with open(path, 'w') as f:
            toml.dump(dataclasses.asdict(self), f)

    @classmethod
    def prompt_default_editor(cls):
        return input(f"What editor do you want to use? (vi|idea|code) [{DEFAULT_EDITOR}]: ") or DEFAULT_EDITOR


@dataclasses.dataclass
class Settings:
    """
    These are long-lived across various commands, they're settings about settings.
    """
    config_directory: Path
    config_file: Path

    @classmethod
    def from_config_directory(cls, directory: Path):
        return cls(
            config_directory=directory,
            config_file=directory / "config.toml",
        )

    @classmethod
    def default(cls):
        return cls.from_config_directory(Path.home() / ".codebased")

    def verify(self):
        if not all([self.config_directory.exists(), self.config_file.exists()]):
            raise NoApplicationDirectoryException(self.config_directory)

    def create_defaults(self):
        greet()
        self.config_directory.mkdir(parents=True, exist_ok=True)
        self.config_file.touch()
        Config.from_prompt().save(self.config_file)
        Secrets.magic().save(self.config_file)
        # Secrets.from_prompt().save(self.secrets_file)

    def ensure_ok(self):
        try:
            self.verify()
        except NoApplicationDirectoryException as e:
            print(f"Looks like you're new here, setting up {self.config_directory}.")
            self.create_defaults()


def get_db(database_file: Path) -> sqlite3.Connection:
    db = sqlite3.connect(database_file, check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


def greet():
    with open(PACKAGE_DIR / "GREETING.txt") as f:
        print(f.read())
