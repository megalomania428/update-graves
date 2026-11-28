#!/usr/bin/env python3
# Check plan.json and the files of a split plan directory against the rules of
# references/plan-format.md; with --tree also the source and the files at base read
# with git, with --inventory the ownership and ranges of every declaration and
# top-level statement.

# cspell:ignore iterdir fullmatch nosec surrogateescape
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
# Path boundaries in the text of section Входит.
PATH_START = r'(?<![\w.@/-])(?:\./)*'
PATH_END = r'(?![\w@-]|\.[\w@/-])'
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


def bad_path(value: object, *, directory: bool = False) -> bool:
  # A directory entry of create ends with one / and covers every path under it.
  if directory and isinstance(value, str) and value.endswith('/'):
    value = value[:-1]
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


def inside(path: str, entry: str) -> bool:
  # Any entry may name a directory, so the paths under it belong to it.
  stem = entry.rstrip('/')
  return path.rstrip('/') == stem or path.startswith(stem + '/')


def overlaps(first: str, second: str) -> bool:
  return inside(first, second) or inside(second, first)


def touches(task: dict[str, Any], path: str) -> bool:
  return any(overlaps(path, entry) for entry in touched(task))


def holds(entry: str, path: str) -> bool:
  # In create only an entry with the trailing / is a directory.
  return path == entry or (entry.endswith('/') and inside(path, entry))


def stub_in(entry: str, stub: str) -> bool:
  # A stub is a file: it equals a plain entry or lies strictly under a directory.
  if entry.endswith('/'):
    return stub.startswith(entry) and stub != entry
  return stub == entry


def changes(task: dict[str, Any], path: str) -> bool:
  return any(inside(path, entry) for entry in task['modify'] + task['delete'])


def at_base(files: set[str], path: str) -> bool:
  # The path is at base as a file or as a directory with files.
  stem = path.rstrip('/')
  return stem in files or any(name.startswith(stem + '/') for name in files)


def under_file(files: set[str], path: str) -> str:
  # A path cannot be created below a file at base.
  parts = path.rstrip('/').split('/')
  for count in range(1, len(parts)):
    parent = '/'.join(parts[:count])
    if parent in files:
      return parent
  return ''


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


def own_prefix(path: str, own: set[str]) -> int:
  # Punctuation or markup may follow a path without a space, as in «path» or path:12.
  for item in own:
    stem = item.rstrip('/')
    if re.match(re.escape(stem) + '(?:/|' + PATH_END + ')', path):
      return len(stem)
  return 0


def without_own(text: str, own: set[str]) -> str:
  # The search splits paths at spaces, so own paths with spaces are cut out first.
  for path in sorted(own, key=len, reverse=True):
    if re.search(r'\s', path):
      stem = re.escape(path.rstrip('/'))
      text = re.sub(PATH_START + stem + PATH_END, ' ', text)
  return text


def mentioned(text: str, entry: str, own: set[str]) -> bool:
  # Any entry may be a directory: a path under it counts unless the task owns it.
  # A backslash separates path parts on Windows, so it counts as /.
  # A period without a path character after it ends the sentence, not the path.
  stem = re.escape(entry.rstrip('/'))
  pattern = re.compile(PATH_START + '(' + stem + r'(?:/[^\s`]*)?)' + PATH_END)
  text = without_own(text.replace('\\', '/'), own)
  match = pattern.search(text)
  while match:
    size = own_prefix(match.group(1), own)
    if not size:
      return True
    # The rest of the word after an own path may hold another path, as in a,b.
    match = pattern.search(text, match.start(1) + size)
  return False


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

  def _stubs(self) -> list[str]:
    return [stub for task in self.wave(0) for stub in task['stubs']]

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
    self._check_creates()
    self._check_wave_zero()
    self._check_wave_one()
    self._check_wave_two()
    self._check_symbols()
    if tree is not None:
      self._check_base(tree)
      self._check_timeline(tree)
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
        if bad_path(value, directory=key == 'create'):
          self.error(f'{name}: {key}: not a normalized relative path: {value!r}')
    for key in STRING_LISTS:
      for value in duplicates(task[key]):
        self.error(f'{name}: {key}: repeated {value}')
    for value in duplicates(task['create'] + task['modify'] + task['delete']):
      self.error(f'{name}: {value} is in more than one of create, modify, delete')
    self._check_task_overlaps(name, task)
    if not all(task['symbols']):
      self.error(f'{name}: symbols: empty name')

  def _check_task_overlaps(self, name: str, task: dict[str, Any]) -> None:
    paths = sorted(touched(task))
    for index, path in enumerate(paths):
      for other in paths[index + 1:]:
        if overlaps(path, other):
          self.error(f'{name}: {path} and {other} overlap in the same task')

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
    if min(counts) < 1:
      found = '/'.join(str(count) for count in counts)
      self.error(f'tasks: need at least one task in each wave, got {found}')

  def _check_dependencies(self) -> None:
    # Waves 0 and 2 are chains in id order, wave 1 runs after the whole wave 0.
    zero = [task['id'] for task in self.wave(0)]
    middle = [task['id'] for task in self.wave(1)]
    for task in self.tasks:
      task_id = task['id']
      wave = task['wave']
      wanted = sorted(zero)
      if wave != 1:
        chain = [other['id'] for other in self.wave(wave) if other['id'] < task_id]
        wanted = sorted(chain if wave == 0 else [*zero, *middle, *chain])
      if sorted(task['depends_on']) != wanted:
        listed = ', '.join(wanted) or 'nothing'
        self.error(f'{task_id}: depends_on must list {listed}')

  def _check_creates(self) -> None:
    seen: list[tuple[str, str, int]] = []
    for task in self.tasks:
      task_id = task['id']
      for path in task['create']:
        for other_id, other, wave in seen:
          # Wave 1 against wave 0 is reported by the wave 1 check.
          if overlaps(path, other) and (wave, task['wave']) != (0, 1):
            self.error(f'{task_id}: create {path} overlaps {other} created by {other_id}')
      seen.extend((task_id, path, task['wave']) for path in task['create'])

  def _check_wave_zero(self) -> None:
    source = self.plan['source']['path']
    root = self.plan['target']['root'] + '/'
    owners = [task['id'] for task in self.wave(0) if task['stubs']]
    if len(owners) != 1:
      found = ', '.join(owners) or 'none'
      self.error(f'wave 0: exactly one task must list stubs, got {found}')
    stubs: dict[str, str] = {}
    for task in self.wave(0):
      task_id = task['id']
      stubs.update(dict.fromkeys(task['stubs'], task_id))
      for stub in task['stubs']:
        if not any(stub_in(entry, stub) for entry in task['create']):
          self.error(f'{task_id}: stub {stub} is not in create')
        if not stub.startswith(root):
          self.error(f'{task_id}: stub {stub} is outside target.root')
      if touches(task, source):
        self.error(f'{task_id}: wave 0 must not touch {source}')
    self._check_stub_changes(stubs)

  def _check_stub_changes(self, stubs: dict[str, str]) -> None:
    for task in self.wave(0):
      task_id = task['id']
      for path in task['modify'] + task['delete']:
        for stub, owner in sorted(stubs.items()):
          # Only the tasks after the owner must keep the stubs intact.
          if task_id > owner and overlaps(stub, path):
            self.error(f'{task_id}: changes stub {stub} of {owner}')

  def _check_wave_one(self) -> None:
    entries: list[tuple[str, str]] = []
    changed: set[str] = set()
    for task in self.wave(1):
      self._check_wave_one_task(task)
      entries.extend((task['id'], path) for path in sorted(touched(task)))
      changed.update(touched(task))
    owners: dict[str, set[str]] = {}
    for task_id, path in entries:
      for other_id, other in entries:
        if other_id != task_id and overlaps(path, other):
          owners.setdefault(path, {task_id}).add(other_id)
    for path, task_ids in sorted(owners.items()):
      shared = ', '.join(sorted(task_ids))
      self.error(f'{path}: shared by wave 1 tasks {shared}')
    for stub in sorted(set(self._stubs()) - changed):
      self.error(f'{stub}: stub is not modified by any wave 1 task')

  def _check_wave_one_task(self, task: dict[str, Any]) -> None:
    task_id = task['id']
    source = self.plan['source']['path']
    stubs = self._stubs()
    handled = [path for other in self.wave(0) for path in touched(other)]
    for path in task['modify']:
      if path not in stubs:
        self.error(f'{task_id}: modifies {path}, which is not a wave 0 stub')
    for path in sorted(task['create']):
      if any(overlaps(path, other) for other in handled):
        self.error(f'{task_id}: creates {path}, which wave 0 already handles')
    if task['delete']:
      self.error(f'{task_id}: wave 1 must not delete files')
    if not task['symbols']:
      self.error(f'{task_id}: wave 1 needs symbols')
    if touches(task, source):
      self.error(f'{task_id}: wave 1 must not touch {source}')

  def _check_wave_two(self) -> None:
    for task in self.tasks:
      if task['wave'] != 0 and task['stubs']:
        task_id = task['id']
        self.error(f'{task_id}: only wave 0 may list stubs')
    source = self.plan['source']['path']
    changers = [task['id'] for task in self.wave(2) if changes(task, source)]
    if self.plan['mode'] == 'split' and len(changers) != 1:
      found = ', '.join(changers) or 'none'
      self.error(f'mode split: exactly one wave 2 task must change {source}, got {found}')
    for task in self.wave(2):
      if self.plan['mode'] == 'port' and touches(task, source):
        task_id = task['id']
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

  def _base_files(self, tree: Path) -> set[str] | None:
    git = shutil.which('git')
    if git is None:
      return None
    base = self.plan['base']
    result = subprocess.run(  # noqa: S603  # nosec B603
      [git, '-C', str(tree), 'ls-tree', '-r', '-z', '--name-only', base],
      capture_output=True,
      check=False,
    )
    if result.returncode != 0:
      reason = result.stderr.decode('utf-8', 'replace').strip()
      self.error(f'cannot list files at base {base}: {reason}')
      return None
    names = result.stdout.decode('utf-8', 'surrogateescape').split('\0')
    return set(filter(None, names))

  def _check_timeline(self, tree: Path) -> None:
    files = self._base_files(tree)
    if files is None:
      return
    for task in self.tasks:
      self._check_task_timeline(task, files)

  def _check_task_timeline(self, task: dict[str, Any], files: set[str]) -> None:
    # The tasks of depends_on change the paths of base in id order, so a later create
    # brings back a path that an earlier delete removed.
    task_id = task['id']
    present = set(files)
    gone: set[str] = set()
    for other in self.tasks:
      if other['id'] not in task['depends_on']:
        continue
      for path in other['delete']:
        present.difference_update([name for name in present if inside(name, path)])
        gone.add(path)
      for path in other['create']:
        gone.difference_update([name for name in gone if overlaps(name, path)])
        present.add(path)
    for path in task['create']:
      parent = under_file(files, path)
      if parent:
        self.error(f'{task_id}: creates {path} under file {parent} at base')
      elif at_base(files, path):
        self.error(f'{task_id}: creates {path}, which is already at base')
    for path in task['modify'] + task['delete']:
      found = any(holds(entry, path) or inside(entry, path) for entry in present)
      if not found or any(inside(path, entry) for entry in gone):
        self.error(f'{task_id}: changes {path}, which is not at base or in depends_on')

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
    foreign = {self.plan['source']['path']}
    for other in self.tasks:
      foreign.update(touched(other))
    for path in sorted(foreign):
      if any(inside(path, item) for item in own):
        continue
      if mentioned(files, path, own):
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
