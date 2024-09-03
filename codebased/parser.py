from __future__ import annotations

import ctypes
from functools import lru_cache
from pathlib import Path

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

from codebased.models import Object, Coordinates


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
    line_count = text.count(b'\n') + 1
    last_newline_pos = text.rfind(b'\n')
    total_length = len(text)
    last_line_length = total_length - last_newline_pos - 1
    if last_newline_pos == -1:
        last_line_length = total_length
    return (0, 0), (line_count - 1, last_line_length)


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


def get_capsule_pointer(capsule):
    # This is a highly unconventional and potentially unsafe method
    # It relies on CPython implementation details and may break
    # in different Python versions or implementations

    # Get the memory address of the capsule object
    capsule_address = id(capsule)

    # Create a ctypes structure to represent the PyObject
    class PyObject(ctypes.Structure):
        _fields_ = [("ob_refcnt", ctypes.c_ssize_t),
                    ("ob_type", ctypes.c_void_p)]

    # Create a ctypes structure to represent the PyCapsule
    class PyCapsule(ctypes.Structure):
        _fields_ = [("PyObject_HEAD", PyObject),
                    ("pointer", ctypes.c_void_p),
                    ("name", ctypes.c_char_p),
                    ("context", ctypes.c_void_p),
                    ("destructor", ctypes.c_void_p)]

    # Cast the capsule address to a PyCapsule pointer
    capsule_struct = ctypes.cast(capsule_address, ctypes.POINTER(PyCapsule)).contents

    # Extract the pointer value
    pointer_value = capsule_struct.pointer

    return pointer_value


def parse_objects(path: Path, text: bytes) -> list[Object]:
    file_type = path.suffix[1:]
    impl = get_language_for_file_type(file_type)
    language_name = impl.name if impl else 'text'
    objects = [
        Object(
            path=path,
            name=str(path),
            language=language_name,
            kind='file',
            byte_range=(0, len(text)),
            coordinates=get_text_coordinates(text),
            context_before=[],
            context_after=[]
        )
    ]
    if impl is None:
        return objects
    tree = impl.parser.parse(text)
    root_node = tree.root_node
    chunks = objects
    matches = impl.tags.matches(root_node)
    for _, captures in matches:
        name_node = captures.pop('name')
        for definition_kind, definition_node in captures.items():
            before, after = get_context(definition_node)
            chunks.append(
                Object(
                    path=path,
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


def get_language_for_file_type(file_type: str) -> LanguageImpl | None:
    if file_type == 'py':
        return get_python_impl()
    elif file_type == 'rs':
        return get_rust_impl()
    elif file_type == 'c' or file_type == 'h':
        return get_c_impl()
    elif file_type == 'cc' or file_type == 'cpp' or file_type == 'cxx' or file_type == 'hpp' or file_type == 'hxx' or file_type == 'h':
        return get_cpp_impl()
    elif file_type == 'cs':
        return get_c_sharp_impl()
    elif file_type == 'go':
        return get_go_impl()
    elif file_type == 'java':
        return get_java_impl()
    elif file_type == 'js' or file_type == 'mjs' or file_type == 'cjs' or file_type == 'jsx':
        return get_javascript_impl()
    elif file_type == 'php':
        return get_php_impl()
    elif file_type == 'rb':
        return get_ruby_impl()
    elif file_type == 'ts':
        return get_typescript_impl()
    elif file_type == 'tsx':
        return get_tsx_impl()
    else:
        return None


@lru_cache(1)
def get_php_impl() -> LanguageImpl:
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
    return PHP_IMPL


@lru_cache(1)
def get_ruby_impl() -> LanguageImpl:
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
    return RUBY_IMPL


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


@lru_cache(1)
def get_typescript_impl() -> LanguageImpl:
    TYPESCRIPT_IMPL = LanguageImpl.from_language(
        tree_sitter.Language(tree_sitter_typescript.language_typescript()),
        tags=_TYPESCRIPT_TAG_QUERY,
        file_types=[
            'ts',
        ],
        name='typescript'
    )
    return TYPESCRIPT_IMPL


@lru_cache(1)
def get_tsx_impl() -> LanguageImpl:
    TSX_IMPL = LanguageImpl.from_language(
        tree_sitter.Language(tree_sitter_typescript.language_tsx()),
        tags=_TYPESCRIPT_TAG_QUERY,
        file_types=[
            'tsx',
        ],
        name='tsx'
    )
    return TSX_IMPL


# This is fucked but if we don't keep this at the top level it might get garbage collected and the code will crash.
TSP_LANGUAGE = tree_sitter_python.language()


@lru_cache(1)
def get_python_impl() -> LanguageImpl:
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
    return PYTHON_IMPL


@lru_cache(1)
def get_rust_impl() -> LanguageImpl:
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
    return RUST_IMPL


@lru_cache(1)
def get_c_impl() -> LanguageImpl:
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
    return C_IMPL


@lru_cache(1)
def get_cpp_impl() -> LanguageImpl:
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
    return CPP_IMPL


@lru_cache(1)
def get_c_sharp_impl() -> LanguageImpl:
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
    return C_SHARP_IMPL


@lru_cache(1)
def get_go_impl() -> LanguageImpl:
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
    return GO_IMPL


@lru_cache(1)
def get_java_impl() -> LanguageImpl:
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
    return JAVA_IMPL


@lru_cache(1)
def get_javascript_impl() -> LanguageImpl:
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
    return JAVASCRIPT_IMPL


def render_object(
        obj: Object,
        in_lines: list[bytes],
        *,
        context: bool = True,
        file: bool = True,
        line_numbers: bool = False,
) -> str:
    out_lines = []
    if file:
        out_lines.append(str(obj.path))
        out_lines.append('')
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
