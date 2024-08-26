from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import textwrap
import typing as T
from datetime import datetime
from pathlib import Path

import toml
import tree_sitter
import tree_sitter_c
import tree_sitter_c_sharp
import tree_sitter_cpp
import tree_sitter_go
import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_php
import tree_sitter_python
import tree_sitter_ruby
import tree_sitter_rust
import tree_sitter_typescript
from openai import OpenAI

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
class Object:
    file_revision_id: int
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


class LanguageImpl:
    def __init__(
            self,
            name: str,
            parser: tree_sitter.Parser,
            language: tree_sitter.Language,
            file_types: list[str],
            tags: tree_sitter.Query,
    ):
        self.name = name
        self.parser = parser
        self.language = language
        self.file_types = file_types
        self.tags = tags

    @classmethod
    def from_language(cls, language: tree_sitter.Language, *, tags: str, file_types: list[str], name: str):
        parser = tree_sitter.Parser(language)
        return cls(
            name=name,
            parser=parser,
            language=language,
            file_types=file_types,
            tags=language.query(tags)
        )


PY_IMPL = LanguageImpl.from_language(
    tree_sitter.Language(tree_sitter_python.language()),
    tags="""
        (module (expression_statement (assignment left: (identifier) @name) @definition.constant))
        
        (class_definition
          name: (identifier) @name) @definition.class
        
        (function_definition
          name: (identifier) @name) @definition.function
    """,
    file_types=['py'],
    name='python'
)
RUST_IMPL = LanguageImpl.from_language(
    tree_sitter.Language(tree_sitter_rust.language()),
    tags="""
    ; ADT definitions

(struct_item
    name: (type_identifier) @name) @definition.class

(enum_item
    name: (type_identifier) @name) @definition.class

(union_item
    name: (type_identifier) @name) @definition.class

; type aliases

(type_item
    name: (type_identifier) @name) @definition.class

; method definitions

(function_item
  name: (identifier) @name) @definition.function

; trait definitions
(trait_item
    name: (type_identifier) @name) @definition.interface

; module definitions
(mod_item
    name: (identifier) @name) @definition.module

; macro definitions

(macro_definition
    name: (identifier) @name) @definition.macro

; implementations

(impl_item
    trait: (type_identifier) @name) @definition.trait.impl

(impl_item
    type: (type_identifier) @name
    !trait) @definition.struct.impl

    """,
    file_types=['rs'],
    name='rust'
)
C_IMPL = LanguageImpl.from_language(
    tree_sitter.Language(tree_sitter_c.language()),
    tags="""
        (struct_specifier name: (type_identifier) @name body:(_)) @definition.class
        
        (declaration type: (union_specifier name: (type_identifier) @name)) @definition.class
        
        (function_definition declarator: (function_declarator declarator: (identifier) @name)) @definition.function
        
        (type_definition declarator: (type_identifier) @name) @definition.type
        
        (enum_specifier name: (type_identifier) @name) @definition.type
    """,
    file_types=['c', 'h'],
    name='c'
)
CPP_IMPL = LanguageImpl.from_language(
    tree_sitter.Language(tree_sitter_cpp.language()),
    tags="""
       (struct_specifier . name: (type_identifier) @name body:(_)) @definition.class

        (declaration type: (union_specifier name: (type_identifier) @name)) @definition.class
        
        (function_definition declarator: (function_declarator declarator: (identifier) @name)) @definition.function

        (field_declaration (function_declarator declarator: (field_identifier) @name)) @definition.function

        ; removed the local scope from the following line after namespace_identifier
        (function_definition (function_declarator declarator: (qualified_identifier scope: (namespace_identifier) name: (identifier) @name))) @definition.method

        (type_definition . declarator: (type_identifier) @name) @definition.type

        (enum_specifier . name: (type_identifier) @name) @definition.type

        (class_specifier . name: (type_identifier) @name) @definition.class
    """,
    file_types=[
        "cc",
        "cpp",
        "cxx",
        "hpp",
        "hxx",
        "h"
    ],
    name='cpp'
)
C_SHARP_IMPL = LanguageImpl.from_language(
    tree_sitter.Language(tree_sitter_c_sharp.language()),
    tags="""
        (class_declaration name: (identifier) @name) @definition.class
        (interface_declaration name: (identifier) @name) @definition.interface
        (method_declaration name: (identifier) @name) @definition.method
        (namespace_declaration name: (identifier) @name) @definition.module
    """,
    file_types=['cs'],
    name='csharp'
)
GO_IMPL = LanguageImpl.from_language(
    tree_sitter.Language(tree_sitter_go.language()),
    # TODO: Need to add constants to this.
    tags="""
      (function_declaration
        name: (identifier) @name) @definition.function
      (method_declaration
        name: (field_identifier) @name) @definition.method
        (type_declaration (type_spec
          name: (type_identifier) @name)) @definition.type
    """,
    file_types=['go'],
    name='go'
)
JAVA_IMPL = LanguageImpl.from_language(
    tree_sitter.Language(tree_sitter_java.language()),
    tags="""
    (class_declaration
      name: (identifier) @name) @definition.class
    
    (method_declaration
      name: (identifier) @name) @definition.method
    
    (interface_declaration
      name: (identifier) @name) @definition.interface
    """,
    file_types=['java'],
    name='java'
)
_JAVASCRIPT_TAG_QUERY = """
(method_definition
  name: (property_identifier) @name) @definition.method

(class
  name: (_) @name) @definition.class

(class_declaration
  name: (_) @name) @definition.class

(function_expression
  name: (identifier) @name) @definition.function

(function_declaration
  name: (identifier) @name) @definition.function

(generator_function
  name: (identifier) @name) @definition.function

(generator_function_declaration
  name: (identifier) @name) @definition.function

(variable_declarator
    name: (identifier) @name
    value: [(arrow_function) (function_expression)]) @definition.function

(variable_declarator 
    name: (identifier) @name
    value: [(arrow_function) (function_expression)]) @definition.function

(assignment_expression
  left: [
    (identifier) @name
    (member_expression
      property: (property_identifier) @name)
  ]
  right: [(arrow_function) (function_expression)]) @definition.function

(pair
  key: (property_identifier) @name
  value: [(arrow_function) (function_expression)]) @definition.function

(export_statement 
  value: (assignment_expression 
    left: (identifier) @name 
    right: ([
      (number)
      (string)
      (identifier)
      (undefined)
      (null)
      (new_expression)
      (binary_expression)
      (call_expression)
    ]))) @definition.constant
    
    """
JAVASCRIPT_IMPL = LanguageImpl.from_language(
    tree_sitter.Language(tree_sitter_javascript.language()),
    tags=_JAVASCRIPT_TAG_QUERY,
    file_types=[
        "js",
        "mjs",
        "cjs",
        "jsx"
    ],
    name='javascript'
)
PHP_IMPL = LanguageImpl.from_language(
    tree_sitter.Language(tree_sitter_php.language_php()),
    tags="""
    (namespace_definition
  name: (namespace_name) @name) @definition.module

(interface_declaration
  name: (name) @name) @definition.interface

(trait_declaration
  name: (name) @name) @definition.interface

(class_declaration
  name: (name) @name) @definition.class

(class_interface_clause [(name) (qualified_name)] @name) @definition.class_interface_clause

(property_declaration
  (property_element (variable_name (name) @name))) @definition.field

(function_definition
  name: (name) @name) @definition.function

(method_declaration
  name: (name) @name) @definition.function
""",
    file_types=['php'],
    name='php'
)
RUBY_IMPL = LanguageImpl.from_language(
    tree_sitter.Language(tree_sitter_ruby.language()),
    tags="""
    ; Method definitions
    (method
      name: (_) @name) @definition.method
    (singleton_method
      name: (_) @name) @definition.method

(alias
  name: (_) @name) @definition.method

    (class
      name: [
        (constant) @name
        (scope_resolution
          name: (_) @name)
      ]) @definition.class
    (singleton_class
      value: [
        (constant) @name
        (scope_resolution
          name: (_) @name)
      ]) @definition.class

; Module definitions

  (module
    name: [
      (constant) @name
      (scope_resolution
        name: (_) @name)
    ]) @definition.module
    """,
    file_types=['rb'],
    name='ruby'
)
_TYPESCRIPT_ONLY_TAG_QUERY = """
    (function_signature
      name: (identifier) @name) @definition.function
    
    (method_signature
      name: (property_identifier) @name) @definition.method
    
    (abstract_method_signature
      name: (property_identifier) @name) @definition.method
    
    (abstract_class_declaration
      name: (type_identifier) @name) @definition.class
    
    (module
      name: (identifier) @name) @definition.module
    
    (interface_declaration
        name: (type_identifier) @name) @definition.interface
      """
_TYPESCRIPT_TAG_QUERY = '\n'.join([_TYPESCRIPT_ONLY_TAG_QUERY, _JAVASCRIPT_TAG_QUERY])
TYPESCRIPT_IMPL = LanguageImpl.from_language(
    tree_sitter.Language(tree_sitter_typescript.language_typescript()),
    tags=_TYPESCRIPT_TAG_QUERY,
    file_types=[
        'ts',
    ],
    name='typescript'
)
TSX_IMPL = LanguageImpl.from_language(
    tree_sitter.Language(tree_sitter_typescript.language_tsx()),
    tags=_TYPESCRIPT_TAG_QUERY,
    file_types=[
        'ts',
    ],
    name='tsx'
)
LANGUAGES = [
    PY_IMPL,
    RUST_IMPL,
    CPP_IMPL,
    C_IMPL,
    C_SHARP_IMPL,
    GO_IMPL,
    JAVA_IMPL,
    JAVASCRIPT_IMPL,
    PHP_IMPL,
    RUBY_IMPL,
    TYPESCRIPT_IMPL,
    TSX_IMPL
]


def get_node_coordinates(node: tree_sitter.Node) -> Coordinates:
    return node.start_point, node.end_point


def get_text_coordinates(text: bytes) -> Coordinates:
    lines = text.split(b'\n')
    return (0, 0), (len(lines) - 1, len(lines[-1]))


def get_all_parents(node: tree_sitter.Node) -> list[tree_sitter.Node]:
    parents = []
    parent = node.parent
    while parent:
        parents.append(parent)
        parent = parent.parent
    return parents


def get_context(node: tree_sitter.Node) -> tuple[list[int], list[int]]:
    parents = get_all_parents(node)
    before, after = [], []
    start_line, end_line = float('-inf'), float('inf')
    try:
        # The root node is typically like a file or something.
        parents.pop()
        while parents:
            parent = parents.pop()
            if not parent.children_by_field_name('name'):
                continue
            parent_start_line = parent.start_point.row
            assert parent_start_line >= start_line
            if start_line < parent_start_line < node.start_point.row:
                # first_line_text = parent.text[:parent.text.find(b'\n')]
                before.append(parent_start_line)
            parent_end_line = parent.end_point.row
            assert parent_end_line <= end_line
            if node.end_point.row < parent_end_line < end_line:
                # last_line_text = parent.text[parent.text.rfind(b'\n') + 1:]
                after.append(parent_end_line)
            start_line = parent_start_line
            end_line = parent_end_line
    except IndexError:
        pass
    return before, after


def get_objects(file_revision: PersistentFileRevision) -> list[Object]:
    file = file_revision.path
    file_type = file.suffix[1:]
    impl = None
    for language in LANGUAGES:
        if file_type in language.file_types:
            impl = language
            break
    with open(file, 'rb') as f:
        text = f.read()
    try:
        text.decode('utf-8')
    except UnicodeDecodeError:
        return []
    if impl is None:
        return [
            Object(
                file_revision_id=file_revision.id,
                name=str(file),
                language='text',
                kind='file',
                byte_range=(0, len(text)),
                coordinates=get_text_coordinates(text),
                context_before=[],
                context_after=[]
            )
        ]
    tree = impl.parser.parse(text)
    root_node = tree.root_node
    root_chunk = Object(
        file_revision_id=file_revision.id,
        name=str(file),
        kind='file',
        language=impl.name,
        byte_range=(0, len(text)),
        coordinates=get_text_coordinates(text),
        context_before=[],
        context_after=[]
    )
    chunks = [root_chunk]
    matches = impl.tags.matches(root_node)
    for _, captures in matches:
        name_node = captures.pop('name')
        for definition_kind, definition_node in captures.items():
            before, after = get_context(definition_node)
            chunks.append(
                Object(
                    file_revision_id=file_revision.id,
                    name=name_node.text.decode('utf-8'),
                    kind=definition_kind,
                    language=impl.name,
                    context_before=before,
                    context_after=after,
                    byte_range=definition_node.byte_range,
                    coordinates=get_node_coordinates(definition_node)
                )
            )
    return chunks


def persist_object(obj: Object, context: Context) -> PersistentObject:
    cursor = context.db.execute(
        """
        INSERT INTO object
         (file_revision_id, name, language, context_before, context_after, kind, byte_range, coordinates)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           RETURNING id
        """,
        (
            obj.file_revision_id,
            obj.name,
            obj.language,
            json.dumps(obj.context_before),
            json.dumps(obj.context_after),
            obj.kind,
            json.dumps(obj.byte_range),
            json.dumps(obj.coordinates)
        )
    )
    persistent_object = PersistentObject(**dataclasses.asdict(obj), id=cursor.lastrowid)
    return persistent_object


def get_equivalent_file_revision(file_revision: FileRevision, context: Context) -> PersistentFileRevision:
    cursor = context.db.execute(
        """
        SELECT 
            id,
            path,
            hash,
            size,
            last_modified
        FROM file_revision WHERE path = ? AND hash = ?;
        """,
        (file_revision.path, file_revision.hash)
    )
    row = cursor.fetchone()
    return PersistentFileRevision(
        id=row['id'],
        path=row['path'],
        hash=row['hash'],
        size=row['size'],
        last_modified=row['last_modified'],
    )


class Main:
    def __init__(self, context: Context):
        self.context = context

    def gather_objects(self, root: Path) -> T.Iterable[PersistentObject]:
        for repo in find_git_repositories(root):
            for path in get_git_files(repo):
                content = path.read_bytes()
                content_hash = hashlib.sha1(content).hexdigest()
                size = path.stat().st_size
                last_modified = datetime.fromtimestamp(path.stat().st_mtime)
                file_revision = FileRevision(path, content_hash, size, last_modified)
                try:
                    self.context.db.execute("begin;")
                    persistent_file_revision = persist_file_revision(file_revision, self.context)
                    objects = get_objects(persistent_file_revision)
                    tmp = []
                    for obj in objects:
                        persistent_object = persist_object(obj, self.context)
                        tmp.append(persistent_object)
                    self.context.db.execute("commit;")
                    yield from tmp
                except sqlite3.IntegrityError:
                    cursor = self.context.db.cursor()
                    cursor.execute(textwrap.dedent("""
                        select
                            id,
                            file_revision_id,
                            name,
                            language,
                            context_before,
                            context_after,
                            kind,
                            byte_range,
                            coordinates
                        from obj 
                        where file_revision_id = (
                            select id from file_revision where path = ? and hash = ?
                        );
                    """), (file_revision.path, file_revision.hash))
                    for row in cursor.fetchall():
                        yield PersistentObject(
                            id=row['id'],
                            file_revision_id=row['file_revision_id'],
                            name=row['name'],
                            language=row['language'],
                            context_before=json.loads(row['context_before']),
                            context_after=json.loads(row['context_after']),
                            kind=row['kind'],
                            byte_range=json.loads(row['byte_range']),
                            coordinates=json.loads(row['coordinates'])
                        )


@dataclasses.dataclass
class PersistentObject(Object):
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
                migration_text = f.read()
            self.db.executescript(migration_text)
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


def persist_file_revision(file_revision: FileRevision, context: Context) -> PersistentFileRevision:
    cursor = context.db.execute(
        """
        INSERT INTO file_revisions
         (path, hash, size, last_modified)
          VALUES (?, ?, ?, ?)
           RETURNING id
        """,
        (file_revision.path, file_revision.hash, file_revision.size, file_revision.last_modified),
    )
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
