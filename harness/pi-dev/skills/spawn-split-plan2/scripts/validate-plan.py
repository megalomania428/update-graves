#!/usr/bin/env python3
# Check plan.json and the files of a split plan directory against the rules of
# references/plan-format.md; with --inventory and --tree also the source at base read
# with git and the ownership and ranges of every declaration and top-level statement.

# cspell:ignore iterdir fullmatch nosec
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess  # noqa: S404  # nosec B404
import sys
from pathlib import Path
from typing import Any

PLAN_KEYS = frozenset({'version', 'mode', 'source', 'target', 'base', 'skip', 'tasks'})
SOURCE_KEYS = frozenset({'path', 'language', 'sha256'})
TARGET_KEYS = frozenset({'language', 'root'})
TASK_KEYS = frozenset({
  'id',
  'wave',
  'depends_on',
  'create',
  'modify',
  'delete',
  'symbols',
  'stubs',
})
PATH_LISTS = ('create', 'modify', 'delete', 'stubs')
STRING_LISTS = ('depends_on', 'symbols', *PATH_LISTS)
ID_RE = re.compile(r'(?!00-)\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*')
LANGUAGE_RE = re.compile(r'[a-z][a-z0-9_+#-]*')
BASE_RE = re.compile(r'[0-9a-f]{40}|[0-9a-f]{64}')
SHA256_RE = re.compile(r'[0-9a-f]{64}')
FENCE_RE = re.compile(r' {0,3}(?:```|~~~)')
# Top-level list items of section Входит; each one is `- path` or `- \`path\``, either
# followed by text after a space.
LIST_RE = re.compile(r'(?:[-*+]|\d+[.)])(?:\s|$)')
ITEM_RE = re.compile(r'- (?:`([^`]+)`|([^\s`]+))(?:\s.*)?')
SECTIONS = (
  'Волна',
  'Предпосылки',
  'Результат',
  'Входит',
  'Не входит',  # ruff: ignore[ambiguous-unicode-character-string]
  'Контракт',
  'Исходные символы',
  'Тесты',
  'Приёмка',
  'Типовые ответы',
)
FILES_SECTION = 'Входит'
SYMBOLS_SECTION = 'Исходные символы'
OVERVIEW = '00-overview.md'
MANIFEST = 'plan.json'
WAVES = (0, 1, 2)


class UsageError(Exception):
  pass


def bad_path(value: object) -> bool:
  if not isinstance(value, str) or not value or '\\' in value:
    return True
  if value.startswith('/') or value.endswith('/'):
    return True
  if any(char < ' ' for char in value):
    return True
  return any(part in {'', '.', '..'} for part in value.split('/'))


def string_list(value: object) -> bool:
  return isinstance(value, list) and all(isinstance(item, str) for item in value)


def duplicates(values: list[str]) -> list[str]:
  seen: set[str] = set()
  repeated: set[str] = set()
  for value in values:
    if value in seen:
      repeated.add(value)
    seen.add(value)
  return sorted(repeated)


def touched(task: dict[str, Any]) -> set[str]:
  return set(task['create'] + task['modify'] + task['delete'])


def exact_keys(name: str, value: object, keys: frozenset[str]) -> list[str]:
  if not isinstance(value, dict):
    return [f'{name}: must be an object']
  found = set(value)
  problems = [f'{name}: missing key {key}' for key in sorted(keys - found)]
  problems.extend(f'{name}: unknown key {key}' for key in sorted(found - keys))
  return problems


def sections(text: str) -> tuple[list[str], dict[str, str]]:
  titles: list[str] = []
  bodies: dict[str, list[str]] = {}
  fenced = False
  for line in text.splitlines():
    if FENCE_RE.match(line):
      fenced = not fenced
    if not fenced and line.startswith('## '):
      titles.append(line[3:].strip())
      bodies.setdefault(titles[-1], [])
    elif titles:
      bodies[titles[-1]].append(line)
  return titles, {
    title: '\n'.join(lines)
    for title, lines in bodies.items()
  }


def mentions(text: str, word: str) -> bool:
  # A path counts also with the `./` prefix, but not inside a longer path.
  pattern = r'(?<![\w.@/-])(?:\./)*' + re.escape(word) + r'(?![\w.@/-])'
  return re.search(pattern, text) is not None


def has_lines(item: object) -> bool:
  if not isinstance(item, dict) or not isinstance(item.get('name'), str):
    return False
  if not isinstance(item.get('parent'), (str, type(None))):
    return False
  lines = (item.get('start_line'), item.get('end_line'))
  return all(isinstance(line, int) and not isinstance(line, bool) for line in lines)


def load_inventory(path: Path) -> dict[str, Any]:
  try:
    data = json.loads(path.read_text(encoding='utf-8'))
  except (OSError, UnicodeDecodeError, ValueError) as error:
    msg = f'inventory {path}: {error}'
    raise UsageError(msg) from error
  if not isinstance(data, dict) or not isinstance(data.get('declarations'), list):
    msg = f'inventory {path}: no declarations, run inventory.sh with tree-sitter'
    raise UsageError(msg)
  if not isinstance(data.get('statements'), list):
    msg = f'inventory {path}: no statements, run inventory.sh with tree-sitter'
    raise UsageError(msg)
  for item in data['declarations'] + data['statements']:
    if not has_lines(item):
      msg = f'inventory {path}: entry without a name or lines: {item!r}'
      raise UsageError(msg)
  return data


class Validator:
  def __init__(self, plan_dir: Path) -> None:
    self.dir = plan_dir
    self.errors: list[str] = []
    self.plan: dict[str, Any] = {}
    self.tasks: list[dict[str, Any]] = []
    self.texts: dict[str, str] = {}
    self.ranges: dict[str, list[str]] = {}

  def error(self, message: str) -> None:
    self.errors.append(message)

  def wave(self, number: int) -> list[dict[str, Any]]:
    return [task for task in self.tasks if task['wave'] == number]

  def run(self, inventory: dict[str, Any] | None, tree: Path | None = None) -> None:
    if not self._load() or not self._check_top():
      return
    for index, task in enumerate(self.plan['tasks']):
      self._check_task(index, task)
    if len(self.tasks) != len(self.plan['tasks']):
      return
    self._check_order()
    if any(not self.wave(number) for number in WAVES):
      return
    self._check_dependencies()
    self._check_wave_zero()
    self._check_wave_one()
    self._check_wave_two()
    self._check_symbols()
    if tree is not None:
      self._check_base(tree)
    if inventory is not None:
      self._check_inventory(inventory)
    self._check_documents()

  def _load(self) -> bool:
    try:
      data = json.loads((self.dir / MANIFEST).read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError) as error:
      self.error(f'{MANIFEST}: {error}')
      return False
    problems = exact_keys(MANIFEST, data, PLAN_KEYS)
    for problem in problems:
      self.error(problem)
    if problems:
      return False
    self.plan = data
    return True

  def _check_top(self) -> bool:
    plan = self.plan
    version = plan['version']
    mode = plan['mode']
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
      self.error(f'version: must be 1, got {version!r}')
    if not isinstance(mode, str) or mode not in {'split', 'port'}:
      self.error(f'mode: must be split or port, got {mode!r}')
    if not isinstance(plan['base'], str) or not BASE_RE.fullmatch(plan['base']):
      self.error('base: must be a full lowercase commit hash')
    if not string_list(plan['skip']) or not all(plan['skip']):
      self.error('skip: must be a list of non-empty strings')
    for problem in exact_keys('source', plan['source'], SOURCE_KEYS):
      self.error(problem)
    for problem in exact_keys('target', plan['target'], TARGET_KEYS):
      self.error(problem)
    if not isinstance(plan['tasks'], list) or not plan['tasks']:
      self.error('tasks: must be a non-empty list')
    if self.errors:
      return False
    sha256 = plan['source']['sha256']
    if not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256):
      self.error('source.sha256: must be a lowercase SHA256 hex digest')
    self._check_languages()
    return not self.errors

  def _check_languages(self) -> None:
    source = self.plan['source']
    target = self.plan['target']
    for name, value in (
      ('source.language', source['language']),
      ('target.language', target['language']),
    ):
      if not isinstance(value, str) or not LANGUAGE_RE.fullmatch(value):
        self.error(f'{name}: must be a lowercase language key, got {value!r}')
    for name, value in (('source.path', source['path']), ('target.root', target['root'])):
      if bad_path(value):
        self.error(f'{name}: not a normalized relative path: {value!r}')
    same = source['language'] == target['language']
    if self.plan['mode'] == 'split' and not same:
      self.error('mode split: target.language must equal source.language')
    if self.plan['mode'] == 'port' and same:
      self.error('mode port: target.language must differ from source.language')

  def _check_task(self, index: int, task: object) -> None:
    problems = exact_keys(f'tasks[{index}]', task, TASK_KEYS)
    for problem in problems:
      self.error(problem)
    if problems or not isinstance(task, dict):
      return
    count = len(self.errors)
    name = self._check_task_types(index, task)
    if len(self.errors) == count:
      self._check_task_lists(name, task)
    if len(self.errors) == count:
      self.tasks.append(task)

  def _check_task_types(self, index: int, task: dict[str, Any]) -> str:
    name = f'tasks[{index}]'
    task_id = task['id']
    if isinstance(task_id, str) and ID_RE.fullmatch(task_id):
      name = task_id
    else:
      self.error(f'{name}: bad id {task_id!r}')
    wave = task['wave']
    if not isinstance(wave, int) or isinstance(wave, bool) or wave not in WAVES:
      self.error(f'{name}: wave must be 0, 1 or 2, got {wave!r}')
    for key in STRING_LISTS:
      if not string_list(task[key]):
        self.error(f'{name}: {key} must be a list of strings')
    return name

  def _check_task_lists(self, name: str, task: dict[str, Any]) -> None:
    for key in PATH_LISTS:
      for value in task[key]:
        if bad_path(value):
          self.error(f'{name}: {key}: not a normalized relative path: {value!r}')
    for key in STRING_LISTS:
      for value in duplicates(task[key]):
        self.error(f'{name}: {key}: repeated {value}')
    for value in duplicates(task['create'] + task['modify'] + task['delete']):
      self.error(f'{name}: {value} is in more than one of create, modify, delete')
    if not all(task['symbols']):
      self.error(f'{name}: symbols: empty name')

  def _check_order(self) -> None:
    ids = [task['id'] for task in self.tasks]
    for value in duplicates([task_id[:2] for task_id in ids]):
      self.error(f'tasks: number {value} is used more than once')
    if ids != sorted(ids):
      self.error('tasks: must be sorted by id')
    waves = [task['wave'] for task in self.tasks]
    if waves != sorted(waves):
      self.error('tasks: waves must not decrease along the id order')
    counts = [len(self.wave(number)) for number in WAVES]
    if counts[0] != 1 or counts[1] < 1 or counts[2] != 1:
      found = '/'.join(str(count) for count in counts)
      self.error(f'tasks: need waves 1/at least 1/1, got {found}')

  def _check_dependencies(self) -> None:
    first = self.wave(0)[0]['id']
    middle = [task['id'] for task in self.wave(1)]
    expected: dict[int, list[str]] = {
      0: [],
      1: [first],
      2: sorted([first, *middle]),
    }
    for task in self.tasks:
      wanted = expected[task['wave']]
      if sorted(task['depends_on']) != wanted:
        listed = ', '.join(wanted) or 'nothing'
        task_id = task['id']
        self.error(f'{task_id}: depends_on must list {listed}')

  def _check_wave_zero(self) -> None:
    task = self.wave(0)[0]
    task_id = task['id']
    source = self.plan['source']['path']
    root = self.plan['target']['root'] + '/'
    for stub in task['stubs']:
      if stub not in task['create']:
        self.error(f'{task_id}: stub {stub} is not in create')
      if not stub.startswith(root):
        self.error(f'{task_id}: stub {stub} is outside target.root')
    if source in touched(task):
      self.error(f'{task_id}: wave 0 must not touch {source}')

  def _check_wave_one(self) -> None:
    first = self.wave(0)[0]
    owners: dict[str, list[str]] = {}
    for task in self.wave(1):
      self._check_wave_one_task(task, first)
      for path in sorted(touched(task)):
        owners.setdefault(path, []).append(task['id'])
    for path, task_ids in sorted(owners.items()):
      if len(task_ids) > 1:
        shared = ', '.join(task_ids)
        self.error(f'{path}: shared by wave 1 tasks {shared}')
    for stub in sorted(set(first['stubs']) - set(owners)):
      self.error(f'{stub}: stub is not modified by any wave 1 task')

  def _check_wave_one_task(self, task: dict[str, Any], first: dict[str, Any]) -> None:
    task_id = task['id']
    source = self.plan['source']['path']
    for path in task['modify']:
      if path not in first['stubs']:
        self.error(f'{task_id}: modifies {path}, which is not a wave 0 stub')
    for path in sorted(set(task['create']) & touched(first)):
      self.error(f'{task_id}: creates {path}, which wave 0 already handles')
    if task['delete']:
      self.error(f'{task_id}: wave 1 must not delete files')
    if not task['symbols']:
      self.error(f'{task_id}: wave 1 needs symbols')
    if source in touched(task):
      self.error(f'{task_id}: wave 1 must not touch {source}')

  def _check_wave_two(self) -> None:
    for task in self.tasks:
      if task['wave'] != 0 and task['stubs']:
        task_id = task['id']
        self.error(f'{task_id}: only wave 0 may list stubs')
    task = self.wave(2)[0]
    task_id = task['id']
    source = self.plan['source']['path']
    changed = source in task['modify'] or source in task['delete']
    if self.plan['mode'] == 'split' and not changed:
      self.error(f'{task_id}: mode split needs {source} in modify or delete')
    if self.plan['mode'] == 'port' and source in touched(task):
      self.error(f'{task_id}: mode port must not touch {source}')

  def _entries(self) -> list[str]:
    found = list(self.plan['skip'])
    for task in self.tasks:
      found.extend(task['symbols'])
    return found

  def _check_symbols(self) -> None:
    for value in duplicates(self._entries()):
      self.error(f'symbol {value} has more than one owner')

  def _read_documents(self) -> None:
    expected = {OVERVIEW, MANIFEST, *(task['id'] + '.md' for task in self.tasks)}
    # editorconfig-checker-disable
    present = {entry.name
               for entry in self.dir.iterdir()}
    # editorconfig-checker-enable
    for name in sorted(present - expected):
      self.error(f'{name}: unexpected file in the plan directory')
    for name in sorted(expected - {MANIFEST}):
      path = self.dir / name
      if not path.is_file():
        self.error(f'{name}: missing')
        continue
      text = path.read_text(encoding='utf-8', errors='replace')
      if text.strip():
        self.texts[name] = text
      else:
        self.error(f'{name}: empty')

  def _check_documents(self) -> None:
    self._read_documents()
    overview = self.texts.get(OVERVIEW)
    for task in self.tasks:
      name = task['id'] + '.md'
      if overview is not None and name not in overview:
        self.error(f'{OVERVIEW}: does not mention {name}')
      text = self.texts.get(name)
      if text is not None:
        self._check_task_document(name, text, task)

  def _check_base(self, tree: Path) -> None:
    path = self.plan['source']['path']
    base = self.plan['base']
    git = shutil.which('git')
    if git is None:
      self.error('git not found, cannot read the source at base')
      return
    result = subprocess.run(  # noqa: S603  # nosec B603
      [git, '-C', str(tree), 'show', f'{base}:{path}'],
      capture_output=True,
      check=False,
    )
    if result.returncode != 0:
      reason = result.stderr.decode('utf-8', 'replace').strip()
      self.error(f'cannot read {path} at base {base}: {reason}')
      return
    digest = hashlib.sha256(result.stdout).hexdigest()
    if digest != self.plan['source']['sha256']:
      self.error(f'source.sha256 does not match {path} at base: {digest}')

  def _check_files_section(self, name: str, files: str, task: dict[str, Any]) -> None:
    own = touched(task)
    listed: set[str] = set()
    for line in files.splitlines():
      if not LIST_RE.match(line):
        continue
      item = ITEM_RE.fullmatch(line)
      if item is None:
        self.error(f'{name}: section {FILES_SECTION} item is not - path: {line}')
        continue
      path = item.group(1) or item.group(2)
      listed.add(path)
      if path not in own:
        self.error(f'{name}: section {FILES_SECTION} lists {path} outside the task')
    for path in sorted(own - listed):
      self.error(f'{name}: section {FILES_SECTION} does not list {path}')
    # editorconfig-checker-disable
    foreign = {path
               for other in self.tasks
               for path in touched(other)}
    # editorconfig-checker-enable
    foreign.add(self.plan['source']['path'])
    for path in sorted(foreign - own):
      if mentions(files, path):
        self.error(f'{name}: section {FILES_SECTION} mentions {path} outside the task')

  def _check_task_document(self, name: str, text: str, task: dict[str, Any]) -> None:
    titles, bodies = sections(text)
    if titles != list(SECTIONS):
      expected = ', '.join(SECTIONS)
      found = ', '.join(titles) or 'none'
      self.error(f'{name}: sections must be {expected} in this order, got {found}')
      return
    self._check_files_section(name, bodies[FILES_SECTION], task)
    symbols = bodies[SYMBOLS_SECTION]
    for symbol in task['symbols']:
      if not mentions(symbols, symbol):
        self.error(f'{name}: section {SYMBOLS_SECTION} does not mention {symbol}')
      for lines in self.ranges.get(symbol, []):
        if not mentions(symbols, lines):
          self.error(f'{name}: section {SYMBOLS_SECTION} lacks range {lines}')

  def _check_inventory(self, inventory: dict[str, Any]) -> None:
    source = self.plan['source']
    path = source['path']
    found = inventory.get('path')
    if found != path:
      self.error(f'inventory path {found!r} is not {path}')
    language = source['language']
    if inventory.get('language') != language:
      self.error(f'inventory language is not {language}')
    digest = inventory.get('sha256')
    if digest != source['sha256']:
      self.error(f'inventory sha256 does not match source.sha256: {digest!r}')
    if inventory.get('syntax_errors') is not False:
      self.error('inventory has syntax errors or none recorded, use a manual inventory')
    items = inventory['declarations'] + inventory['statements']
    for item in items:
      start = item['start_line']
      end = item['end_line']
      self.ranges.setdefault(item['name'], []).append(f'{path}:{start}-{end}')
    names = set(self.ranges)
    # A member is owned with its parent only when the parent is a declaration too.
    tops = {item['name']
            for item in items if item.get('parent') not in names}
    entries = set(self._entries())
    for name in sorted(entries - names):
      self.error(f'symbol {name} is not a declaration or statement of the inventory')
    for name in sorted(tops - entries):
      self.error(f'declaration {name} has no owner')
    self._check_shared(inventory['declarations'])

  def _check_shared(self, declarations: list[dict[str, Any]]) -> None:
    # Definitions inside one top-level statement share its range and its owner.
    owners: dict[str, str] = dict.fromkeys(self.plan['skip'], 'skip')
    for task in self.tasks:
      owners.update(dict.fromkeys(task['symbols'], task['id']))
    groups: dict[tuple[int, int], set[str]] = {}
    for item in declarations:
      if item.get('parent') is None:
        key = (item['start_line'], item['end_line'])
        groups.setdefault(key, set()).add(item['name'])
    path = self.plan['source']['path']
    for (start, end), names in sorted(groups.items()):
      if len({owners.get(name)
              for name in names}) > 1:
        listed = ', '.join(sorted(names))
        self.error(f'declarations {listed} share {path}:{start}-{end} and need one owner')


def parse_args(argv: list[str]) -> argparse.Namespace:
  parser = argparse.ArgumentParser(description='Check a split plan directory.')
  parser.add_argument('plan_dir', type=Path, metavar='PLAN_DIR')
  parser.add_argument('--inventory', type=Path, help='inventory.json of the source')
  parser.add_argument(
    '--tree',
    type=Path,
    help='git work tree of the source to read it at base; required with --inventory',
  )
  args = parser.parse_args(argv)
  if args.inventory is not None and args.tree is None:
    parser.error('--inventory needs --tree')
  return args


def main(argv: list[str] | None = None) -> int:
  args = parse_args(sys.argv[1:] if argv is None else argv)
  if not args.plan_dir.is_dir():
    sys.stderr.write(f'error: not a directory: {args.plan_dir}\n')
    return 2
  if args.tree is not None and not args.tree.is_dir():
    sys.stderr.write(f'error: not a directory: {args.tree}\n')
    return 2
  try:
    inventory = None if args.inventory is None else load_inventory(args.inventory)
  except UsageError as error:
    sys.stderr.write(f'error: {error}\n')
    return 2
  validator = Validator(args.plan_dir)
  validator.run(inventory, args.tree)
  if validator.errors:
    sys.stderr.write(''.join(f'error: {problem}\n' for problem in validator.errors))
    return 1
  counts = '/'.join(str(len(validator.wave(number))) for number in WAVES)
  sys.stdout.write(f'ok: {len(validator.tasks)} tasks (waves {counts})\n')
  return 0


if __name__ == '__main__':
  sys.exit(main())
