#!/usr/bin/env python3
# Inventory a source file for split planning: declarations, imports, other top-level
# statements and references with tree-sitter, plus module globals, decorators, name
# references and dynamic name lookups with ast and symtable for Python.
# Format: references/plan-format.md.

# cspell:ignore symtable lineno unparse surrogateescape setdefault popitem excepthandler
# cspell:ignore subtree hexdigest delattr getattr hasattr setattr asname dictcomp
# cspell:ignore finalbody genexpr kwonlyargs orelse posonlyargs preproc setcomp vararg
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import importlib.resources
import io
import json
import operator
import re
import symtable
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol

if TYPE_CHECKING:
  from collections.abc import Callable, Iterator, Mapping

TREE_SITTER = 'tree-sitter==0.26.1'
LANGUAGE_RE = re.compile(r'[a-z][a-z0-9_]*')
# --grammar value: a pip pin of a grammar package and its language function.
GRAMMAR_RE = re.compile(
  r'(?P<package>tree-sitter-[a-z0-9]+(?:-[a-z0-9]+)*)==(?P<version>[0-9][0-9a-z.]*)'
  r'(?:@(?P<function>[a-z_][a-z0-9_]*))?',
)
MODULE = '<module>'
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
NESTED = (*SCOPES, ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
BODIES = ('body', 'orelse', 'finalbody', 'handlers', 'cases')
# Scopes without a name of their own and the names symtable gives them.
COMPREHENSIONS: dict[type[ast.AST], str] = {
  ast.DictComp: 'dictcomp',
  ast.GeneratorExp: 'genexpr',
  ast.Lambda: 'lambda',
  ast.ListComp: 'listcomp',
  ast.SetComp: 'setcomp',
}
SELF_NAMES = frozenset({'self', 'cls'})
# Calls that reach names through strings or namespaces, invisible to name references.
DYNAMIC = frozenset({
  '__import__',
  'compile',
  'delattr',
  'eval',
  'exec',
  'getattr',
  'globals',
  'hasattr',
  'import_module',
  'locals',
  'setattr',
  'vars',
})
# The names above that are builtins, dynamic APIs also when called by bare name.
BUILTIN_DYNAMIC = DYNAMIC - {'import_module'}
# Modules whose attributes with the names above are the same dynamic APIs.
DYNAMIC_MODULES = frozenset({'builtins', 'importlib'})
COMPREHENSION_NODES = (ast.DictComp, ast.GeneratorExp, ast.ListComp, ast.SetComp)
# Node types of newer Python versions, empty tuples on older ones.
MATCH_CASES = tuple(getattr(ast, name) for name in ('match_case', ) if hasattr(ast, name))
CAPTURE_TYPES = ('MatchAs', 'MatchMapping', 'MatchStar')
MATCH_CAPTURES = tuple(getattr(ast, name) for name in CAPTURE_TYPES if hasattr(ast, name))
TYPE_ALIASES = tuple(getattr(ast, name) for name in ('TypeAlias', ) if hasattr(ast, name))
PARAM_TYPES = ('ParamSpec', 'TypeVar', 'TypeVarTuple')
TYPE_PARAMS = tuple(getattr(ast, name) for name in PARAM_TYPES if hasattr(ast, name))
# Method names that change a global container in place; the list is approximate.
MUTATORS = frozenset({
  'add',
  'append',
  'cache_clear',
  'clear',
  'discard',
  'extend',
  'insert',
  'pop',
  'popitem',
  'remove',
  'reverse',
  'setdefault',
  'sort',
  'update',
})


@dataclass(frozen=True)
class Grammar:
  package: str
  version: str
  module: str
  imports: frozenset[str]
  skip: frozenset[str]
  function: str = 'language'
  # Member wrappers whose range replaces the member node, like Python decorators.
  wrappers: frozenset[str] = frozenset()
  # Definition kinds whose nested definitions become members `Container.member`.
  containers: frozenset[str] = frozenset({'class'})
  # Sibling nodes that belong to the next top-level unit, like Rust attributes.
  attached: frozenset[str] = frozenset()
  # Containers without a definition whose body children are top-level units.
  transparent: Mapping[str, str] = field(default_factory=dict)
  # Node type -> field naming the owner type of the definitions inside it.
  scopes: Mapping[str, str] = field(default_factory=dict)
  # False when the tags query misses calls or marks every identifier as a call.
  references: bool = True

  def requirements(self) -> list[str]:
    return [TREE_SITTER, f'{self.package}=={self.version}']


def typescript(function: str) -> Grammar:
  return Grammar(
    package='tree-sitter-typescript',
    version='0.23.2',
    module='tree_sitter_typescript',
    imports=frozenset({'import_alias', 'import_statement'}),
    skip=frozenset({'comment', 'hash_bang_line', 'html_comment'}),
    function=function,
    containers=frozenset({'class', 'interface'}),
    references=False,
  )


# Language key -> pinned grammar package, its import name and node types of the grammar.
# Only grammars whose package ships a tags query; see references/scripts.md.
LANGUAGES: dict[str, Grammar] = {
  'c':
    Grammar(
      package='tree-sitter-c',
      version='0.24.2',
      module='tree_sitter_c',
      imports=frozenset({'preproc_include'}),
      skip=frozenset({'comment'}),
      containers=frozenset(),
      references=False,
    ),
  'cpp':
    Grammar(
      package='tree-sitter-cpp',
      version='0.23.4',
      module='tree_sitter_cpp',
      imports=frozenset({'preproc_include'}),
      skip=frozenset({'comment'}),
      transparent={
        'linkage_specification': 'declaration_list',
        'namespace_definition': 'declaration_list',
      },
      references=False,
    ),
  'go':
    Grammar(
      package='tree-sitter-go',
      version='0.25.0',
      module='tree_sitter_go',
      imports=frozenset({'import_declaration', 'package_clause'}),
      skip=frozenset({'comment'}),
      containers=frozenset(),
      scopes={
        'method_declaration': 'receiver',
      },
    ),
  'java':
    Grammar(
      package='tree-sitter-java',
      version='0.23.5',
      module='tree_sitter_java',
      imports=frozenset({'import_declaration', 'package_declaration'}),
      skip=frozenset({'block_comment', 'line_comment'}),
      containers=frozenset({'class', 'interface'}),
    ),
  'javascript':
    Grammar(
      package='tree-sitter-javascript',
      version='0.25.0',
      module='tree_sitter_javascript',
      imports=frozenset({'import_statement'}),
      skip=frozenset({'comment', 'hash_bang_line', 'html_comment'}),
    ),
  'lua':
    Grammar(
      package='tree-sitter-lua',
      version='0.5.0',
      module='tree_sitter_lua',
      imports=frozenset(),
      skip=frozenset({'comment', 'hash_bang_line'}),
      containers=frozenset(),
    ),
  'php':
    Grammar(
      package='tree-sitter-php',
      version='0.25.1',
      module='tree_sitter_php',
      imports=frozenset({'namespace_use_declaration'}),
      skip=frozenset({'comment', 'php_tag'}),
      function='language_php',
      containers=frozenset({'class', 'interface'}),
    ),
  'python':
    Grammar(
      package='tree-sitter-python',
      version='0.25.0',
      module='tree_sitter_python',
      imports=frozenset({
        'future_import_statement',
        'import_from_statement',
        'import_statement',
      }),
      skip=frozenset({'comment'}),
      wrappers=frozenset({'decorated_definition'}),
    ),
  'ruby':
    Grammar(
      package='tree-sitter-ruby',
      version='0.23.1',
      module='tree_sitter_ruby',
      imports=frozenset(),
      skip=frozenset({'comment'}),
      containers=frozenset({'class', 'module'}),
      references=False,
    ),
  'rust':
    Grammar(
      package='tree-sitter-rust',
      version='0.24.2',
      module='tree_sitter_rust',
      imports=frozenset({'extern_crate_declaration', 'use_declaration'}),
      skip=frozenset({'block_comment', 'line_comment'}),
      containers=frozenset({'class', 'interface', 'module'}),
      attached=frozenset({'attribute_item'}),
      scopes={
        'impl_item': 'type',
      },
    ),
  'scala':
    Grammar(
      package='tree-sitter-scala',
      version='0.26.13',
      module='tree_sitter_scala',
      imports=frozenset({'export_declaration', 'import_declaration'}),
      skip=frozenset({'block_comment', 'comment'}),
      containers=frozenset({'class', 'enum', 'interface', 'object'}),
    ),
  'tsx':
    typescript('language_tsx'),
  'typescript':
    typescript('language_typescript'),
}


class InventoryError(Exception):
  pass


class UsageError(InventoryError):
  pass


class Point(Protocol):
  row: int
  column: int


class Node(Protocol):
  id: int
  type: str
  start_point: Point
  end_point: Point
  start_byte: int
  end_byte: int
  parent: Node | None
  prev_named_sibling: Node | None
  named_children: list[Node]
  has_error: bool

  def child_by_field_name(self, name: str, /) -> Node | None:
    ...


@dataclass(frozen=True)
class Definition:
  node: Node
  kind: str
  name: str


def start_line(node: Node) -> int:
  return node.start_point.row + 1


def end_line(node: Node) -> int:
  end = node.end_point
  if end.column == 0 and end.row > node.start_point.row:
    return end.row
  return end.row + 1


def definition_start(definition: Definition) -> int:
  return definition.node.start_byte


DECLARATION_ORDER = operator.itemgetter('start_line', 'name')
GLOBAL_ORDER = operator.itemgetter('line', 'name')
DYNAMIC_ORDER = operator.itemgetter('line', 'from', 'text')


def reference_order(item: dict[str, Any]) -> tuple[int, str, str, str]:
  return (item['line'], item['from'], ','.join(item['to']), item['source'])


def grammar_for(language: str, spec: str | None = None) -> Grammar:
  if not LANGUAGE_RE.fullmatch(language):
    msg = f'bad language key: {language}'
    raise UsageError(msg)
  if spec is not None:
    return generic_grammar(language, spec)
  grammar = LANGUAGES.get(language)
  if grammar is None:
    known = ', '.join(sorted(LANGUAGES))
    msg = f'unsupported language: {language}; known: {known}; or pass --grammar'
    raise UsageError(msg)
  return grammar


def generic_grammar(language: str, spec: str) -> Grammar:
  if language in LANGUAGES:
    msg = f'--grammar is for languages outside the table, {language} is in it'
    raise UsageError(msg)
  found = GRAMMAR_RE.fullmatch(spec)
  if found is None:
    msg = f'bad --grammar: {spec}; expected tree-sitter-<name>==<version>[@<function>]'
    raise UsageError(msg)
  package = found['package']
  # Imports are unknown to a generic grammar and stay in statements.
  return Grammar(
    package=package,
    version=found['version'],
    module=package.replace('-', '_'),
    imports=frozenset(),
    skip=frozenset({'comment'}),
    function=found['function'] or 'language',
    containers=frozenset({'class', 'enum', 'interface', 'module', 'object'}),
  )


def tags_query(binding: object, module: str) -> str:
  query = getattr(binding, 'TAGS_QUERY', None)
  if isinstance(query, str):
    return query
  resource = importlib.resources.files(module) / 'queries' / 'tags.scm'
  try:
    return resource.read_text(encoding='utf-8')
  except (OSError, UnicodeDecodeError) as error:
    msg = f'{module} has no tags query: {error}'
    raise InventoryError(msg) from error


def type_name(node: Node) -> Node | None:
  pending = [node]
  while pending:
    current = pending.pop(0)
    if current.type == 'type_identifier':
      return current
    pending.extend(current.named_children)
  return None


class TreeSitterLayer:
  def __init__(self, grammar: Grammar, data: bytes) -> None:
    try:
      core = importlib.import_module('tree_sitter')
      binding = importlib.import_module(grammar.module)
    except ImportError as error:
      msg = f'{error}; run inventory.sh or pass --no-tree-sitter'
      raise InventoryError(msg) from error
    language = core.Language(getattr(binding, grammar.function)())
    self.grammar = grammar
    self.data = data
    self.root: Node = core.Parser(language).parse(data).root_node
    query = core.Query(language, tags_query(binding, grammar.module))
    cursor = core.QueryCursor(query)
    self.matches: list[tuple[int, dict[str, list[Node]]]] = cursor.matches(self.root)
    self.units = self.top_units()
    # editorconfig-checker-disable
    self.unit_ids = {unit.id
                     for unit in self.units}
    # editorconfig-checker-enable
    self.owners: dict[int, str] = {}
    self.roots: set[int] = set()
    self.declarations: list[dict[str, Any]] = []
    self.imports: list[dict[str, Any]] = []
    self.statements: list[dict[str, Any]] = []
    self.references: list[dict[str, Any]] = []
    self.collect_declarations()
    self.collect_top_level()
    self.collect_references()

  def text(self, node: Node) -> str:
    return self.data[node.start_byte:node.end_byte].decode('utf-8', 'replace')

  def top_units(self) -> list[Node]:
    units: list[Node] = []
    pending = list(self.root.named_children)
    while pending:
      node = pending.pop(0)
      body_type = self.grammar.transparent.get(node.type)
      body = None
      if body_type is not None:
        body = next((child for child in node.named_children if child.type == body_type),
                    None)
      if body is None:
        units.append(node)
      else:
        pending[0:0] = body.named_children
    return units

  def unit(self, node: Node) -> Node:
    current = node
    while current.id not in self.unit_ids and current.parent is not None:
      current = current.parent
    return current

  def first_line(self, node: Node) -> int:
    first = node
    while (
      first.prev_named_sibling is not None
      and first.prev_named_sibling.type in self.grammar.attached
    ):
      first = first.prev_named_sibling
    return start_line(first)

  def scope(self, definition: Definition, unit: Node) -> str | None:
    for node in (definition.node, unit):
      name = self.grammar.scopes.get(node.type)
      target = None if name is None else node.child_by_field_name(name)
      found = None if target is None else type_name(target)
      if found is not None:
        return self.text(found)
    return None

  def definitions(self) -> dict[int, Definition]:
    found: dict[int, Definition] = {}
    for _, captures in self.matches:
      names = captures.get('name', [])
      for capture, nodes in captures.items():
        if not capture.startswith('definition.') or not nodes or not names:
          continue
        if nodes[0].type not in self.grammar.transparent:
          kind = capture.split('.', 1)[1]
          found[nodes[0].id] = Definition(nodes[0], kind, self.text(names[0]))
    return found

  @staticmethod
  def outer(node: Node, found: dict[int, Definition]) -> Definition | None:
    current = node.parent
    while current is not None:
      if current.id in found:
        return found[current.id]
      current = current.parent
    return None

  def record(
    self,
    name: str,
    parent: str | None,
    kind: str,
    node: Node,
  ) -> dict[str, Any]:
    return {
      'name': name,
      'parent': parent,
      'kind': kind,
      'start_line': self.first_line(node),
      'end_line': end_line(node),
    }

  def collect_declarations(self) -> None:
    found = self.definitions()
    for definition in sorted(found.values(), key=definition_start):
      outer = self.outer(definition.node, found)
      if outer is None:
        self.add_top(definition)
        continue
      container = outer.kind in self.grammar.containers
      if container and self.outer(outer.node, found) is None:
        self.add_member(outer.name, definition)
    self.declarations.sort(key=DECLARATION_ORDER)

  def add_top(self, definition: Definition) -> None:
    top = self.unit(definition.node)
    self.roots.add(top.id)
    label = self.scope(definition, top)
    if label is not None:
      self.add_member(label, definition)
      return
    self.owners.setdefault(top.id, definition.name)
    self.owners[definition.node.id] = definition.name
    self.declarations.append(self.record(definition.name, None, definition.kind, top))

  def add_member(self, parent: str, definition: Definition) -> None:
    node = definition.node
    if node.parent is not None and node.parent.type in self.grammar.wrappers:
      node = node.parent
    name = f'{parent}.{definition.name}'
    kind = 'method' if definition.kind == 'function' else definition.kind
    self.owners[node.id] = name
    self.owners[definition.node.id] = name
    self.declarations.append(self.record(name, parent, kind, node))

  def collect_top_level(self) -> None:
    for unit in self.units:
      if unit.id in self.roots or unit.type in self.grammar.attached:
        continue
      if unit.type in self.grammar.skip:
        continue
      if unit.type in self.grammar.imports:
        self.imports.append({
          'start_line': self.first_line(unit),
          'end_line': end_line(unit),
          'text': self.text(unit),
        })
      else:
        self.statements.append({
          'name': f'{unit.type}@{self.first_line(unit)}',
          'kind': unit.type,
          'start_line': self.first_line(unit),
          'end_line': end_line(unit),
        })

  def owner(self, node: Node) -> str:
    current: Node | None = node
    while current is not None:
      if current.id in self.owners:
        return self.owners[current.id]
      current = current.parent
    return MODULE

  def collect_references(self) -> None:
    if not self.grammar.references:
      return
    names: dict[str, list[str]] = {}
    for item in self.declarations:
      simple = item['name'].rsplit('.', 1)[-1]
      if item['name'] not in names.setdefault(simple, []):
        names[simple].append(item['name'])
    for _, captures in self.matches:
      targets = captures.get('name', [])
      for capture in captures:
        if not capture.startswith('reference.') or not targets:
          continue
        found = names.get(self.text(targets[0]), [])
        if found:
          self.references.append({
            'from': self.owner(targets[0]),
            'to': sorted(found),
            'line': start_line(targets[0]),
            'source': 'tags',
          })


def module_statements(body: list[Any]) -> Iterator[ast.stmt]:
  for node in body:
    if isinstance(node, ast.stmt):
      yield node
      if isinstance(node, SCOPES):
        continue
    for name in BODIES:
      yield from module_statements(getattr(node, name, []))


def module_stores(tree: ast.Module) -> dict[str, int]:
  # Variables of the module: names its code binds besides definitions and imports,
  # also when the same name is imported or defined too. Inside comprehensions only
  # `NAME := …` binds in the module.
  lines: dict[str, int] = {}
  pending: list[tuple[Any, bool]] = [(node, False) for node in tree.body]
  while pending:
    node, inner = pending.pop()
    comprehension = isinstance(node, COMPREHENSION_NODES)
    if isinstance(node, NESTED) and not comprehension:
      continue
    found: list[str] = []
    if inner:
      if isinstance(node, ast.NamedExpr):
        found = bound_names(node.target)
    elif not isinstance(node, ast.ExceptHandler):
      # `except … as NAME` unbinds NAME at the end of the clause, so it alone makes
      # no variable.
      found = bound_names(node)
    for name in found:
      lines[name] = min(node.lineno, lines.get(name, node.lineno))
    children = ast.iter_child_nodes(node)
    pending.extend((child, inner or comprehension) for child in children)
  return lines


def subtree(table: symtable.SymbolTable) -> list[symtable.SymbolTable]:
  tables = [table]
  for child in table.get_children():
    tables.extend(subtree(child))
  return tables


class Block(NamedTuple):
  # A child symbol table: its name, its line or None for any line, and a slot that
  # tells apart the tables of one scope node.
  name: str
  line: int | None
  slot: str


class Step(NamedTuple):
  # AST nodes that run in the table at the end of a path of child tables.
  parts: list[ast.AST]
  path: tuple[Block, ...]


def arg_annotations(args: ast.arguments) -> list[ast.expr]:
  every = [*args.posonlyargs, *args.args, args.vararg, *args.kwonlyargs, args.kwarg]
  return [
    note for item in every if item is not None and (note := item.annotation) is not None
  ]


def defaults(args: ast.arguments) -> list[ast.AST]:
  return [*args.defaults, *[item for item in args.kw_defaults if item is not None]]


def own_path(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> tuple[Block]:
  # The outermost table of a function or class: of its type parameters if it has any.
  slot = 'params' if getattr(node, 'type_params', None) else 'body'
  return (Block(node.name, node.lineno, slot), )


def function_steps(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[Step]:
  params: list[ast.AST] = list(getattr(node, 'type_params', []))
  head = own_path(node) if params else ()
  notes: list[ast.AST] = list(arg_annotations(node.args))
  if node.returns is not None:
    notes.append(node.returns)
  return [
    Step([*defaults(node.args), *node.decorator_list], ()),
    Step([*params, *notes], head),
    Step(list(node.body), (*head, Block(node.name, node.lineno, 'body'))),
  ]


def class_steps(node: ast.ClassDef) -> list[Step]:
  params: list[ast.AST] = list(getattr(node, 'type_params', []))
  head = own_path(node) if params else ()
  return [
    Step(list(node.decorator_list), ()),
    Step([*params, *node.bases, *node.keywords], head),
    Step(list(node.body), (*head, Block(node.name, node.lineno, 'body'))),
  ]


class AliasNode(Protocol):
  # `type Name[T] = value` of Python 3.12+.
  lineno: int
  name: ast.Name
  type_params: list[ast.AST]
  value: ast.expr


class ParamNode(Protocol):
  # A type parameter of Python 3.12+.
  name: str


def alias_steps(node: AliasNode) -> list[Step]:
  name = node.name.id
  params: list[ast.AST] = list(node.type_params)
  head = (Block(name, node.lineno, 'params'), ) if params else ()
  return [
    Step([node.name], ()),
    Step(params, head),
    Step([node.value], (*head, Block(name, node.lineno, 'body'))),
  ]


def param_steps(node: ParamNode) -> list[Step]:
  # The bound and the default of a type parameter run in tables of their own, which
  # carry the line of the parameter or of the expression, depending on version.
  steps: list[Step] = []
  for slot in ('bound', 'default_value'):
    part = getattr(node, slot, None)
    if part is not None:
      steps.append(Step([part], (Block(node.name, None, slot), )))
  return steps


def lambda_steps(node: ast.Lambda) -> list[Step]:
  body = Block(COMPREHENSIONS[ast.Lambda], node.lineno, 'body')
  return [Step(defaults(node.args), ()), Step([node.body], (body, ))]


def comprehension_steps(
  node: ast.DictComp | ast.GeneratorExp | ast.ListComp | ast.SetComp,
) -> list[Step]:
  first, *rest = node.generators
  tail = [node.value, node.key] if isinstance(node, ast.DictComp) else [node.elt]
  body = Block(COMPREHENSIONS[type(node)], node.lineno, 'body')
  return [
    Step([first.iter], ()),
    Step([first.target, *first.ifs, *rest, *tail], (body, )),
  ]


# Builders of the steps of scope nodes, in the order symtable of CPython 3.9-3.13
# visits their parts and opens their tables.
STEP_BUILDERS: tuple[tuple[tuple[type, ...], Callable[[Any], list[Step]]], ...] = (
  (FUNCTIONS, function_steps),
  ((ast.ClassDef, ), class_steps),
  (TYPE_ALIASES, alias_steps),
  (TYPE_PARAMS, param_steps),
  ((ast.Lambda, ), lambda_steps),
  (COMPREHENSION_NODES, comprehension_steps),
)


def scope_steps(node: ast.AST) -> list[Step] | None:
  # Parts of a node with tables of its own, each with the path of child tables it runs
  # in; None for other nodes.
  for types, build in STEP_BUILDERS:
    if isinstance(node, types):
      return build(node)
  return None


def refers_global(table: symtable.SymbolTable, name: str) -> bool:
  try:
    return table.lookup(name).is_global()
  except KeyError:
    return False


def lookup(table: symtable.SymbolTable, name: str) -> symtable.Symbol | None:
  try:
    return table.lookup(name)
  except KeyError:
    return None


def module_binds(table: symtable.SymbolTable, name: str) -> bool:
  symbol = lookup(table, name)
  return symbol is not None and (symbol.is_assigned() or symbol.is_imported())


def future_annotations(tree: ast.Module) -> bool:
  return any(
    isinstance(node, ast.ImportFrom) and node.module == '__future__'
    and any(item.name == 'annotations' for item in node.names) for node in tree.body
  )


def lazy_annotations(
  tree: ast.Module,
  tables: dict[int, symtable.SymbolTable],
) -> set[int]:
  # Nodes inside annotations Python does not evaluate: every annotation under
  # `from __future__ import annotations` and annotations of local variables. They are
  # neither reads nor dynamic API uses.
  future = future_annotations(tree)
  found: set[int] = set()
  for node in ast.walk(tree):
    notes: list[ast.expr] = []
    if isinstance(node, FUNCTIONS) and future:
      notes = arg_annotations(node.args)
      if node.returns is not None:
        notes.append(node.returns)
    elif isinstance(node, ast.AnnAssign):
      if future or tables[id(node)].get_type() == 'function':
        notes = [node.annotation]
    found.update(id(item) for note in notes for item in ast.walk(note))
  return found


def dynamic_bindings(node: ast.AST) -> list[tuple[str, str]]:
  # Pairs (name, kind) that an import binds to a module from DYNAMIC_MODULES or to a
  # dynamic API from one.
  found: list[tuple[str, str]] = []
  if isinstance(node, ast.Import):
    for item in node.names:
      top = item.name.split('.')[0]
      if item.name in DYNAMIC_MODULES or (top in DYNAMIC_MODULES and not item.asname):
        found.append((item.asname or top, 'module'))
  elif isinstance(node, ast.ImportFrom) and node.module in DYNAMIC_MODULES:
    # editorconfig-checker-disable
    found.extend((item.asname or item.name, 'api') for item in node.names
                 if item.name in DYNAMIC)
    # editorconfig-checker-enable
  return found


def bindings(node: ast.AST) -> list[tuple[ast.expr, str]]:
  # Pairs (value, name) of plain assignments to a name.
  targets: list[ast.expr] = []
  if isinstance(node, ast.Assign):
    targets = node.targets
  elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
    targets = [node.target]
  value = getattr(node, 'value', None)
  return [(value, item.id) for item in targets if isinstance(item, ast.Name) and value]


def dynamic_use(
  node: ast.expr,
  api: DynamicApi,
  table: symtable.SymbolTable,
  called: set[int],
) -> bool:
  # A call of a dynamic API or of its alias, or a reference to one passed on uncalled.
  if isinstance(node, ast.Call):
    return api.refers(node.func, table)
  if id(node) in called or not api.refers(node, table):
    return False
  return isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Load)


def python_text(data: bytes, path: str) -> str:
  try:
    encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
  except SyntaxError as error:
    msg = f'{path}: {error}'
    raise InventoryError(msg) from error
  return data.decode(encoding)


def root_name(node: ast.expr) -> str | None:
  # The name at the bottom of a chain of attributes and indexes, as G in G[k].items.
  while isinstance(node, (ast.Attribute, ast.Subscript)):
    node = node.value
  return node.id if isinstance(node, ast.Name) else None


def mutated_name(node: ast.AST) -> str | None:
  if not isinstance(node, (ast.Attribute, ast.Subscript)):
    return None
  if isinstance(node.ctx, (ast.Store, ast.Del)):
    return root_name(node)
  # A bound changing method, called or passed on, for example as a decorator.
  if isinstance(node, ast.Attribute) and node.attr in MUTATORS:
    return root_name(node)
  return None


def bound_names(node: ast.AST) -> list[str]:
  # Names an AST node binds besides definitions and imports, which are not variables:
  # a Store or Del name, `except … as NAME` and the captures of `case` patterns.
  if isinstance(node, ast.Name):
    return [] if isinstance(node.ctx, ast.Load) else [node.id]
  if isinstance(node, (ast.ExceptHandler, *MATCH_CAPTURES)):
    found = [getattr(node, slot, None) for slot in ('name', 'rest')]
    return [name for name in found if isinstance(name, str)]
  return []


def accesses(node: ast.AST) -> list[tuple[str, str]]:
  # Pairs (kind, name) of reads, writes and in-place changes of a name made by one AST
  # node; a write counts only where the name is global, as after `global X`.
  if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
    return [('readers', node.id)]
  written = bound_names(node)
  if written:
    return [('writers', name) for name in written]
  if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
    # The target of `X += 1` is a Store name, yet the statement reads X.
    return [('readers', node.target.id)]
  name = mutated_name(node)
  return [] if name is None else [('mutators', name)]


def shallow(root: ast.AST) -> list[ast.AST]:
  # The nodes of a root, without the body of an except clause: its statements are
  # roots of their own.
  return [root] if isinstance(root, ast.ExceptHandler) else list(ast.walk(root))


def loose_parts(node: ast.stmt) -> list[ast.AST]:
  # Expressions of a top-level statement outside its nested statement bodies; an except
  # clause stands for its own `as NAME` only, see shallow.
  parts: list[ast.AST] = []
  for child in ast.iter_child_nodes(node):
    if isinstance(child, ast.ExceptHandler):
      parts.append(child)
      parts.extend(part for part in (child.type, ) if part is not None)
    elif isinstance(child, MATCH_CASES):
      pattern = child.pattern  # pyright: ignore[reportAttributeAccessIssue]
      guard = child.guard  # pyright: ignore[reportAttributeAccessIssue]
      parts.extend(part for part in (pattern, guard) if part is not None)
    elif not isinstance(child, ast.stmt):
      parts.append(child)
  return parts


class ScopeMap:
  # The symbol table of the scope every AST node runs in. Scope nodes are split into
  # steps in the order symtable visits them, and child tables with the same name and
  # line are matched to their scope nodes in that order.
  def __init__(self, tree: ast.Module, table: symtable.SymbolTable, path: str) -> None:
    self.root = table
    self.tables: dict[int, symtable.SymbolTable] = {}
    self.parents: dict[int, symtable.SymbolTable] = {}
    self.claimed: dict[tuple[int, tuple[Block, ...]], symtable.SymbolTable | None] = {}
    self.taken: dict[tuple[int, str, int | None], int] = {}
    pending: list[tuple[ast.AST, symtable.SymbolTable, int, tuple[Block, ...]]] = [
      (tree, table, 0, ()),
    ]
    while pending:
      node, host, owner, steps_path = pending.pop()
      # A table is claimed when the first node in it is reached, as symtable opens it.
      current = self.claim(owner, host, steps_path)
      self.tables[id(node)] = current
      steps = scope_steps(node)
      if steps is None:
        children = list(ast.iter_child_nodes(node))
        pending.extend((child, current, 0, ()) for child in reversed(children))
        continue
      # editorconfig-checker-disable
      ordered = [(part, current, id(node), step.path) for step in steps
                 for part in step.parts]
      # editorconfig-checker-enable
      pending.extend(reversed(ordered))
    # Nodes between a scope node and its parts, like arguments, run where their parent
    # runs; ast.walk reaches a parent before its children.
    for node in ast.walk(tree):
      for child in ast.iter_child_nodes(node):
        self.tables.setdefault(id(child), self.tables[id(node)])
    self.lazy = lazy_annotations(tree, self.tables)
    self.check(tree, path)

  def claim(
    self,
    owner: int,
    host: symtable.SymbolTable,
    path: tuple[Block, ...],
  ) -> symtable.SymbolTable:
    table = host
    for depth in range(1, len(path) + 1):
      key = (owner, path[:depth])
      if key not in self.claimed:
        self.claimed[key] = self.child(table, path[depth - 1])
      found = self.claimed[key]
      table = table if found is None else found
    return table

  def child(
    self,
    parent: symtable.SymbolTable,
    block: Block,
  ) -> symtable.SymbolTable | None:
    count = (parent.get_id(), block.name, block.line)
    index = self.taken.get(count, 0)
    same = [
      item for item in parent.get_children()
      if item.get_name() == block.name and block.line in {None, item.get_lineno()}
    ]
    if index >= len(same):
      return None
    self.taken[count] = index + 1
    self.parents[same[index].get_id()] = parent
    return same[index]

  def binding(
    self,
    table: symtable.SymbolTable,
    name: str,
  ) -> symtable.SymbolTable | None:
    # The table a name read in a table is bound in: free names are looked up in the
    # enclosing tables, skipping classes as Python does; None for builtins.
    current: symtable.SymbolTable | None = table
    nested = False
    while current is not None:
      symbol = lookup(current, name)
      if symbol is not None and not (nested and current.get_type() == 'class'):
        if symbol.is_global():
          return self.root if module_binds(self.root, name) else None
        if not symbol.is_free():
          return current
      nested = True
      current = self.parents.get(current.get_id())
    return None

  def own(
    self,
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
  ) -> symtable.SymbolTable | None:
    return self.claimed.get((id(node), own_path(node)))

  def check(self, tree: ast.Module, path: str) -> None:
    # symtable records every name read in the table of its scope. A miss means the
    # steps do not match this Python version: fail instead of reporting wrong lists.
    known: dict[int, frozenset[str]] = {}
    for node in ast.walk(tree):
      if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
        continue
      if node.id.startswith('__') and not node.id.endswith('__'):
        # Private names are mangled inside classes.
        continue
      table = self.tables[id(node)]
      if table.get_id() not in known:
        known[table.get_id()] = frozenset(table.get_identifiers())
      if node.id not in known[table.get_id()] and id(node) not in self.lazy:
        version = f'Python {sys.version_info[0]}.{sys.version_info[1]}'
        msg = f'{path}:{node.lineno}: cannot map {node.id} to its scope on {version}'
        raise InventoryError(msg)


class DynamicApi:
  # Names bound to dynamic APIs and to modules from DYNAMIC_MODULES, by the table that
  # binds them; aliases are followed through chains of plain assignments.
  def __init__(self, tree: ast.Module, scopes: ScopeMap) -> None:
    self.scopes = scopes
    self.bound: dict[tuple[int, str], str] = {}
    pairs: list[tuple[symtable.SymbolTable, ast.expr, str]] = []
    for node in ast.walk(tree):
      table = scopes.tables[id(node)]
      for name, kind in dynamic_bindings(node):
        self.bind(table, name, kind)
      pairs.extend((table, value, name) for value, name in bindings(node))
    size = -1
    while size != len(self.bound):
      size = len(self.bound)
      for table, value, name in pairs:
        found = self.kind(value, table)
        if found is not None:
          self.bind(table, name, found)

  def bind(self, table: symtable.SymbolTable, name: str, kind: str) -> None:
    # A `global` name is bound in the module table.
    found = self.scopes.binding(table, name) or self.scopes.root
    self.bound.setdefault((found.get_id(), name), kind)

  def kind(self, node: ast.AST, table: symtable.SymbolTable) -> str | None:
    # 'api' or 'module' for a name or attribute that resolves to a dynamic API or to a
    # module from DYNAMIC_MODULES; methods of other objects with the same names, like
    # re.compile, and other functions with the same names are not.
    if isinstance(node, ast.Attribute):
      if node.attr in DYNAMIC and self.kind(node.value, table) == 'module':
        return 'api'
      return None
    if not isinstance(node, ast.Name) or lookup(table, node.id) is None:
      return None
    found = self.scopes.binding(table, node.id)
    if found is None:
      return 'api' if node.id in BUILTIN_DYNAMIC else None
    return self.bound.get((found.get_id(), node.id))

  def refers(self, node: ast.AST, table: symtable.SymbolTable) -> bool:
    return self.kind(node, table) == 'api'


@dataclass
class Owner:
  name: str
  cls: str | None
  roots: list[ast.AST]
  tables: list[symtable.SymbolTable]
  decorators: list[ast.expr]


class PythonLayer:
  def __init__(self, text: str, path: str) -> None:
    try:
      self.tree = ast.parse(text, filename=path)
      self.table = symtable.symtable(text, path, 'exec')
    except SyntaxError as error:
      msg = f'{path}: {error}'
      raise InventoryError(msg) from error
    self.text = text
    self.owners: dict[str, Owner] = {}
    self.members: set[str] = set()
    self.classes: set[str] = set()
    self.references: list[dict[str, Any]] = []
    self.dynamic: list[dict[str, Any]] = []
    self.scopes = ScopeMap(self.tree, self.table, path)
    self.dynamic_api = DynamicApi(self.tree, self.scopes)
    self.collect_owners()
    self.globals = self.module_globals()
    self.collect_references()

  def add_owner(self, owner: Owner) -> None:
    known = self.owners.get(owner.name)
    if known is None:
      self.owners[owner.name] = owner
      return
    known.roots.extend(owner.roots)
    known.tables.extend(owner.tables)
    known.decorators.extend(owner.decorators)

  def add_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
    table = self.scopes.own(node)
    tables = [] if table is None else subtree(table)
    self.add_owner(Owner(node.name, None, [node], tables, list(node.decorator_list)))

  def add_class(self, node: ast.ClassDef) -> None:
    self.classes.add(node.name)
    table = self.scopes.own(node)
    params: list[ast.AST] = list(getattr(node, 'type_params', []))
    shell: list[ast.AST] = [*params, *node.bases, *node.keywords]
    claimed: list[symtable.SymbolTable] = []
    for item in node.body:
      if not isinstance(item, FUNCTIONS):
        shell.append(item)
        continue
      name = f'{node.name}.{item.name}'
      self.members.add(name)
      method = self.scopes.own(item)
      tables = [] if method is None else subtree(method)
      claimed.extend(tables)
      self.add_owner(Owner(name, node.name, [item], tables, list(item.decorator_list)))
    own = [] if table is None else subtree(table)
    tables = [item for item in own if item not in claimed]
    decorators = list(node.decorator_list)
    self.add_owner(Owner(node.name, node.name, shell, tables, decorators))

  def collect_owners(self) -> None:
    for node in module_statements(self.tree.body):
      if isinstance(node, FUNCTIONS):
        self.add_function(node)
      elif isinstance(node, ast.ClassDef):
        self.add_class(node)
    for node in self.tree.body:
      target: ast.expr | None = node.target if isinstance(node, ast.AnnAssign) else None
      if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
      if isinstance(target, ast.Name):
        self.add_owner(Owner(target.id, None, [node], [], []))
    # Top-level code outside declarations, like the `if __name__` block.
    # editorconfig-checker-disable
    owned = {id(root)
             for item in self.owners.values()
             for root in item.roots}
    # editorconfig-checker-enable
    loose: list[ast.AST] = []
    for node in module_statements(self.tree.body):
      if isinstance(node, SCOPES) or id(node) in owned:
        continue
      if any(getattr(node, name, None) for name in BODIES):
        loose.extend(loose_parts(node))
      else:
        # A simple statement as a whole, so that `X += 1` is a read too.
        loose.append(node)
    self.add_owner(Owner(MODULE, None, loose, [], []))

  def module_globals(self) -> dict[str, int]:
    found: dict[str, int] = {}
    found.update(module_stores(self.tree))
    for owner in self.owners.values():
      for table in owner.tables:
        for symbol in table.get_symbols():
          if symbol.is_declared_global() and symbol.is_assigned():
            found.setdefault(symbol.get_name(), table.get_lineno())
    return found

  def usage(self, owner: Owner) -> dict[str, set[str]]:
    used: dict[str, set[str]] = {
      'readers': set(),
      'writers': set(),
      'mutators': set(),
    }
    for table in owner.tables:
      for symbol in table.get_symbols():
        name = symbol.get_name()
        if name in self.globals and symbol.is_declared_global() and symbol.is_assigned():
          used['writers'].add(name)
    for root in [*owner.roots, *owner.decorators]:
      for node in shallow(root):
        if id(node) in self.scopes.lazy:
          continue
        table = self.scopes.tables[id(node)]
        for kind, name in accesses(node):
          # The assignment that defines a variable is not its writer.
          if kind == 'writers' and name == owner.name:
            continue
          if name in self.globals and refers_global(table, name):
            used[kind].add(name)
    return used

  def report_globals(self) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {
      name: {
        'name': name,
        'line': line,
        'readers': [],
        'writers': [],
        'mutators': [],
      }
      for name, line in self.globals.items()
    }
    for owner in sorted(self.owners.values(), key=owner_name):
      for kind, names in self.usage(owner).items():
        for name in sorted(names):
          rows[name][kind].append(owner.name)
    return sorted(rows.values(), key=GLOBAL_ORDER)

  def report_decorators(self) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for owner in sorted(self.owners.values(), key=owner_name):
      texts = [
        ast.get_source_segment(self.text, item) or ast.unparse(item)
        for item in owner.decorators
      ]
      if texts:
        found[owner.name] = texts
    return found

  def target(self, node: ast.AST, cls: str | None) -> str | None:
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
      if node.id in self.owners and '.' not in node.id:
        return node.id
      return None
    if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
      return None
    base = node.value.id
    if base in SELF_NAMES and cls is not None:
      base = cls
    name = f'{base}.{node.attr}'
    return name if base in self.classes and name in self.members else None

  def segment(self, node: ast.expr) -> str:
    text = ast.get_source_segment(self.text, node) or ast.unparse(node)
    return text.splitlines()[0]

  def scan(self, source: str, roots: list[ast.AST], cls: str | None) -> None:
    for root in roots:
      called = {id(node.func)
                for node in shallow(root) if isinstance(node, ast.Call)}
      for node in shallow(root):
        if not isinstance(node, ast.expr):
          continue
        found = self.target(node, cls)
        if found is not None:
          self.references.append({
            'from': source,
            'to': [found],
            'line': node.lineno,
            'source': 'ast',
          })
        table = self.scopes.tables[id(node)]
        if id(node) not in self.scopes.lazy and dynamic_use(
          node,
          self.dynamic_api,
          table,
          called,
        ):
          self.dynamic.append({
            'from': source,
            'line': node.lineno,
            'text': self.segment(node),
          })

  def collect_references(self) -> None:
    for owner in self.owners.values():
      self.scan(owner.name, [*owner.roots, *owner.decorators], owner.cls)

  def report(self) -> dict[str, Any]:
    return {
      'globals': self.report_globals(),
      'decorators': self.report_decorators(),
      'dynamic': unique(self.dynamic, DYNAMIC_ORDER),
    }


def owner_name(owner: Owner) -> str:
  return owner.name


def unique(
  items: list[dict[str, Any]],
  order: Callable[[dict[str, Any]], tuple[Any, ...]],
) -> list[dict[str, Any]]:
  seen: dict[str, dict[str, Any]] = {}
  for item in items:
    seen.setdefault(json.dumps(item, sort_keys=True), item)
  return sorted(seen.values(), key=order)


def inventory(
  path: Path,
  language: str,
  display: str,
  *,
  tree_sitter: bool,
  spec: str | None = None,
) -> dict[str, Any]:
  grammar = grammar_for(language, spec)
  data = path.read_bytes()
  result: dict[str, Any] = {
    'version': 1,
    'path': display,
    'language': language,
    'sha256': hashlib.sha256(data).hexdigest(),
    'lines': data.count(b'\n') + (0 if not data or data.endswith(b'\n') else 1),
    'syntax_errors': None,
    'declarations': None,
    'imports': None,
    'statements': None,
    'references': [],
    'python': None,
  }
  references: list[dict[str, Any]] = []
  text = python_text(data, display) if language == 'python' else None
  if tree_sitter:
    layer = TreeSitterLayer(grammar, data if text is None else text.encode('utf-8'))
    result['syntax_errors'] = layer.root.has_error
    result['declarations'] = layer.declarations
    result['imports'] = layer.imports
    result['statements'] = layer.statements
    references.extend(layer.references)
  if text is not None:
    python = PythonLayer(text, display)
    result['python'] = python.report()
    references.extend(python.references)
  result['references'] = unique(references, reference_order)
  return result


def parse_args(argv: list[str]) -> argparse.Namespace:
  parser = argparse.ArgumentParser(
    description='Print the JSON inventory of a source file for split planning.',
  )
  parser.add_argument(
    '--requirements',
    metavar='LANGUAGE',
    help='print the pinned pip requirements of a language and exit',
  )
  parser.add_argument('--display', help='path to print in the output, default: PATH')
  parser.add_argument(
    '--grammar',
    metavar='SPEC',
    help='grammar package for a language outside the table: '
    'tree-sitter-<name>==<version>[@<function>]',
  )
  parser.add_argument(
    '--no-tree-sitter',
    action='store_true',
    help='skip the tree-sitter layer, Python only',
  )
  parser.add_argument('path', nargs='?', type=Path, metavar='PATH')
  parser.add_argument('language', nargs='?', metavar='LANGUAGE')
  args = parser.parse_args(argv)
  if args.requirements is None and (args.path is None or args.language is None):
    parser.error('PATH and LANGUAGE are required without --requirements')
  return args


def output(args: argparse.Namespace) -> str:
  if args.requirements is not None:
    return '\n'.join(grammar_for(args.requirements, args.grammar).requirements())
  if args.no_tree_sitter and args.language != 'python':
    msg = '--no-tree-sitter works only for python'
    raise UsageError(msg)
  result = inventory(
    args.path,
    args.language,
    args.display or args.path.as_posix(),
    tree_sitter=not args.no_tree_sitter,
    spec=args.grammar,
  )
  return json.dumps(result, indent=2, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
  args = parse_args(sys.argv[1:] if argv is None else argv)
  try:
    text = output(args)
  except UsageError as error:
    sys.stderr.write(f'error: {error}\n')
    return 2
  except (OSError, UnicodeDecodeError, InventoryError) as error:
    sys.stderr.write(f'error: {error}\n')
    return 1
  sys.stdout.write(text + '\n')
  return 0


if __name__ == '__main__':
  sys.exit(main())
