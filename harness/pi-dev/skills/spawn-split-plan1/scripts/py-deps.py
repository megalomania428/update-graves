#!/usr/bin/env python3
# Inventory the top-level symbols of a Python module with their references, check a
# partition of the symbols into package modules by waves and list the module attributes
# that tests patch.

# cspell:ignore asname finalbody kwonlyargs orelse popitem posonlyargs setdefault vararg

from __future__ import annotations

import argparse
import ast
import copy
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from collections.abc import Iterator

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
WAVES = ('0', '1', '2')
PART_NAME = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
SCOPES = (*FUNCTIONS, ast.ClassDef, *COMPREHENSIONS)


class UsageError(Exception):
  pass


@dataclass
class Symbol:
  name: str
  kind: str
  start: int
  end: int
  deps: set[str] = field(default_factory=set)
  imports: set[str] = field(default_factory=set)
  writes: set[str] = field(default_factory=set)
  mutates: set[str] = field(default_factory=set)

  @property
  def lines(self) -> int:
    return self.end - self.start + 1


@dataclass
class Scope:
  bound: set[str]
  declared: set[str]
  is_class: bool = False


def stored_names(target: ast.AST) -> Iterator[str]:
  for node in ast.walk(target):
    if isinstance(node, ast.Name):
      yield node.id
    elif isinstance(node, ast.Starred) and isinstance(node.value, ast.Name):
      yield node.value.id


def shallow_walk(nodes: list[ast.stmt]) -> Iterator[ast.AST]:
  stack: list[ast.AST] = list(nodes)
  while stack:
    node = stack.pop()
    yield node
    if isinstance(node, SCOPES):
      continue
    stack.extend(ast.iter_child_nodes(node))


def body_bindings(body: list[ast.stmt]) -> tuple[set[str], set[str]]:
  bound: set[str] = set()
  declared: set[str] = set()
  for node in shallow_walk(body):
    if isinstance(node, (ast.Global, ast.Nonlocal)):
      declared.update(node.names)
    elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
      bound.add(node.id)
    elif isinstance(node, (*FUNCTIONS[:2], ast.ClassDef)):
      bound.add(node.name)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
      bound.update((alias.asname or alias.name).split('.')[0] for alias in node.names)
    elif isinstance(node, (ast.ExceptHandler, ast.MatchAs)) and node.name:
      bound.add(node.name)
  return bound - declared, declared


def argument_names(arguments: ast.arguments) -> set[str]:
  every = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
  every.extend(item for item in (arguments.vararg, arguments.kwarg) if item)
  return {item.arg
          for item in every}


class References(ast.NodeVisitor):
  def __init__(self, top: set[str]) -> None:
    self.top = top
    self.scopes: list[Scope] = []
    self.loads: set[str] = set()
    self.writes: set[str] = set()
    self.mutates: set[str] = set()

  def is_module_name(self, name: str) -> bool:
    if name not in self.top:
      return False
    for depth, scope in enumerate(reversed(self.scopes)):
      if scope.is_class and depth > 0:
        continue
      if name in scope.declared:
        return True
      if name in scope.bound:
        return False
    return True

  def enter(self, scope: Scope, nodes: list[ast.AST]) -> None:
    self.scopes.append(scope)
    for node in nodes:
      self.visit(node)
    self.scopes.pop()

  def visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
    for item in [*node.decorator_list, *node.args.defaults, *node.args.kw_defaults]:
      if item is not None:
        self.visit(item)
    notes = [item.annotation for item in ast.walk(node.args) if isinstance(item, ast.arg)]
    for item in [*notes, node.returns]:
      if item is not None:
        self.visit(item)
    bound, declared = body_bindings(node.body)
    self.enter(Scope(bound | argument_names(node.args), declared), list(node.body))

  visit_FunctionDef = visit_function  # noqa: N815
  visit_AsyncFunctionDef = visit_function  # noqa: N815

  def visit_Lambda(self, node: ast.Lambda) -> None:
    for item in [*node.args.defaults, *node.args.kw_defaults]:
      if item is not None:
        self.visit(item)
    self.enter(Scope(argument_names(node.args), set()), [node.body])

  def visit_ClassDef(self, node: ast.ClassDef) -> None:
    for item in [*node.decorator_list, *node.bases, *node.keywords]:
      self.visit(item)
    bound, declared = body_bindings(node.body)
    self.enter(Scope(bound, declared, is_class=True), list(node.body))

  def scoped_comprehension(self, node: ast.expr) -> None:
    generators: list[ast.comprehension] = getattr(node, 'generators', [])
    self.visit(generators[0].iter)
    # editorconfig-checker-disable
    bound = {name
             for item in generators
             for name in stored_names(item.target)}
    # editorconfig-checker-enable
    rest: list[ast.AST] = [generators[0].target, *generators[0].ifs]
    for item in generators[1:]:
      rest.extend([item.target, item.iter, *item.ifs])
    parts = ('elt', 'key', 'value')
    rest.extend(getattr(node, name) for name in parts if hasattr(node, name))
    self.enter(Scope(bound, set()), rest)

  visit_ListComp = scoped_comprehension  # noqa: N815
  visit_SetComp = scoped_comprehension  # noqa: N815
  visit_DictComp = scoped_comprehension  # noqa: N815
  visit_GeneratorExp = scoped_comprehension  # noqa: N815

  def visit_Name(self, node: ast.Name) -> None:
    if not self.is_module_name(node.id):
      return
    if isinstance(node.ctx, ast.Load):
      self.loads.add(node.id)
    elif self.scopes:
      self.writes.add(node.id)

  def mark_mutation(self, target: ast.expr) -> None:
    if isinstance(target, (ast.Subscript, ast.Attribute)):
      base = target.value
      if isinstance(base, ast.Name) and self.is_module_name(base.id):
        self.mutates.add(base.id)

  def visit_Call(self, node: ast.Call) -> None:
    if isinstance(node.func, ast.Attribute) and node.func.attr in MUTATORS:
      self.mark_mutation(node.func)
    self.generic_visit(node)

  def visit_Assign(self, node: ast.Assign) -> None:
    if self.scopes:
      for target in node.targets:
        self.mark_mutation(target)
    self.generic_visit(node)

  def visit_AugAssign(self, node: ast.AugAssign) -> None:
    if self.scopes:
      self.mark_mutation(node.target)
    self.generic_visit(node)

  def visit_Delete(self, node: ast.Delete) -> None:
    if self.scopes:
      for target in node.targets:
        self.mark_mutation(target)
    self.generic_visit(node)


def is_type_checking(test: ast.expr) -> bool:
  if isinstance(test, ast.Name):
    return test.id == 'TYPE_CHECKING'
  return isinstance(test, ast.Attribute) and test.attr == 'TYPE_CHECKING'


def is_main_guard(test: ast.expr) -> bool:
  if not isinstance(test, ast.Compare) or not isinstance(test.left, ast.Name):
    return False
  return test.left.id == '__name__'


def definitions(node: ast.stmt, *, typing: bool = False) -> Iterator[tuple[str, str]]:
  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
    yield node.name, 'def'
  elif isinstance(node, ast.ClassDef):
    yield node.name, 'class'
  elif isinstance(node, (ast.Import, ast.ImportFrom)):
    if isinstance(node, ast.ImportFrom) and node.module == '__future__':
      return
    for alias in node.names:
      name = (alias.asname or alias.name).split('.')[0]
      yield name, 'typing' if typing else 'import'
  elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
      for name in stored_names(target):
        yield name, 'const'


def nested_statements(node: ast.stmt) -> list[ast.stmt]:
  nested: list[ast.stmt] = []
  for name in ('body', 'orelse', 'finalbody'):
    nested.extend(getattr(node, name, []))
  for handler in getattr(node, 'handlers', []):
    nested.extend(handler.body)
  return nested


def top_statements(module: ast.Module) -> Iterator[tuple[ast.stmt, str, str]]:
  for node in module.body:
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
      continue
    if isinstance(node, ast.If) and is_main_guard(node.test):
      yield node, '__main__', 'main'
      continue
    found = list(definitions(node))
    if found:
      for name, kind in found:
        yield node, name, kind
      continue
    if isinstance(node, (ast.If, ast.Try, ast.With)):
      typing = isinstance(node, ast.If) and is_type_checking(node.test)
      inner = [(
        item,
        name,
        kind,
      ) for item in nested_statements(node) for name, kind in definitions(
        item,
        typing=typing,
      )]
      if inner:
        yield from inner
        continue
    if not isinstance(node, (ast.ImportFrom, ast.Import)):
      yield node, f'@{node.lineno}', 'stmt'


def inventory(path: Path) -> list[Symbol]:
  module = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
  found = list(top_statements(module))
  # editorconfig-checker-disable
  top = {name
         for _, name, _ in found}
  # editorconfig-checker-enable
  imported = {name
              for _, name, kind in found if kind in {'import', 'typing'}}
  symbols: list[Symbol] = []
  for node, name, kind in found:
    visitor = References(top)
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
      for item in (node.value, getattr(node, 'annotation', None)):
        if item is not None:
          visitor.visit(item)
    elif kind not in {'import', 'typing'}:
      visitor.visit(node)
    end = node.end_lineno or node.lineno
    start = first_line(node)
    symbols.append(
      Symbol(
        name,
        kind,
        start,
        end,
        (visitor.loads - imported) - {name},
        visitor.loads & imported,
        visitor.writes,
        visitor.mutates,
      ),
    )
  return symbols


def first_line(node: ast.stmt) -> int:
  decorators: list[ast.expr] = getattr(node, 'decorator_list', [])
  return min([node.lineno, *(item.lineno for item in decorators)])


def joined(names: set[str]) -> str:
  return ','.join(sorted(names)) or '-'


def state_of(symbol: Symbol) -> str:
  parts = [f'writes:{joined(symbol.writes)}'] if symbol.writes else []
  parts.extend([f'mutates:{joined(symbol.mutates)}'] if symbol.mutates else [])
  return ';'.join(parts) or '-'


def run_inventory(arguments: argparse.Namespace) -> int:
  out = sys.stdout
  out.write('name\tkind\tstart\tend\tlines\tdeps\timports\tstate\n')
  for item in inventory(Path(arguments.source)):
    out.write(
      f'{item.name}\t{item.kind}\t{item.start}\t{item.end}\t{item.lines}\t'
      f'{joined(item.deps)}\t{joined(item.imports)}\t{state_of(item)}\n',
    )
  return 0


def self_attributes(node: ast.AST) -> set[str]:
  return {
    item.attr
    for item in ast.walk(node) if isinstance(item, ast.Attribute)
    and isinstance(item.value, ast.Name) and item.value.id in {'self', 'cls'}
  }


def run_members(arguments: argparse.Namespace) -> int:
  path = Path(arguments.source)
  module = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
  # editorconfig-checker-disable
  top = {name
         for _, name, _ in top_statements(module)}
  # editorconfig-checker-enable
  classes = [
    node for node in module.body
    if isinstance(node, ast.ClassDef) and node.name == arguments.name
  ]
  if not classes:
    message = f'no top-level class {arguments.name} in {path}'
    raise UsageError(message)
  out = sys.stdout
  out.write('member\tstart\tend\tlines\tdeps\tself\n')
  for node in classes[0].body:
    found = [name for name, _ in definitions(node)]
    if not found:
      continue
    visitor = References(top)
    visitor.visit(node)
    end = node.end_lineno or node.lineno
    start = first_line(node)
    for name in found:
      out.write(
        f'{name}\t{start}\t{end}\t{end - start + 1}\t{joined(visitor.loads)}\t'
        f'{joined(self_attributes(node) - {name})}\n',
      )
  return 0


@dataclass
class Partition:
  part_of: dict[str, str] = field(default_factory=dict)
  wave_of: dict[str, str] = field(default_factory=dict)
  errors: list[str] = field(default_factory=list)

  def add(self, number: int, wave: str, part: str, name: str) -> None:
    where = f'partition:{number}'
    if wave not in WAVES:
      self.errors.append(f'{where}: bad wave {wave!r}')
    elif not PART_NAME.match(part):
      self.errors.append(f'{where}: bad part {part!r}')
    elif self.wave_of.setdefault(part, wave) != wave:
      self.errors.append(f'{where}: part {part} is in two waves')
    elif name in self.part_of:
      self.errors.append(f'{where}: {name} is assigned twice')
    else:
      self.part_of[name] = part

  def single_parts(self) -> None:
    for wave in ('0', '2'):
      parts = sorted(part for part, value in self.wave_of.items() if value == wave)
      if len(parts) > 1:
        self.errors.append(f'wave {wave} has several parts: {", ".join(parts)}')


def read_partition(path: Path) -> Partition:
  partition = Partition()
  for number, raw in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
    line = raw.strip()
    if not line or line.startswith('#'):
      continue
    fields = line.split('\t')
    if len(fields) != 3:  # noqa: PLR2004
      partition.errors.append(f'partition:{number}: expected wave, part and name')
      continue
    partition.add(number, *fields)
  partition.single_parts()
  return partition


@dataclass
class Unit:
  name: str
  lines: int
  deps: set[str]
  imports: set[str]
  writes: set[str]
  mutates: set[str]
  calls: set[str] = field(default_factory=set)


def unit_of(
  name: str,
  node: ast.AST,
  size: int,
  context: tuple[set[str], set[str]],
  members: set[str],
) -> Unit:
  top, imported = context
  visitor = References(top)
  visitor.visit(node)
  owner = name.split('.', 1)[0]
  # editorconfig-checker-disable
  calls = {f'{owner}.{item}'
           for item in self_attributes(node) & members} - {name}
  # editorconfig-checker-enable
  return Unit(
    name,
    size,
    visitor.loads - imported - {owner},
    visitor.loads & imported,
    visitor.writes,
    visitor.mutates,
    calls,
  )


def member_units(
  node: ast.ClassDef,
  split: set[str],
  context: tuple[set[str], set[str]],
) -> list[Unit]:
  # editorconfig-checker-disable
  members = {name
             for item in node.body
             for name, _ in definitions(item)}
  # editorconfig-checker-enable
  units: list[Unit] = []
  kept: list[ast.stmt] = []
  for item in node.body:
    # editorconfig-checker-disable
    names = {name
             for name, _ in definitions(item)}
    # editorconfig-checker-enable
    if not names & split:
      kept.append(item)
      continue
    size = (item.end_lineno or item.lineno) - item.lineno + 1
    units.extend(
      unit_of(f'{node.name}.{name}', item, size, context, members)
      for name in sorted(names)
    )
  header = copy.copy(node)
  header.body = kept or [ast.Pass()]
  size = 1 + sum((item.end_lineno or item.lineno) - item.lineno + 1 for item in kept)
  units.append(unit_of(node.name, header, size, context, members))
  return units


def check_units(path: Path, partition: Partition) -> tuple[list[Unit], list[str]]:
  module = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
  every = inventory(path)
  imported = {item.name
              for item in every if item.kind in {'import', 'typing'}}
  context = ({item.name
              for item in every}, imported)
  classes = {
    node.name: node
    for node in module.body if isinstance(node, ast.ClassDef)
  }
  errors: list[str] = []
  units: list[Unit] = []
  for item in every:
    if item.name in imported:
      continue
    prefix = f'{item.name}.'
    # editorconfig-checker-disable
    split = {name[len(prefix):]
             for name in partition.part_of if name.startswith(prefix)}
    # editorconfig-checker-enable
    if not split or item.name not in classes:
      units.append(
        Unit(
          item.name,
          item.lines,
          item.deps,
          item.imports,
          item.writes,
          item.mutates,
        ),
      )
      continue
    found = member_units(classes[item.name], split, context)
    # editorconfig-checker-disable
    known = {unit.name
             for unit in found}
    # editorconfig-checker-enable
    errors.extend(
      f'unknown member: {item.name}.{name}' for name in sorted(split)
      if f'{item.name}.{name}' not in known
    )
    units.extend(found)
  return units, errors


def target_part(name: str, partition: Partition) -> str | None:
  if name in partition.part_of:
    return partition.part_of[name]
  owner = name.split('.', 1)[0]
  return partition.part_of.get(owner) if owner != name else None


@dataclass
class Report:
  errors: list[str] = field(default_factory=list)
  notes: list[str] = field(default_factory=list)


def classify(wave: str, target_wave: str, mode: str) -> str:
  if wave == '2' or target_wave == '0':
    return 'ok'
  if wave == target_wave == '1' and mode == 'port':
    return 'contract'
  return 'error'


def check_edges(
  units: list[Unit],
  partition: Partition,
  mode: str,
  report: Report,
) -> None:
  for unit in units:
    part = target_part(unit.name, partition)
    if part is None:
      continue
    wave = partition.wave_of[part]
    for dep in sorted(unit.deps | unit.calls):
      target = target_part(dep, partition)
      if target is None or target == part:
        continue
      target_wave = partition.wave_of[target]
      line = f'{part}:{unit.name} (wave {wave}) uses {target}:{dep} (wave {target_wave})'
      verdict = classify(wave, target_wave, mode)
      if verdict == 'error':
        report.errors.append(f'edge: {line}')
      elif verdict == 'contract':
        report.notes.append(f'contract: {line}')
    for name in sorted(unit.writes | unit.mutates):
      owner = target_part(name, partition)
      if owner is None or owner == part:
        continue
      how = 'by rebinding' if name in unit.writes else 'in place'
      line = f'state: {part}:{unit.name} changes {owner}:{name} {how}'
      if name in unit.writes and mode == 'move':
        report.errors.append(line)
      else:
        report.notes.append(line)


def summary(units: list[Unit], partition: Partition) -> list[str]:
  sizes: dict[str, list[int]] = {}
  needs: dict[str, set[str]] = {}
  for unit in units:
    part = target_part(unit.name, partition)
    if part is None:
      continue
    size = sizes.setdefault(part, [0, 0])
    size[0] += 1
    size[1] += unit.lines
    needs.setdefault(part, set()).update(unit.imports)
  ordered = sorted(sizes.items(), key=lambda pair: (partition.wave_of[pair[0]], pair[0]))
  return [
    f'part\t{partition.wave_of[part]}\t{part}\t{count}\t{lines}\t{joined(needs[part])}'
    for part, (count, lines) in ordered
  ]


def run_check(arguments: argparse.Namespace) -> int:
  partition = read_partition(Path(arguments.partition))
  units, errors = check_units(Path(arguments.source), partition)
  report = Report(errors=[*partition.errors, *errors])
  # editorconfig-checker-disable
  names = {unit.name
           for unit in units}
  # editorconfig-checker-enable
  assigned = set(partition.part_of)
  missing = sorted(name for name in names - assigned if '.' not in name)
  unknown = sorted(name for name in assigned - names if name.split('.')[0] not in names)
  report.errors.extend(f'missing: {name}' for name in missing)
  report.errors.extend(f'unknown: {name}' for name in unknown)
  check_edges(units, partition, arguments.mode, report)
  out = sys.stdout
  out.write('kind\twave\tpart\tunits\tlines\timports\n')
  for line in summary(units, partition):
    out.write(line + '\n')
  for line in report.notes:
    out.write(f'note\t{line}\n')
  for line in report.errors:
    out.write(f'error\t{line}\n')
  return 1 if report.errors else 0


def dumps_by_name(path: Path) -> dict[str, list[str]]:
  module = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
  found: dict[str, list[str]] = {}
  for node, name, kind in top_statements(module):
    if kind in {'def', 'class', 'const'}:
      found.setdefault(name, []).append(ast.dump(node))
  return found


def run_same(arguments: argparse.Namespace) -> int:
  old = dumps_by_name(Path(arguments.old))
  new = dumps_by_name(Path(arguments.new))
  names = arguments.names or sorted(new)
  out = sys.stdout
  failed = 0
  for name in names:
    if name not in new:
      verdict = 'missing-in-new'
    elif name not in old:
      verdict = 'missing-in-old'
    else:
      verdict = 'same' if old[name] == new[name] else 'differs'
    failed += verdict != 'same'
    out.write(f'{verdict}\t{name}\n')
  return 1 if failed else 0


def patched_name(node: ast.Call) -> tuple[str, str] | None:
  func = node.func
  name = func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', '')
  texts = [item.value for item in node.args if isinstance(item, ast.Constant)]
  strings = [text for text in texts if isinstance(text, str)]
  if name == 'object' and len(node.args) > 1 and strings:
    return strings[0], 'patch.object'
  if name == 'setattr' and len(node.args) > 1:
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
      return first.value.rsplit('.', 1)[-1], 'setattr'
    if strings:
      return strings[0], 'setattr'
  head = node.args[0] if node.args else None
  if name == 'patch' and isinstance(head, ast.Constant) and isinstance(head.value, str):
    return head.value.rsplit('.', 1)[-1], 'patch'
  return None


def patched_in(path: Path) -> Iterator[tuple[int, str, str]]:
  module = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
  for node in ast.walk(module):
    if isinstance(node, ast.Call):
      found = patched_name(node)
      if found:
        yield node.lineno, *found
    elif isinstance(node, ast.Assign):
      for target in node.targets:
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Attribute):
          yield node.lineno, target.attr, 'assign'


def run_patches(arguments: argparse.Namespace) -> int:
  symbols = inventory(Path(arguments.source))
  kinds = {
    item.name: item.kind
    for item in symbols
  }
  module_of: dict[str, str] = {}
  if arguments.partition:
    partition = read_partition(Path(arguments.partition))
    if partition.errors:
      message = '; '.join(partition.errors)
      raise UsageError(message)
    module_of = partition.part_of
  out = sys.stdout
  out.write('place\tname\tkind\tform\tmodule\n')
  for test in arguments.tests:
    for line, name, form in patched_in(Path(test)):
      if name in kinds:
        out.write(
          f'{test}:{line}\t{name}\t{kinds[name]}\t{form}\t'
          f'{module_of.get(name, "-")}\n',
        )
  return 0


def parser() -> argparse.ArgumentParser:
  root = argparse.ArgumentParser(description='Symbols of a Python module split by waves')
  commands = root.add_subparsers(dest='command', required=True)
  listing = commands.add_parser('inventory', help='top-level symbols with references')
  listing.add_argument('source')
  listing.set_defaults(run=run_inventory)
  members = commands.add_parser('members', help='members of a top-level class')
  members.add_argument('source')
  members.add_argument('name')
  members.set_defaults(run=run_members)
  checking = commands.add_parser('check', help='check a partition into modules')
  checking.add_argument('--mode', choices=('move', 'port'), default='move')
  checking.add_argument('source')
  checking.add_argument('partition')
  checking.set_defaults(run=run_check)
  same = commands.add_parser('same', help='compare the syntax trees of moved symbols')
  same.add_argument('old')
  same.add_argument('new')
  same.add_argument('names', nargs='*')
  same.set_defaults(run=run_same)
  patching = commands.add_parser('patches', help='module attributes patched by tests')
  patching.add_argument('--partition')
  patching.add_argument('source')
  patching.add_argument('tests', nargs='+')
  patching.set_defaults(run=run_patches)
  return root


def main() -> int:
  arguments = parser().parse_args()
  try:
    return int(arguments.run(arguments))
  except (OSError, SyntaxError, UnicodeDecodeError, UsageError) as error:
    sys.stderr.write(f'error: {error}\n')
    return 2


if __name__ == '__main__':
  sys.exit(main())
