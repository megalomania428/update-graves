#!/usr/bin/env python3
# Convert the pi-web session archive between the flat layout (archived-sessions.json
# with archived-sessions/) and the archived-projects/ tree with monthly summaries.

# cspell:ignore jsonl nosec noqa errno EXDEV EPERM ENOTSUP copyfileobj copystat getpid
# cspell:ignore fromisoformat fromtimestamp rmdir iterdir mtime
# cspell:ignore ERRNOS unflushed RDWR RDONLY NTFS EINVAL EISDIR
from __future__ import annotations

import argparse
import errno
import filecmp
import json
import os
import re
import shutil
import stat
import subprocess  # ruff: ignore[suspicious-subprocess-import]  # nosec B404
import sys
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from collections.abc import Generator, Iterator

INDEX_NAME = 'archived-sessions.json'
FLAT_DIR_NAME = 'archived-sessions'
TREE_DIR_NAME = 'archived-projects'
SUMMARY_NAME = 'summary.json'
SUMMARY_VERSION = 1
MS_PER_SECOND = 1000
NS_PER_MS = 1_000_000
UUID_V7 = re.compile(
  r'^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
  re.IGNORECASE,
)
FILE_TIME = re.compile(r'^(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})-(\d{3})Z_')
YEAR_DIR = re.compile(r'^\d{4}$')
MONTH_DIR = re.compile(r'^\d{2}$')
LINE_BREAKS = re.compile(r'[\r\n]+')
LINK_FALLBACK_ERRNOS = frozenset({errno.EXDEV, errno.EPERM, errno.ENOTSUP})
NAME_SYSTEM_PROMPT = (
  'Generate a concise title for a coding-agent chat session. '
  'Return only the title, with no quotes or punctuation wrapper.'
)
NAME_USER_PROMPT = 'Create a 2-6 word title for this request:\n\n'
NAME_INPUT_LIMIT = 4000
NAME_LENGTH_LIMIT = 60
ENTRY_ID_ATTEMPTS = 100
ELLIPSIS = '\u2026'
PLACED = frozenset({'moved', 'present'})


def say(message: str) -> None:
  sys.stdout.write(message + '\n')
  sys.stdout.flush()


def absolute(path: Path) -> Path:
  return Path(os.path.normpath(Path.cwd() / path))


def default_data_dir() -> Path:
  configured = os.environ.get('PI_WEB_DATA_DIR', '')
  if not configured:
    return Path.home() / '.pi-web'
  return absolute(Path(configured))


def canonical_cwd(cwd: str) -> str:
  if not cwd.startswith('/'):
    return cwd
  return '/' + os.path.normpath(cwd).lstrip('/')


def project_dir_name(cwd: str) -> str:
  return re.sub(r'[/\\:]', '-', re.sub(r'^[/\\]', '', cwd)) + '--'


def env_value(*keys: str) -> str | None:
  for key in keys:
    value = os.environ.get(key, '')
    if value:
      return value
  return None


def expand_tilde(value: str) -> str:
  if value == '~':
    return str(Path.home())
  if value.startswith('~/'):
    return str(Path.home() / value[2:])
  return value


def read_json_object(path: Path) -> dict[str, object]:
  try:
    data = json.loads(path.read_text(encoding='utf-8-sig'))
  except (OSError, ValueError):
    return {}
  return data if isinstance(data, dict) else {}


def pi_web_config_path() -> Path:
  configured = os.environ.get('PI_WEB_CONFIG', '')
  if configured:
    return absolute(Path(configured))
  xdg = os.environ.get('XDG_CONFIG_HOME', '')
  base = Path(xdg) if xdg else Path.home() / '.config'
  return base / 'pi-web' / 'config.json'


@cache
def agent_dir() -> Path:
  # Same order as PI WEB: the deprecated alias, pi's variable, PI WEB config, default.
  configured = env_value('PI_WEB_AGENT_DIR', 'PI_CODING_AGENT_DIR')
  if configured is None:
    agent = read_json_object(pi_web_config_path()).get('agent')
    directory = text_of(agent.get('dir')) if isinstance(agent, dict) else None
    configured = (directory or '').strip() or None
  return absolute(Path(expand_tilde(configured or '~/.pi/agent')))


def settings_session_dir(cwd: str) -> str | None:
  # Project settings override the settings of the agent directory, as in pi.
  found = None
  paths = [agent_dir() / 'settings.json']
  if Path(cwd).is_absolute():
    paths.append(Path(cwd) / '.pi' / 'settings.json')
  for path in paths:
    configured = text_of(read_json_object(path).get('sessionDir'))
    found = found if configured is None else configured
  return found or None


@cache
def default_session_dir(cwd: str) -> Path:
  # The session directory of a project as PI WEB resolves it: the environment, the
  # settings of pi, else the project directory under the sessions of the agent.
  configured = env_value('PI_WEB_AGENT_SESSION_DIR', 'PI_CODING_AGENT_SESSION_DIR')
  configured = configured or settings_session_dir(cwd)
  if configured is not None:
    return absolute(Path(cwd) / expand_tilde(configured))
  return agent_dir() / 'sessions' / ('--' + project_dir_name(cwd))


def text_of(value: object) -> str | None:
  return value if isinstance(value, str) else None


def iso_ms(moment_ms: int) -> str:
  seconds, millis = divmod(moment_ms, MS_PER_SECOND)
  moment = datetime.fromtimestamp(seconds, tz=timezone.utc)
  return f'{moment:%Y-%m-%dT%H:%M:%S}.{millis:03d}Z'


def now_iso() -> str:
  return iso_ms(time.time_ns() // NS_PER_MS)


def mtime_iso(path: Path) -> str:
  return iso_ms(path.stat().st_mtime_ns // NS_PER_MS)


def parse_iso_ms(value: object) -> int | None:
  if not isinstance(value, str) or not value:
    return None
  try:
    moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
  except ValueError:
    return None
  if moment.tzinfo is None:
    moment = moment.replace(tzinfo=timezone.utc)
  return round(moment.timestamp() * MS_PER_SECOND)


def created_ms(session_id: str, file_name: str) -> int | None:
  if UUID_V7.match(session_id):
    return int(session_id[:8] + session_id[9:13], 16)
  match = FILE_TIME.match(file_name)
  if match is None:
    return None
  year, month, day, hour, minute, second, millis = (int(part) for part in match.groups())
  try:
    moment = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
  except ValueError:
    return None
  return int(moment.timestamp()) * MS_PER_SECOND + millis


def parse_json_line(raw: bytes) -> dict[str, object] | None:
  try:
    value = json.loads(raw)
  except ValueError:
    return None
  return value if isinstance(value, dict) else None


def read_header(path: Path) -> dict[str, object]:
  if not path.is_file():
    return {}
  with path.open('rb') as handle:
    header = parse_json_line(handle.readline())
  return {} if header is None else header


def header_ms(path: Path) -> int | None:
  return parse_iso_ms(read_header(path).get('timestamp'))


def same_contents(first: Path, second: Path) -> bool:
  return first.samefile(second) or filecmp.cmp(first, second, shallow=False)


def is_prefix(shorter: Path, longer: Path) -> bool:
  size = shorter.stat().st_size
  if size >= longer.stat().st_size:
    return False
  with shorter.open('rb') as first, longer.open('rb') as second:
    while size > 0:
      chunk = first.read(min(size, 1 << 20))
      if not chunk or chunk != second.read(len(chunk)):
        return False
      size -= len(chunk)
  return True


def part_text(part: object) -> str:
  if not isinstance(part, dict) or part.get('type') != 'text':
    return ''
  text = part.get('text')
  return text if isinstance(text, str) else ''


def user_text(message: object) -> str:
  if not isinstance(message, dict) or message.get('role') != 'user':
    return ''
  content = message.get('content')
  if isinstance(content, str):
    return content.strip()
  if not isinstance(content, list):
    return ''
  return '\n'.join(text for text in map(part_text, content) if text).strip()


def unique_entry_id(ids: set[str]) -> str:
  for _ in range(ENTRY_ID_ATTEMPTS):
    candidate = uuid.uuid4().hex[:8]
    if candidate not in ids:
      return candidate
  return str(uuid.uuid4())


def read_json(path: Path) -> object:
  return json.loads(path.read_text(encoding='utf-8'))


def write_json(path: Path, data: object, *, dry_run: bool) -> None:
  if dry_run:
    say(f'would write {path}')
    return
  created = make_dirs(path.parent)
  temp = path.with_name(f'.{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp')
  text = json.dumps(data, indent=2, ensure_ascii=False) + '\n'
  with temp.open('w', encoding='utf-8') as writer:
    writer.write(text)
    writer.flush()
    os.fsync(writer.fileno())
  if os.name == 'nt' and path.exists() and not os.access(path, os.W_OK):
    # Windows refuses to replace a read-only file; the new summary stays read-only too.
    with lifted_read_only(path, [path]):
      temp.replace(path)
  else:
    temp.replace(path)
  for directory in [path.parent, *created]:
    fsync_dir(directory)


def copy_exclusive(source: Path, target: Path) -> None:
  # The copy gets its name only when complete: a stop leaves a hidden .copying file.
  temp = target.with_name(f'.{target.name}.{uuid.uuid4()}.copying')
  try:
    with source.open('rb') as reader, temp.open('xb') as writer:
      shutil.copyfileobj(reader, writer)
      writer.flush()
      # The source is removed next, so the copy must be on disk first.
      os.fsync(writer.fileno())
    shutil.copystat(source, temp)
    try:
      os.link(temp, target)
    except OSError as error:
      if error.errno not in LINK_FALLBACK_ERRNOS:
        raise
      # Without hard links nothing else creates the target while PI WEB is stopped.
      if target.exists():
        message = f'target already exists: {target}'
        raise FileExistsError(message) from error
      temp.rename(target)
  finally:
    remove_file(temp, missing_ok=True, keep=target)


def move_file(source: Path, target: Path) -> None:
  if target.exists():
    message = f'target already exists: {target}'
    raise FileExistsError(message)
  target.parent.mkdir(parents=True, exist_ok=True)
  try:
    os.link(source, target)
  except OSError as error:
    if error.errno not in LINK_FALLBACK_ERRNOS:
      raise
    copy_exclusive(source, target)
  # Every directory on the path: a summary may have created some of them just before.
  make_durable(target, list(target.parents))
  remove_file(source, keep=target)


def make_durable(target: Path, directories: list[Path]) -> None:
  # The file and its new name must survive a power loss before the old name goes;
  # a hard link shares the inode, so its contents are flushed too.
  fsync_file(target)
  for directory in directories:
    fsync_dir(directory)


def make_dirs(path: Path) -> list[Path]:
  # Creates path with its missing parents and returns the parents of the created ones.
  missing = [directory for directory in (path, *path.parents) if not directory.exists()]
  path.mkdir(parents=True, exist_ok=True)
  return [directory.parent for directory in missing]


def drop_duplicate(keep: Path, drop: Path) -> None:
  # An interrupted operation may have left both copies unflushed: the kept one and every
  # directory on its path must survive a power loss before the other one goes.
  make_durable(keep, list(keep.parents))
  remove_file(drop, keep=keep)


def remove_file(
  path: Path,
  *,
  missing_ok: bool = False,
  keep: Path | None = None,
) -> None:
  try:
    path.unlink(missing_ok=missing_ok)
  except PermissionError:
    if os.name != 'nt':
      raise
    # Windows refuses to delete a read-only file. The attribute belongs to the file, so
    # it goes back to the path on a failure and to a hard link that stays.
    links = [path]
    if keep is not None and keep.exists() and keep.samefile(path):
      links.append(keep)
    with lifted_read_only(path, links):
      path.unlink()


@contextmanager
def lifted_read_only(path: Path, restore: list[Path]) -> Generator[None, None, None]:
  # The read-only attribute of path is lifted for the operation and put on every
  # restore path that exists afterwards, however the operation ends.
  mode = path.stat().st_mode
  path.chmod(mode | stat.S_IWRITE)
  try:
    yield
  finally:
    for link in restore:
      if link.exists():
        link.chmod(mode)


def fsync_file(path: Path) -> None:
  try:
    descriptor = os.open(path, os.O_RDWR)
  except PermissionError:
    if os.name != 'nt':
      # POSIX flushes a read-only file through a read-only descriptor as well.
      descriptor = os.open(path, os.O_RDONLY)
    else:
      # Windows flushes only through a handle open for writing.
      with lifted_read_only(path, [path]):
        descriptor = os.open(path, os.O_RDWR)
  try:
    os.fsync(descriptor)
  finally:
    os.close(descriptor)


def fsync_dir(path: Path) -> None:
  # Windows cannot open a directory; NTFS journals its entries itself.
  if os.name == 'nt':
    return
  try:
    descriptor = os.open(path, os.O_RDONLY)
  except PermissionError:
    # Neither readable nor writable: this script has changed no entries there.
    if not os.access(path, os.W_OK):
      return
    raise
  try:
    os.fsync(descriptor)
  except OSError as error:
    # A file system without directory fsync, as syncDir of PI WEB assumes too.
    if error.errno not in {errno.EINVAL, errno.EPERM, errno.EISDIR}:
      raise
  finally:
    os.close(descriptor)


def remove_dir(path: Path) -> bool:
  try:
    path.rmdir()
  except OSError:
    return False
  return True


def remove_empty_dirs(start: Path, root: Path) -> None:
  current = start
  while root in current.parents:
    if current.exists() and not remove_dir(current):
      return
    current = current.parent


def child_dirs(path: Path, pattern: re.Pattern[str] | None = None) -> list[Path]:
  if not path.is_dir():
    return []
  return sorted(
    child for child in path.iterdir()
    if child.is_dir() and (pattern is None or pattern.match(child.name))
  )


def month_dirs(project_dir: Path) -> Iterator[Path]:
  for year_dir in child_dirs(project_dir, YEAR_DIR):
    yield from child_dirs(year_dir, MONTH_DIR)


def session_files(month_dir: Path) -> Iterator[Path]:
  for day_dir in child_dirs(month_dir):
    yield from sorted(path for path in day_dir.glob('*.jsonl') if path.is_file())


class SessionFile:
  def __init__(self, path: Path) -> None:
    self.path = path
    self.header: dict[str, object] = {}
    self.message_count = 0
    self.name: str | None = None
    self.has_name_entry = False
    self.first_user_text = ''
    self.entry_ids: set[str] = set()
    self.last_id: str | None = None
    with path.open('rb') as handle:
      for raw in handle:
        entry = parse_json_line(raw)
        if entry is not None:
          self.take(entry)

  def take(self, entry: dict[str, object]) -> None:
    kind = entry.get('type')
    if kind == 'session':
      if not self.header:
        self.header = entry
      return
    entry_id = entry.get('id')
    if isinstance(entry_id, str):
      self.entry_ids.add(entry_id)
      self.last_id = entry_id
    if kind == 'message':
      self.message_count += 1
      if not self.first_user_text:
        self.first_user_text = user_text(entry.get('message'))
    elif kind == 'session_info':
      name = entry.get('name')
      cleaned = name.strip() if isinstance(name, str) else ''
      self.name = cleaned or None
      self.has_name_entry = True

  def append_name(self, name: str) -> None:
    entry_id = unique_entry_id(self.entry_ids)
    line = json.dumps(
      {
        'type': 'session_info',
        'id': entry_id,
        'parentId': self.last_id,
        'timestamp': now_iso(),
        'name': name,
      },
      ensure_ascii=False,
      separators=(',', ':'),
    )
    with self.path.open('rb+') as handle:
      size = handle.seek(0, os.SEEK_END)
      prefix = b''
      if size > 0:
        handle.seek(size - 1)
        if handle.read(1) != b'\n':
          prefix = b'\n'
      handle.write(prefix + line.encode('utf-8') + b'\n')
      handle.flush()
      # The summary gets the name later and must not get ahead of the file.
      os.fsync(handle.fileno())
    self.entry_ids.add(entry_id)
    self.last_id = entry_id
    self.name = name or None
    self.has_name_entry = True


def required_text(data: dict[str, object], key: str) -> str:
  value = data.get(key)
  if not isinstance(value, str):
    message = f'summary entry needs a string {key}'
    raise TypeError(message)
  return value


@dataclass
class ArchiveEntry:
  session_id: str
  cwd: str
  archived_at: str
  original_path: str
  message_count: int
  name: str | None = None
  parent_session_path: str | None = None

  @classmethod
  def from_summary(cls, data: object) -> ArchiveEntry:
    if not isinstance(data, dict):
      message = 'summary entry must be an object'
      raise TypeError(message)
    count = data.get('messageCount')
    if not isinstance(count, int):
      message = 'summary entry needs an integer messageCount'
      raise TypeError(message)
    return cls(
      session_id=required_text(data, 'sessionId'),
      cwd=canonical_cwd(required_text(data, 'cwd')),
      archived_at=required_text(data, 'archivedAt'),
      original_path=required_text(data, 'originalPath'),
      message_count=count,
      name=text_of(data.get('name')),
      parent_session_path=text_of(data.get('parentSessionPath')),
    )

  @property
  def file_name(self) -> str:
    return Path(self.original_path).name

  def to_summary(self) -> dict[str, object]:
    data: dict[str, object] = {
      'sessionId': self.session_id,
      'cwd': self.cwd,
      'archivedAt': self.archived_at,
      'originalPath': self.original_path,
      'messageCount': self.message_count,
    }
    if self.name:
      data['name'] = self.name
    if self.parent_session_path is not None:
      data['parentSessionPath'] = self.parent_session_path
    return data

  def same_session(self, other: ArchiveEntry) -> bool:
    return self.session_id == other.session_id and self.cwd == other.cwd


@dataclass(frozen=True)
class TreeSlot:
  month_dir: Path
  day_dir: Path

  @classmethod
  def at(cls, root: Path, cwd: str, moment_ms: int) -> TreeSlot:
    moment = datetime.fromtimestamp(moment_ms // MS_PER_SECOND, tz=timezone.utc)
    project_dir = root / project_dir_name(cwd)
    month_dir = project_dir / f'{moment.year:04d}' / f'{moment.month:02d}'
    return cls(month_dir=month_dir, day_dir=month_dir / f'{moment.day:02d}')

  @property
  def summary(self) -> Path:
    return self.month_dir / SUMMARY_NAME


def entry_moment(entry: ArchiveEntry, path: Path) -> int | None:
  moment = created_ms(entry.session_id, path.name)
  return header_ms(path) if moment is None else moment


def find_project_file(
  tree_root: Path,
  cwd: str,
  session_id: str,
  name: str,
) -> Path | None:
  # A file of the session anywhere in the project tree, whatever month and day it is in.
  for month_dir in month_dirs(tree_root / project_dir_name(cwd)):
    for day_dir in child_dirs(month_dir):
      candidate = day_dir / name
      if candidate.is_file() and read_header(candidate).get('id') == session_id:
        return candidate
  return None


def matching_named_file(day_dirs: list[Path], entry: ArchiveEntry) -> Path | None:
  for day_dir in day_dirs:
    candidate = day_dir / entry.file_name
    if candidate.is_file() and read_header(candidate).get('id') == entry.session_id:
      return candidate
  return None


def unreadable_named_file(day_dirs: list[Path], entry: ArchiveEntry) -> Path | None:
  # A same-named file without an id in its header, e.g. an empty session file.
  for day_dir in day_dirs:
    candidate = day_dir / entry.file_name
    if candidate.is_file() and read_header(candidate).get('id') is None:
      return candidate
  return None


def project_day_dirs(tree_root: Path, cwd: str) -> list[Path]:
  return [
    day_dir for month_dir in month_dirs(tree_root / project_dir_name(cwd))
    for day_dir in child_dirs(month_dir)
  ]


def expected_day_dir(tree_root: Path, entry: ArchiveEntry) -> Path | None:
  moment = created_ms(entry.session_id, entry.file_name)
  if moment is None:
    return None
  return TreeSlot.at(tree_root, entry.cwd, moment).day_dir


def has_session_id(path: Path, entry: ArchiveEntry) -> bool:
  return path.is_file() and read_header(path).get('id') == entry.session_id


def find_entry_file(tree_root: Path, month_dir: Path, entry: ArchiveEntry) -> Path | None:
  # A same-named file of another session is never taken; one with an id is
  # preferred to one without a readable id.
  expected = expected_day_dir(tree_root, entry)
  if expected is not None and has_session_id(expected / entry.file_name, entry):
    return expected / entry.file_name
  day_dirs = child_dirs(month_dir)
  first = [] if expected is None else [expected]
  return (
    matching_named_file(day_dirs, entry)
    or find_project_file(tree_root, entry.cwd, entry.session_id, entry.file_name)
    or unreadable_named_file(
      [*first, *day_dirs, *project_day_dirs(tree_root, entry.cwd)],
      entry,
    )
  )


def entry_file(month_dir: Path, entry: ArchiveEntry) -> Path:
  moment = created_ms(entry.session_id, entry.file_name)
  expected: Path | None = None
  if moment is not None:
    day = datetime.fromtimestamp(moment // MS_PER_SECOND, tz=timezone.utc).day
    expected = month_dir / f'{day:02d}'
    if has_session_id(expected / entry.file_name, entry):
      return expected / entry.file_name
  day_dirs = child_dirs(month_dir)
  first = [] if expected is None else [expected]
  found = matching_named_file(day_dirs, entry) or unreadable_named_file(
    [*first, *day_dirs],
    entry,
  )
  if found is not None:
    return found
  if expected is not None and not (expected / entry.file_name).exists():
    return expected / entry.file_name
  return month_dir / entry.file_name


def read_summary(path: Path) -> list[ArchiveEntry]:
  if not path.exists():
    return []
  data = read_json(path)
  if not isinstance(data, dict):
    message = f'summary {path} is not an object'
    raise TypeError(message)
  if data.get('version') != SUMMARY_VERSION:
    message = f'unsupported summary version in {path}'
    raise ValueError(message)
  sessions = data.get('sessions')
  if not isinstance(sessions, list):
    message = f'summary without sessions {path}'
    raise TypeError(message)
  return [ArchiveEntry.from_summary(item) for item in sessions]


def read_index(path: Path) -> list[dict[str, object]]:
  if not path.exists():
    return []
  data = read_json(path)
  sessions = data.get('sessions') if isinstance(data, dict) else None
  if not isinstance(sessions, list):
    message = f'unsupported archive index {path}'
    raise TypeError(message)
  return [record for record in sessions if isinstance(record, dict)]


def record_file_name(record: dict[str, object]) -> str:
  for key in ('archivePath', 'originalPath'):
    value = text_of(record.get(key))
    if value:
      return Path(value).name
  return ''


def record_count(record: dict[str, object], path: Path) -> int:
  count = record.get('messageCount')
  return count if isinstance(count, int) else SessionFile(path).message_count


def original_dirs(records: list[dict[str, object]]) -> dict[str, Path]:
  dirs: dict[str, Path] = {}
  for record in records:
    cwd = text_of(record.get('cwd'))
    original = text_of(record.get('originalPath'))
    if cwd and original:
      dirs.setdefault(canonical_cwd(cwd), Path(original).parent)
  return dirs


def flat_record(entry: ArchiveEntry, readable: Path, target: Path) -> dict[str, object]:
  moment = entry_moment(entry, readable)
  record: dict[str, object] = {
    'sessionId': entry.session_id,
    'cwd': entry.cwd,
    'archivedAt': entry.archived_at,
    'originalPath': entry.original_path,
    'archivePath': str(target),
    'created': entry.archived_at if moment is None else iso_ms(moment),
    'modified': entry.archived_at,
    'messageCount': entry.message_count,
    'firstMessage': SessionFile(readable).first_user_text,
  }
  if entry.name:
    record['name'] = entry.name
  if entry.parent_session_path is not None:
    record['parentSessionPath'] = entry.parent_session_path
  return record


class Summaries:
  def __init__(self, *, dry_run: bool) -> None:
    self.dry_run = dry_run
    self.loaded: dict[Path, list[ArchiveEntry]] = {}
    self.dirty: set[Path] = set()

  def entries(self, path: Path) -> list[ArchiveEntry]:
    if path not in self.loaded:
      self.loaded[path] = read_summary(path)
    return self.loaded[path]

  def upsert(self, path: Path, entry: ArchiveEntry) -> None:
    entries = self.entries(path)
    for index, current in enumerate(entries):
      if current.same_session(entry):
        entries[index] = entry
        break
    else:
      entries.append(entry)
    self.dirty.add(path)

  def remove(self, path: Path, entry: ArchiveEntry) -> None:
    kept = [item for item in self.entries(path) if not item.same_session(entry)]
    self.loaded[path] = kept
    self.dirty.add(path)

  def discard(self) -> None:
    self.dirty.clear()

  def flush(self) -> list[Path]:
    emptied: list[Path] = []
    for path in sorted(self.dirty):
      entries = self.loaded[path]
      if entries:
        sessions = [item.to_summary() for item in entries]
        data = {
          'version': SUMMARY_VERSION,
          'sessions': sessions,
        }
        write_json(path, data, dry_run=self.dry_run)
      elif self.dry_run:
        say(f'would remove {path}')
      else:
        remove_file(path, missing_ok=True)
        emptied.append(path.parent)
    self.dirty.clear()
    return emptied


@dataclass
class NamingOptions:
  pi: str
  model: str | None
  limit: int | None
  timeout: float


@dataclass
class Namer:
  options: NamingOptions
  used: int = 0

  def name_for(self, session: SessionFile) -> str | None:
    limit = self.options.limit
    text = session.first_user_text
    if not text or (limit is not None and self.used >= limit):
      return None
    self.used += 1
    if len(text) > NAME_INPUT_LIMIT:
      text = text[:NAME_INPUT_LIMIT] + ELLIPSIS
    command = [
      self.options.pi,
      '-p',
      '--no-session',
      '--no-tools',
      '--no-extensions',
      '--no-skills',
      '--no-context-files',
      '--system-prompt',
      NAME_SYSTEM_PROMPT,
    ]
    if self.options.model:
      command.extend(['--model', self.options.model])
    try:
      result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        command,
        input=NAME_USER_PROMPT + text,
        capture_output=True,
        text=True,
        encoding='utf-8',
        timeout=self.options.timeout,
        check=False,
      )  # nosec B603
    except (OSError, subprocess.TimeoutExpired) as error:
      say(f'naming {session.path.name} failed: {error}')
      return None
    if result.returncode != 0:
      say(f'naming {session.path.name} failed: pi exited with {result.returncode}')
      return None
    name = LINE_BREAKS.sub(' ', result.stdout).strip()[:NAME_LENGTH_LIMIT].strip()
    return name or None


@dataclass
class Options:
  data_dir: Path
  mode: str
  dry_run: bool
  naming: NamingOptions | None


class Converter:
  def __init__(self, options: Options) -> None:
    self.options = options
    self.tree_root = options.data_dir / TREE_DIR_NAME
    self.summaries = Summaries(dry_run=options.dry_run)
    naming = options.naming
    self.namer = None if naming is None or options.dry_run else Namer(naming)
    self.counts: Counter[str] = Counter()
    self.cleanup: set[Path] = set()
    self.parked: dict[Path, list[Path]] = {}
    self.known_dirs: list[Path] = []
    self.unplaced_parked: list[Path] = []

  def run(self) -> None:
    try:
      if self.options.mode == 'reverse':
        self.reverse()
      elif self.options.mode == 'repair':
        self.repair()
      else:
        self.forward()
    finally:
      # Files moved before an error or Ctrl+C keep their summary records.
      self.flush()
    if self.options.dry_run:
      return
    for directory in sorted(self.cleanup, reverse=True):
      remove_empty_dirs(directory, self.tree_root)

  def report(self) -> None:
    counts = ', '.join(f'{key}: {value}' for key, value in sorted(self.counts.items()))
    say(counts or 'nothing to do')

  def _past(self, done: str, would: str) -> str:
    return f'would {would}' if self.options.dry_run else done

  def _problem(self, message: str) -> None:
    say(f'problem: {message}')
    self.counts['problems'] += 1

  def flush(self) -> None:
    self.cleanup.update(self.summaries.flush())

  def place(self, source: Path, target: Path, session_id: str) -> str:
    source_exists = source.exists()
    target_exists = target.exists()
    if source_exists and target_exists:
      same_session = read_header(target).get('id') == session_id
      if not same_session or not same_contents(source, target):
        self._problem(f'both {source} and {target} exist, left as is')
        return 'conflict'
      # A move interrupted between the link and the unlink, maybe before the flush.
      if self.options.dry_run:
        say(f'would remove {source}, identical to {target}')
      else:
        drop_duplicate(target, source)
      self.counts['moved'] += 1
      self.cleanup.add(source.parent)
      return 'moved'
    if not source_exists:
      if not target_exists:
        self._problem(f'session file {source} is missing')
        return 'missing'
      # A file moved by an earlier interrupted run.
      if read_header(target).get('id') == session_id:
        return 'present'
      self._problem(f'{target} exists but is not a file of session {session_id}')
      return 'conflict'
    if self.options.dry_run:
      say(f'would move {source} -> {target}')
    else:
      move_file(source, target)
    self.counts['moved'] += 1
    self.cleanup.add(source.parent)
    return 'moved'

  def name_entry(self, entry: ArchiveEntry, path: Path) -> None:
    if entry.name or self.options.naming is None:
      return
    self.counts['unnamed'] += 1
    if self.namer is None:
      return
    session = SessionFile(path)
    if session.name:
      entry.name = session.name
      return
    name = self.namer.name_for(session)
    if name is None:
      return
    try:
      session.append_name(name)
    except PermissionError:
      say(f'warning: {path} is read-only, left unnamed')
      self.counts['warnings'] += 1
      return
    entry.name = name
    self.counts['named'] += 1

  def entry_from_file(
    self,
    path: Path,
    session_dirs: dict[str, Path],
  ) -> ArchiveEntry | None:
    session = SessionFile(path)
    session_id = text_of(session.header.get('id'))
    cwd = canonical_cwd(text_of(session.header.get('cwd')) or '')
    if session_id is None or cwd in {'', '/'}:
      self._problem(f'{path} has no session header with an id and a cwd other than /')
      return None
    directory = session_dirs.get(cwd, default_session_dir(cwd))
    return ArchiveEntry(
      session_id=session_id,
      cwd=cwd,
      archived_at=mtime_iso(path),
      original_path=str(directory / path.name),
      message_count=session.message_count,
      name=session.name,
      parent_session_path=text_of(session.header.get('parentSession')),
    )

  def forward(self) -> None:
    index_path = self.options.data_dir / INDEX_NAME
    flat_dir = self.options.data_dir / FLAT_DIR_NAME
    records = read_index(index_path)
    session_dirs = original_dirs(records)
    remaining = [
      record for record in records if not self.forward_record(record, flat_dir)
    ]
    referenced = {record_file_name(record)
                  for record in records}
    for path in sorted(flat_dir.glob('*.jsonl')):
      if path.is_file() and path.name not in referenced:
        self.forward_orphan(path, session_dirs)
    self.flush()
    self._finish_index(index_path, remaining)

  def forward_record(self, record: dict[str, object], flat_dir: Path) -> bool:
    session_id = text_of(record.get('sessionId'))
    cwd = canonical_cwd(text_of(record.get('cwd')) or '')
    original = text_of(record.get('originalPath'))
    file_name = record_file_name(record)
    if session_id is None or original is None or not file_name or cwd in {'', '/'}:
      self._problem(f'index record {record.get("sessionId")!r} is incomplete or of /')
      return False
    source = flat_dir / file_name
    moment = created_ms(session_id, file_name)
    if moment is None:
      moment = header_ms(source)
    if moment is None and not source.exists():
      # An interrupted run may have moved the file already: its header dates it.
      placed = find_project_file(self.tree_root, cwd, session_id, file_name)
      moment = None if placed is None else header_ms(placed)
    if moment is None:
      self._problem(f'cannot date session {session_id}')
      return False
    slot = TreeSlot.at(self.tree_root, cwd, moment)
    target = slot.day_dir / file_name
    if self.place(source, target, session_id) not in PLACED:
      return False
    readable = target if target.exists() else source
    name = (text_of(record.get('name')) or '').strip()
    entry = ArchiveEntry(
      session_id=session_id,
      cwd=cwd,
      archived_at=text_of(record.get('archivedAt')) or mtime_iso(readable),
      original_path=original,
      message_count=record_count(record, readable),
      name=name or None,
      parent_session_path=text_of(record.get('parentSessionPath')),
    )
    self.summaries.upsert(slot.summary, entry)
    self.name_entry(entry, readable)
    return True

  def forward_orphan(self, path: Path, session_dirs: dict[str, Path]) -> None:
    entry = self.entry_from_file(path, session_dirs)
    if entry is None:
      return
    # jscpd:ignore-start # noqa: ERA001
    if entry.cwd not in session_dirs:
      say(f'warning: {entry.original_path} is only a guess of where {path} came from')
      self.counts['warnings'] += 1
    moment = entry_moment(entry, path)
    if moment is None:
      self._problem(f'cannot date session file {path}')
      return
    slot = TreeSlot.at(self.tree_root, entry.cwd, moment)
    # jscpd:ignore-end # noqa: ERA001
    target = slot.day_dir / path.name
    known = any(item.same_session(entry) for item in self.summaries.entries(slot.summary))
    if not known:
      # The index has no record of this file, so its record is on disk before the move.
      self.summaries.upsert(slot.summary, entry)
      self.flush()
    if self.place(path, target, entry.session_id) not in PLACED:
      if not known:
        self.summaries.remove(slot.summary, entry)
      return
    self.summaries.upsert(slot.summary, entry)
    self.name_entry(entry, target if target.exists() else path)
    say(
      self._past(
        f'added {path.name} that had no index record',
        f'add {path.name} that has no index record',
      ),
    )

  def _finish_index(self, index_path: Path, remaining: list[dict[str, object]]) -> None:
    if not index_path.exists():
      return
    if remaining or self.counts['problems']:
      say(f'{len(remaining)} records stay in {index_path}, fix the problems, run again')
      write_json(index_path, {
        'sessions': remaining,
      }, dry_run=self.options.dry_run)
      return
    stamp = datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    migrated = index_path.with_name(f'{index_path.name}.migrated-{stamp}')
    if self.options.dry_run:
      say(f'would rename {index_path} -> {migrated}')
      return
    index_path.rename(migrated)
    remove_dir(self.options.data_dir / FLAT_DIR_NAME)

  def reverse(self) -> None:
    index_path = self.options.data_dir / INDEX_NAME
    index = read_index(index_path)
    known = {
      text_of(record.get('sessionId')): record
      for record in index
    }
    count = len(index)
    try:
      for project_dir in child_dirs(self.tree_root):
        for month_dir in month_dirs(project_dir):
          self.reverse_month(month_dir, index, known)
    finally:
      # Records of files moved before an error or Ctrl+C are kept.
      if len(index) > count:
        written = False
        try:
          write_json(index_path, {
            'sessions': index,
          }, dry_run=self.options.dry_run)
          written = True
        finally:
          # Without their index records the moved files stay in the summaries.
          if not written:
            self.summaries.discard()

  def reverse_month(
    self,
    month_dir: Path,
    index: list[dict[str, object]],
    known: dict[str | None, dict[str, object]],
  ) -> None:
    summary = month_dir / SUMMARY_NAME
    flat_dir = self.options.data_dir / FLAT_DIR_NAME
    for entry in list(self.summaries.entries(summary)):
      source = entry_file(month_dir, entry)
      target = flat_dir / source.name
      existing = known.get(entry.session_id)
      if existing is not None and text_of(existing.get('archivePath')) != str(target):
        self._problem(f'{INDEX_NAME} already has another record of {entry.session_id}')
        continue
      if self.place(source, target, entry.session_id) not in PLACED:
        continue
      if existing is None:
        record = flat_record(entry, target if target.exists() else source, target)
        index.append(record)
        known[entry.session_id] = record
      self.summaries.remove(summary, entry)

  def _months(self) -> Iterator[Path]:
    for project_dir in child_dirs(self.tree_root):
      yield from month_dirs(project_dir)

  def repair(self) -> None:
    # Summaries come back from backups before anything reads them.
    for month_dir in self._months():
      self._clean_leftovers(month_dir)
    flat_dir = self.options.data_dir / FLAT_DIR_NAME
    for leftover in sorted(flat_dir.glob('.*.copying')):
      self._remove_leftover(leftover)
    session_dirs = self._tree_session_dirs()
    for parked in self.unplaced_parked:
      self._resolve_unplaced(parked)
    claimed: set[Path] = set()
    for month_dir in self._months():
      self.repair_month(month_dir, claimed)
    for month_dir in self._months():
      for path in list(session_files(month_dir)):
        if path not in claimed:
          self.repair_orphan(path, session_dirs, claimed)

  def _clean_leftovers(self, month_dir: Path) -> None:
    summary = month_dir / SUMMARY_NAME
    backups = sorted(
      month_dir.glob(f'.{SUMMARY_NAME}.*.previous'),
      key=lambda path: path.stat().st_mtime_ns,
    )
    if backups and not summary.exists():
      # An undo failed after the last record of the month was removed.
      newest = backups.pop()
      if self.options.dry_run:
        say(f'would restore {summary} from {newest}')
        self.summaries.loaded[summary] = read_summary(newest)
      else:
        newest.rename(summary)
        say(f'restored {summary} from {newest}')
      self.counts['repaired'] += 1
    leftovers = [*backups, *month_dir.glob(f'.{SUMMARY_NAME}.*.tmp')]
    for day_dir in child_dirs(month_dir):
      leftovers.extend(day_dir.glob('.*.copying'))
      for parked in sorted(day_dir.glob('*.moving')):
        kept = day_dir / parked.name.rsplit('.', 2)[0]
        if kept.is_file():
          self._resolve_parked(parked, kept)
        else:
          # Its original is looked for once the session directories are known.
          self.unplaced_parked.append(parked)
    for leftover in sorted(leftovers):
      self._remove_leftover(leftover)

  def _remove_leftover(self, leftover: Path) -> None:
    if self.options.dry_run:
      say(f'would remove {leftover}')
    else:
      remove_file(leftover)
      say(f'removed {leftover} left by an interrupted operation')
      self.cleanup.add(leftover.parent)
    self.counts['repaired'] += 1

  def _find_original(self, entry: ArchiveEntry, path: Path) -> Path:
    original = Path(entry.original_path)
    if original.exists():
      return original
    found = [d / path.name for d in self.known_dirs if (d / path.name).is_file()]
    if len(found) > 1:
      say(f'warning: {path.name} is in several session directories, took {found[0]}')
      self.counts['warnings'] += 1
    if not found:
      say(f'warning: {original} is only a guess of the original path of {path}')
      self.counts['warnings'] += 1
      return original
    entry.original_path = str(found[0])
    return found[0]

  def _warn_prefix(self, shorter: Path, longer: Path) -> None:
    # An unfinished copy or an older version that pi may still be writing: only reported.
    say(f'warning: {shorter} is a strict prefix of {longer}, both files are kept')
    self.counts['warnings'] += 1

  def _resolve_unplaced(self, parked: Path) -> None:
    # A restore parks the archived file; its copy at the original path is the survivor.
    name = parked.name.rsplit('.', 2)[0]
    header = read_header(parked)
    session_id = header.get('id')
    dirs = list(self.known_dirs)
    cwd = text_of(header.get('cwd'))
    if cwd:
      dirs.append(default_session_dir(canonical_cwd(cwd)))
    for original in sorted({directory / name
                            for directory in dirs}):
      if original.is_file() and read_header(original).get('id') == session_id:
        self._resolve_parked(parked, original)
        return
    searched = ', '.join(str(directory) for directory in sorted(set(dirs)))
    say(
      f'warning: {parked} is left by an interrupted operation, its original is not '
      f'in {searched or "any known directory"}, the file is kept',
    )
    self.counts['warnings'] += 1

  def _resolve_parked(self, parked: Path, kept: Path) -> None:
    if not same_contents(parked, kept):
      say(f'warning: {parked} is left by an interrupted operation, compare with {kept}')
      self.counts['warnings'] += 1
      return
    if self.options.dry_run:
      say(f'would remove {parked}, identical to {kept}')
    else:
      drop_duplicate(kept, parked)
      self.cleanup.add(parked.parent)
      say(f'removed {parked}, identical to {kept}')
    self.counts['repaired'] += 1

  def _leftovers_near(self, original: Path) -> list[Path]:
    if original.parent not in self.parked:
      self.parked[original.parent] = sorted(
        path for path in original.parent.glob('*')
        if path.name.endswith(('.moving', '.copying'))
      )
    prefixes = (f'{original.name}.', f'.{original.name}.')
    return [
      path for path in self.parked[original.parent] if path.name.startswith(prefixes)
    ]

  def _tree_session_dirs(self) -> dict[str, Path]:
    dirs: dict[str, Path] = {}
    for project_dir in child_dirs(self.tree_root):
      for month_dir in month_dirs(project_dir):
        for entry in self.summaries.entries(month_dir / SUMMARY_NAME):
          dirs.setdefault(entry.cwd, Path(entry.original_path).parent)
          self.known_dirs.append(Path(entry.original_path).parent)
    self.known_dirs = sorted(set(self.known_dirs))
    return dirs

  def repair_month(self, month_dir: Path, claimed: set[Path]) -> None:
    summary = month_dir / SUMMARY_NAME
    for entry in list(self.summaries.entries(summary)):
      path = self.repair_entry(entry, summary, month_dir)
      if path is not None:
        claimed.add(path)

  def repair_entry(
    self,
    entry: ArchiveEntry,
    summary: Path,
    month_dir: Path,
  ) -> Path | None:
    path = find_entry_file(self.tree_root, month_dir, entry)
    flat_file = self.options.data_dir / FLAT_DIR_NAME / entry.file_name
    if path is None and flat_file.is_file():
      self._problem(f'{entry.file_name} is in {FLAT_DIR_NAME}/, run --reverse again')
      return None
    if path is None:
      self.summaries.remove(summary, entry)
      say(
        self._past(
          f'removed the record of missing {entry.file_name} from {summary}',
          f'remove the record of missing {entry.file_name} from {summary}',
        ),
      )
      self.counts['repaired'] += 1
      return None
    for leftover in self._leftovers_near(Path(entry.original_path)):
      if leftover.name.endswith('.copying'):
        self._remove_leftover(leftover)
      else:
        self._resolve_parked(leftover, path)
    if self._drop_identical(entry, summary, path):
      # Claimed: a dry run must not report the file again as one without a record.
      return path
    moment = entry_moment(entry, path)
    if moment is None:
      self._handle_undated(entry, summary, path)
      return path
    slot = TreeSlot.at(self.tree_root, entry.cwd, moment)
    target = slot.day_dir / path.name
    if target != path:
      if self.place(path, target, entry.session_id) not in PLACED:
        return path
      say(self._past(f'moved {path} to {slot.day_dir}', f'move {path} to {slot.day_dir}'))
      self.counts['repaired'] += 1
    if slot.summary != summary:
      self.summaries.remove(summary, entry)
      self.summaries.upsert(slot.summary, entry)
      say(
        self._past(
          f'moved the record of {path.name} to {slot.summary}',
          f'move the record of {path.name} to {slot.summary}',
        ),
      )
      self.counts['repaired'] += 1
    readable = target if target.exists() else path
    self._sync_name(entry, slot.summary, readable)
    return readable

  def _handle_undated(self, entry: ArchiveEntry, summary: Path, path: Path) -> None:
    if path.parent.parent != summary.parent:
      self._problem(f'cannot date session file {path}')
      return
    say(f'warning: {path} has no readable time, it stays in its day directory')
    self.counts['warnings'] += 1
    self._sync_name(entry, summary, path)

  def _drop_identical(self, entry: ArchiveEntry, summary: Path, path: Path) -> bool:
    original = Path(entry.original_path)
    if not original.exists() or original.resolve() == path.resolve():
      return False
    if is_prefix(original, path):
      self._warn_prefix(original, path)
      return False
    if is_prefix(path, original):
      self._warn_prefix(path, original)
      return False
    if not same_contents(original, path):
      say(f'warning: {original} exists again with other contents, both files are kept')
      self.counts['warnings'] += 1
      return False
    if self.options.dry_run:
      say(f'would remove {path}')
    else:
      drop_duplicate(original, path)
      self.cleanup.add(path.parent)
      say(f'removed {path} and its record: {original} has the same contents')
    self.summaries.remove(summary, entry)
    self.counts['repaired'] += 1
    return True

  def _sync_name(self, entry: ArchiveEntry, summary: Path, path: Path) -> None:
    session = SessionFile(path)
    if not session.has_name_entry or session.name == entry.name:
      return
    entry.name = session.name
    self.summaries.upsert(summary, entry)
    say(
      self._past(
        f'took the name of {path.name} from its session_info',
        f'take the name of {path.name} from its session_info',
      ),
    )
    self.counts['repaired'] += 1

  def repair_orphan(
    self,
    path: Path,
    session_dirs: dict[str, Path],
    claimed: set[Path],
  ) -> None:
    entry = self.entry_from_file(path, session_dirs)
    if entry is None:
      return
    original = self._find_original(entry, path)
    if original.exists() and original.resolve() == path.resolve():
      # A symbolic link to the file itself: the file is the only copy.
      say(f'warning: {original} links to {path}, both are kept')
      self.counts['warnings'] += 1
      return
    if original.exists() and is_prefix(path, original):
      # Left without a record: an archived copy is never preferred to a longer original.
      self._warn_prefix(path, original)
      return
    if original.exists() and same_contents(original, path):
      if self.options.dry_run:
        say(f'would remove {path}')
      else:
        drop_duplicate(original, path)
        self.cleanup.add(path.parent)
        say(f'removed {path} without a record: {original} has the same contents')
      self.counts['repaired'] += 1
      return
    if original.exists() and is_prefix(original, path):
      self._warn_prefix(original, path)
    elif original.exists():
      say(f'warning: {original} exists with other contents, both files are kept')
      self.counts['warnings'] += 1
    moment = entry_moment(entry, path)
    if moment is None:
      self._problem(f'cannot date session file {path}')
      return
    slot = TreeSlot.at(self.tree_root, entry.cwd, moment)
    target = slot.day_dir / path.name
    if target != path and self.place(path, target, entry.session_id) not in PLACED:
      return
    claimed.add(target)
    self.summaries.upsert(slot.summary, entry)
    say(self._past(f'added a record of {target}', f'add a record of {target}'))
    self.counts['repaired'] += 1


def parse_options(argv: list[str]) -> Options:
  parser = argparse.ArgumentParser(
    description='Convert the pi-web session archive between archived-sessions.json '
    'with archived-sessions/ and the archived-projects/ tree; stop pi-web first.',
  )
  modes = parser.add_mutually_exclusive_group()
  modes.add_argument(
    '--reverse',
    action='store_true',
    help='convert archived-projects/ back to archived-sessions.json',
  )
  modes.add_argument(
    '--repair',
    action='store_true',
    help='reconcile archived-projects/ summaries with session files',
  )
  parser.add_argument(
    '--data-dir',
    type=Path,
    help='default: $PI_WEB_DATA_DIR or ~/.pi-web',
  )
  parser.add_argument('--dry-run', action='store_true', help='report without changes')
  parser.add_argument(
    '--name-unnamed',
    action='store_true',
    help='forward mode: name unnamed sessions with pi -p',
  )
  parser.add_argument('--pi', default='pi', help='pi executable, default: pi')
  parser.add_argument('--model', help='pi --model for naming')
  parser.add_argument('--name-limit', type=int, help='name at most this many sessions')
  parser.add_argument(
    '--name-timeout',
    type=float,
    default=120.0,
    help='seconds per pi call',
  )
  args = parser.parse_args(argv)
  mode = 'reverse' if args.reverse else 'repair' if args.repair else 'forward'
  if args.name_unnamed and mode != 'forward':
    parser.error('--name-unnamed works only in the forward mode')
  naming = None
  if args.name_unnamed:
    naming = NamingOptions(
      pi=args.pi,
      model=args.model,
      limit=args.name_limit,
      timeout=args.name_timeout,
    )
  data_dir = default_data_dir() if args.data_dir is None else absolute(args.data_dir)
  return Options(data_dir=data_dir, mode=mode, dry_run=args.dry_run, naming=naming)


def main(argv: list[str] | None = None) -> int:
  options = parse_options(sys.argv[1:] if argv is None else argv)
  converter = Converter(options)
  try:
    converter.run()
  except (OSError, TypeError, ValueError) as error:
    say(f'error: {error}')
    converter.report()
    return 2
  converter.report()
  return 1 if converter.counts['problems'] else 0


if __name__ == '__main__':
  sys.exit(main())
