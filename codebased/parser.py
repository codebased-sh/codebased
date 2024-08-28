from __future__ import annotations

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

from codebased.filesystem import get_file_bytes, get_file_lines
from codebased.models import PersistentFileRevision, Object, Coordinates, ObjectHandle
from codebased.segfault import get_capsule_pointer


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


def parse_objects(file_revision: PersistentFileRevision) -> list[Object]:
    file = file_revision.path
    file_type = file.suffix[1:]
    impl = None
    for language in LANGUAGES:
        if file_type in language.file_types:
            impl = language
            break
    text = get_file_bytes(file)
    try:
        # This is wasteful.
        text.decode('utf-8')
    except UnicodeDecodeError:
        return []
    if impl is None:
        default_objects = parse_objects_default(file_revision, text)
        return default_objects
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


def parse_objects_default(file_revision, text):
    default_objects = [
        Object(
            file_revision_id=file_revision.id,
            name=str(file_revision.path),
            language='text',
            kind='file',
            byte_range=(0, len(text)),
            coordinates=get_text_coordinates(text),
            context_before=[],
            context_after=[]
        )
    ]
    return default_objects


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
        'tsx',
    ],
    name='tsx'
)

# This is fucked but if we don't keep this at the top level it will get garbage collected and the code will crash.
TSP_LANGUAGE = tree_sitter_python.language()
PYTHON_IMPL = LanguageImpl.from_language(
    # Don't make breaking changes on me dawg.
    tree_sitter.Language(get_capsule_pointer(TSP_LANGUAGE)),
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
LANGUAGES = [
    PYTHON_IMPL,
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


def render_object(
        obj_handle: ObjectHandle,
        *,
        context: bool = True,
        file: bool = True,
        line_numbers: bool = False
) -> str:
    file_revision = obj_handle.file_revision
    obj = obj_handle.object
    out_lines = []
    if file:
        out_lines.append(str(file_revision.path))
        out_lines.append('')
    in_lines = get_file_lines(file_revision.path)
    max_line_no = max(
        obj.coordinates[0][0],
        obj.coordinates[1][0],
        *obj.context_before,
        # *obj.context_after
    ) + 1
    line_width = len(str(max_line_no))

    def line_formatter(line_index: int, line_content: str) -> str:
        if line_numbers:
            line_number = line_index + 1
            return str(line_number).rjust(line_width) + " " + line_content
        return line_content

    if context:
        for line in obj.context_before:
            out_lines.append(line_formatter(line, in_lines[line].decode('utf-8')))
    start_line, end_line = obj.coordinates[0][0], obj.coordinates[1][0]
    for i in range(start_line, end_line + 1):
        out_lines.append(line_formatter(i, in_lines[i].decode('utf-8')))
    # if context:
    #     for line in obj.context_after[::-1]:
    #         out_lines.append(line_formatter(line, in_lines[line].decode('utf-8')))
    return '\n'.join(out_lines)
