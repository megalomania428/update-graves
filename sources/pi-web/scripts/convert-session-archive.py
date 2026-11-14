#!/usr/bin/env python3
# Convert the pi-web session archive between the flat layout (archived-sessions.json
# with archived-sessions/) and the archived-projects/ tree with monthly summaries, repair
# the tree, or import the sessions of an opencode database into the tree.

# cspell:ignore jsonl nosec noqa errno EXDEV EPERM ENOTSUP copyfileobj copystat getpid
# cspell:ignore fromisoformat fromtimestamp rmdir iterdir mtime
# cspell:ignore ERRNOS unflushed RDWR RDONLY NTFS EINVAL EISDIR
# cspell:ignore devnull sqlite udfff ufffd
# cspell:ignore atime utime hexdigest jsdiff filediff todowrite todos deepcopy cwds ufeff
# cspell:ignore relatime strictatime strerror
from __future__ import annotations

import argparse
import copy
import errno
import filecmp
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess  # ruff: ignore[suspicious-subprocess-import]  # nosec B404
import sys
import tempfile
import time
import uuid
from collections import Counter
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from typing import IO, TYPE_CHECKING, BinaryIO
from urllib.parse import unquote_to_bytes

if TYPE_CHECKING:
  from collections.abc import Callable, Generator, Iterator, Sequence

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
ENTRY_ID_LENGTH = 8
ELLIPSIS = '\u2026'
PLACED = frozenset({'moved', 'present'})
PROGRESS_SECONDS = 5.0
PROJECTS_NAME = 'projects.json'
PI_SESSION_VERSION = 3
OPENCODE_API = 'opencode'
PI_IMAGE_TYPES = frozenset({'image/jpeg', 'image/png', 'image/gif', 'image/webp'})
OPENCODE_DEFAULT_TITLE = re.compile(
  r'^(?:New|Child) session - \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$',
)
OPENCODE_STOP_REASONS = {
  'tool-calls': 'toolUse',
  'length': 'length',
  'error': 'error',
}
UNFINISHED_TOOL = '[no result: the tool call did not finish]'
LONE_SURROGATE = re.compile(r'[\ud800-\udfff]')
REPLACEMENT_CHARACTER = '\ufffd'
RECORD_PREFIX = 'opencode-import.'
SESSION_RECORD = 'opencode-import.session'
MESSAGE_RECORD = 'opencode-import.message'
PART_RECORD = 'opencode-import.part'
EVENT_RECORD = 'opencode-import.event'
SHARE_URL = 'share_url'
SET_COOKIE = 'set-cookie'
IMPORTED_KINDS = frozenset({'session', 'session_info', 'model_change', 'compaction'})
DIFF_CONTEXT = 4
DIFF_EDIT_LIMIT = 2000
DIFF_LINE_BREAK = re.compile(r'(\n|\r\n)')
NO_NEWLINE = '\\ No newline at end of file'
HUNK_HEADER = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')
OPENCODE_VERSION = re.compile(r'^v?(\d+)\.(\d+)\.(\d+)')
LAST_ZERO_BASED_READ = (1, 1, 59)
OLD_READ_OUTPUT = '<file>'
NEW_READ_OUTPUT = '<path>'
PATCH_BEGIN = '*** Begin Patch'
PATCH_END = '*** End Patch'
PATCH_FILE_HEADERS = ('*** Add File: ', '*** Delete File: ', '*** Update File: ')
PATCH_OUTPUT_HEAD = 'Success. Updated the following files:\n'
PATCH_LSP_BLOCK = re.compile(r'\n\nLSP errors detected in (.+?), please fix:\n')
# The opencode tools with a pi counterpart: the pi name and where each opencode input
# key goes in the pi arguments.
PI_TOOLS: dict[str, tuple[str, dict[str, tuple[str | int, ...]]]] = {
  'edit': (
    'edit',
    {
      'filePath': ('path', ),
      'oldString': ('edits', 0, 'oldText'),
      'newString': ('edits', 0, 'newText'),
    },
  ),
  'write': ('write', {
    'filePath': ('path', ),
    'content': ('content', ),
  }),
  'read': (
    'read',
    {
      'filePath': ('path', ),
      'offset': ('offset', ),
      'limit': ('limit', ),
    },
  ),
  'bash': ('bash', {
    'command': ('command', ),
    'timeout': ('timeout', ),
  }),
  'grep': (
    'grep',
    {
      'pattern': ('pattern', ),
      'path': ('path', ),
      'include': ('glob', ),
    },
  ),
  'glob': ('find', {
    'pattern': ('pattern', ),
    'path': ('path', ),
  }),
  'list': ('ls', {
    'path': ('path', ),
  }),
}
# Values of a tool part that the tool result of pi may hold unchanged.
RESULT_LINKS: tuple[tuple[tuple[str | int, ...], tuple[str | int, ...]], ...] = (
  (('state', 'output'), ('content', 0, 'text')),
  (('state', 'error'), ('content', 0, 'text')),
  (('state', 'title'), ('content', 0, 'text')),
  (('state', 'metadata', 'output'), ('content', 0, 'text')),
  (('state', 'metadata', 'diff'), ('details', 'patch')),
  (('state', 'metadata', 'filediff', 'patch'), ('details', 'patch')),
  (('state', 'metadata', 'files', 0, 'patch'), ('details', 'patch')),
)
# Values of a tool part that the arguments of the pi tool call may hold unchanged.
CALL_LINKS: tuple[tuple[tuple[str | int, ...], tuple[str | int, ...]],
                  ...] = ((('state', 'metadata', 'todos'), ('todos', )), )
MISSING = object()


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


def later_stamp(current: str | None, stamp: str | None) -> str | None:
  # The later of two entry times: a file gets the time of its latest entry.
  moment = parse_iso_ms(stamp)
  if moment is None:
    return current
  known = parse_iso_ms(current)
  return stamp if known is None or moment >= known else current


class ReadError(OSError):
  # A session file that cannot be opened or read; its session is reported, not changed.
  def __init__(self, path: Path, error: OSError) -> None:
    super().__init__(error.errno, error.strerror or str(error), str(path))
    self.path = path


@contextmanager
def reading(path: Path) -> Generator[None, None, None]:
  try:
    yield
  except ReadError:
    raise
  except OSError as error:
    name = error.filename
    raise ReadError(Path(name) if isinstance(name, str) else path, error) from error


FIRST_TIMES: dict[Path, tuple[int, int]] = {}
# The files the run has rewritten, appended to or copied: their access time is given
# back after the last read of the run.
REWRITTEN: set[Path] = set()


def note_times(path: Path) -> None:
  # The access and modification times a file has before the run first reads it: reading
  # moves the access time on relatime and strictatime file systems.
  key = path.absolute()
  if key not in FIRST_TIMES:
    try:
      status = path.stat()
    except OSError:
      return
    FIRST_TIMES[key] = (status.st_atime_ns, status.st_mtime_ns)


def original_times(path: Path) -> tuple[int, int]:
  # The noted times count while the modification time is still the noted one.
  current = times_of(path)
  noted = FIRST_TIMES.get(path.absolute())
  if noted is None or noted[1] != current[1]:
    FIRST_TIMES[path.absolute()] = current
    return current
  return noted


def restore_access(path: Path) -> None:
  # A file the run has rewritten gets back the access time it had before the first read.
  noted = FIRST_TIMES.get(path.absolute())
  status = path.stat()
  if noted is None or noted[1] != status.st_mtime_ns:
    return
  if noted[0] != status.st_atime_ns:
    os.utime(path, ns=noted)


def restore_rewritten() -> None:
  for path in sorted(REWRITTEN):
    with suppress(OSError):
      restore_access(path)


def times_of(path: Path) -> tuple[int, int]:
  status = path.stat()
  return status.st_atime_ns, status.st_mtime_ns


def set_mtime(path: Path, moment_ms: int) -> None:
  # Only the modification time changes, the access time stays.
  FIRST_TIMES.pop(path.absolute(), None)
  os.utime(path, ns=(times_of(path)[0], moment_ms * NS_PER_MS))


def created_ms(session_id: str, file_name: str) -> int | None:
  if UUID_V7.match(session_id):
    return int(session_id[:8] + session_id[9:13], 16)
  match = FILE_TIME.match(file_key(file_name))
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
  note_times(path)
  with reading(path), path.open('rb') as handle:
    header = parse_json_line(handle.readline())
  return {} if header is None else header


def header_ms(path: Path) -> int | None:
  return parse_iso_ms(read_header(path).get('timestamp'))


def same_contents(first: Path, second: Path) -> bool:
  note_times(first)
  note_times(second)
  with reading(first):
    return first.samefile(second) or filecmp.cmp(first, second, shallow=False)


def same_stream(handle: IO[bytes], path: Path) -> bool:
  # Whether a written stream holds exactly the contents of a file.
  with reading(path):
    if not path.is_file() or handle.tell() != path.stat().st_size:
      return False
    note_times(path)
    handle.seek(0)
    with path.open('rb') as reader:
      while True:
        chunk = handle.read(1 << 20)
        if chunk != reader.read(len(chunk)):
          return False
        if not chunk:
          return True


def is_prefix(shorter: Path, longer: Path) -> bool:
  with reading(shorter):
    size = shorter.stat().st_size
    if size >= longer.stat().st_size:
      return False
    note_times(shorter)
    note_times(longer)
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
    candidate = uuid.uuid4().hex[:ENTRY_ID_LENGTH]
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
  times = original_times(source)
  try:
    with source.open('rb') as reader, temp.open('xb') as writer:
      shutil.copyfileobj(reader, writer)
      writer.flush()
      # The source is removed next, so the copy must be on disk first.
      os.fsync(writer.fileno())
    shutil.copystat(source, temp)
    os.utime(temp, ns=times)
    link_exclusive(temp, target)
    FIRST_TIMES[target.absolute()] = times
    REWRITTEN.add(target.absolute())
  finally:
    remove_file(temp, missing_ok=True, keep=target)


def replace_contents(path: Path, write: Callable[[BinaryIO, BinaryIO], None]) -> None:
  # A complete hidden copy with the new contents replaces the file in one step and
  # keeps its mode, access and modification times; a stop leaves a .copying file.
  times = original_times(path)
  temp = path.with_name(f'.{path.name}.{uuid.uuid4()}.copying')
  try:
    with path.open('rb') as reader, temp.open('xb') as writer:
      write(reader, writer)
      writer.flush()
      os.fsync(writer.fileno())
    shutil.copystat(path, temp)
    os.utime(temp, ns=times)
    temp.replace(path)
    REWRITTEN.add(path.absolute())
  finally:
    remove_file(temp, missing_ok=True)
  fsync_dir(path.parent)


def replace_header(path: Path, header: dict[str, object]) -> None:
  text = json.dumps(header, ensure_ascii=False, separators=(',', ':'))
  line = LONE_SURROGATE.sub(REPLACEMENT_CHARACTER, text).encode() + b'\n'

  def write(reader: BinaryIO, writer: BinaryIO) -> None:
    reader.readline()
    writer.write(line)
    shutil.copyfileobj(reader, writer)

  replace_contents(path, write)


def append_line(path: Path, line: bytes) -> None:
  def write(reader: BinaryIO, writer: BinaryIO) -> None:
    shutil.copyfileobj(reader, writer)
    size = writer.tell()
    if size > 0:
      reader.seek(size - 1)
      if reader.read(1) != b'\n':
        writer.write(b'\n')
    writer.write(line + b'\n')

  replace_contents(path, write)


def settle(
  temp: Path,
  target: Path,
  *,
  replace_target: bool,
  moment_ms: int | None = None,
) -> bool:
  # A new file gets the target name only when it is complete; an existing one is
  # replaced only when its contents differ. Returns whether the target changed.
  # The modification time comes with the contents, never as a later step.
  if not replace_target:
    set_mtime_if(temp, moment_ms)
    link_exclusive(temp, target)
    make_durable(target, list(target.parents))
    return True
  if same_contents(temp, target):
    return False
  # The run has read the target: its access time is the one from before the first read.
  atime, mtime = original_times(target)
  shutil.copystat(target, temp)
  os.utime(temp, ns=(atime, mtime))
  set_mtime_if(temp, moment_ms)
  FIRST_TIMES[target.absolute()] = atime, times_of(temp)[1]
  temp.replace(target)
  REWRITTEN.add(target.absolute())
  fsync_dir(target.parent)
  return True


def set_mtime_if(path: Path, moment_ms: int | None) -> None:
  if moment_ms is not None:
    set_mtime(path, moment_ms)


def link_exclusive(temp: Path, target: Path) -> None:
  # A complete hidden file gets the target name, an existing target is never replaced.
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
  first = FIRST_TIMES.pop(source.absolute(), None)
  if first is not None:
    FIRST_TIMES[target.absolute()] = first
  if source.absolute() in REWRITTEN:
    REWRITTEN.discard(source.absolute())
    REWRITTEN.add(target.absolute())
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


def file_key(name: str) -> str:
  # A file name as the archive matches it with a record: without its leading dots.
  return name.lstrip('.')


def archive_files(project_dir: Path) -> list[Path]:
  # Session files at any depth of a project directory, hidden names included; the
  # leftovers .copying and .moving never end in .jsonl.
  return sorted(path for path in project_dir.rglob('*.jsonl') if path.is_file())


def imported_entry(entry: dict[str, object]) -> bool:
  # An entry the opencode import writes: pi itself adds assistant messages of other APIs.
  kind = entry.get('type')
  if kind == 'message':
    message = object_of(entry.get('message'))
    return message.get('role') != 'assistant' or message.get('api') == OPENCODE_API
  if kind == 'custom':
    return (text_of(entry.get('customType')) or '').startswith(RECORD_PREFIX)
  return kind in IMPORTED_KINDS


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
    self.latest_timestamp: str | None = None
    self.names: list[dict[str, object]] = []
    self.foreign: set[str] = set()
    note_times(path)
    with reading(path), path.open('rb') as handle:
      for raw in handle:
        entry = parse_json_line(raw)
        if entry is not None:
          self.take(entry)

  def take(self, entry: dict[str, object]) -> None:
    kind = entry.get('type')
    self.latest_timestamp = later_stamp(
      self.latest_timestamp,
      text_of(entry.get('timestamp')),
    )
    if not imported_entry(entry):
      self.foreign.add(str(kind))
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
      self.names.append(entry)

  def carried(self) -> list[dict[str, object]]:
    # The names given after the import: the import names a session at its creation time.
    created = self.header.get('timestamp')
    return [entry for entry in self.names if entry.get('timestamp') != created]

  def append_name(self, name: str) -> None:
    if not os.access(self.path, os.W_OK):
      message = f'{self.path} is read-only'
      raise PermissionError(message)
    entry_id = unique_entry_id(self.entry_ids)
    # The name is as old as the latest entry, so the session keeps its place in the list.
    line = json.dumps(
      {
        'type': 'session_info',
        'id': entry_id,
        'parentId': self.last_id,
        'timestamp': self.latest_timestamp or now_iso(),
        'name': name,
      },
      ensure_ascii=False,
      separators=(',', ':'),
    )
    # The summary gets the name later and must not get ahead of the file.
    append_line(self.path, line.encode('utf-8'))
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


def has_session_id(path: Path, entry: ArchiveEntry) -> bool:
  return path.is_file() and read_header(path).get('id') == entry.session_id


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
    self.added: set[Path] = set()
    self.removed: dict[Path, list[ArchiveEntry]] = {}

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
    self.added.add(path)

  def remove(self, path: Path, entry: ArchiveEntry) -> None:
    current = self.entries(path)
    kept = [item for item in current if not item.same_session(entry)]
    self.removed.setdefault(path, []).extend(
      item for item in current if item.same_session(entry)
    )
    self.loaded[path] = kept
    self.dirty.add(path)

  def discard(self) -> None:
    self.dirty.clear()
    self.added.clear()
    self.removed.clear()

  def _write(self, path: Path, entries: list[ArchiveEntry]) -> None:
    data = {
      'version': SUMMARY_VERSION,
      'sessions': [item.to_summary() for item in entries],
    }
    write_json(path, data, dry_run=self.dry_run)

  def flush(self) -> list[Path]:
    # A record is on disk in some summary at every moment: the summaries that gain
    # records are written first, still with the records they lose, then the final
    # ones, and the emptied summaries go last.
    paths = sorted(self.dirty)
    written: set[Path] = set()
    if not self.dry_run and any(self.removed.get(path) for path in paths):
      for path in paths:
        entries = self.loaded[path]
        if path not in self.added or not entries:
          continue
        lost = [
          item for item in self.removed.get(path, [])
          if not any(item.same_session(kept) for kept in entries)
        ]
        if lost:
          self._write(path, [*entries, *lost])
        else:
          self._write(path, entries)
          written.add(path)
    for path in paths:
      if self.loaded[path] and path not in written:
        self._write(path, self.loaded[path])
    emptied: list[Path] = []
    for path in paths:
      if self.loaded[path]:
        continue
      if self.dry_run:
        say(f'would remove {path}')
      else:
        remove_file(path, missing_ok=True)
        emptied.append(path.parent)
    self.discard()
    return emptied


@dataclass
class ArchiveRecord:
  summary: Path
  entry: ArchiveEntry


class ArchiveIndex:
  # Every session file under the project directories of the archive tree, at any depth,
  # and every record of every summary there, both by session id.
  def __init__(self, tree_root: Path, summaries: Summaries) -> None:
    self.tree_root = tree_root
    self.summaries = summaries
    self.files: dict[str, list[Path]] = {}
    self.headers: dict[Path, dict[str, object]] = {}
    self.unreadable: list[Path] = []
    self.failed: list[tuple[Path, OSError]] = []
    self.blocked: dict[str, Path] = {}
    self.records: dict[str, list[ArchiveRecord]] = {}

  def scan(self) -> None:
    for project_dir in child_dirs(self.tree_root):
      for path in archive_files(project_dir):
        try:
          header = read_header(path)
        except OSError as error:
          self.failed.append((path, error))
          continue
        self.add(path, header)
      for summary in self._summaries(project_dir):
        for entry in self.summaries.entries(summary):
          record = ArchiveRecord(summary=summary, entry=entry)
          self.records.setdefault(entry.session_id, []).append(record)
    self._block_unreadable()

  def _block_unreadable(self) -> None:
    # The sessions an unreadable file may hold: the records with its file name and the
    # id in its name, which is <time>_<id>.jsonl.
    for path, _ in self.failed:
      name = file_key(path.name)
      ids = {
        record.entry.session_id
        for record in self.all_records() if file_key(record.entry.file_name) == name
      }
      match = FILE_TIME.match(name)
      if match is not None and name.endswith('.jsonl'):
        ids.add(name[match.end():-len('.jsonl')])
      for session_id in ids:
        self.blocked.setdefault(session_id, path)

  def _summaries(self, project_dir: Path) -> list[Path]:
    # A summary restored from its backup only in memory by a dry run counts too.
    found = set(project_dir.rglob(SUMMARY_NAME))
    found.update(path for path in self.summaries.loaded if project_dir in path.parents)
    return sorted(found)

  def add(self, path: Path, header: dict[str, object]) -> None:
    session_id = text_of(header.get('id'))
    if not session_id:
      self.unreadable.append(path)
      return
    self.headers[path] = header
    self.files.setdefault(session_id, []).append(path)

  def project_of(self, path: Path) -> Path:
    return self.tree_root / path.relative_to(self.tree_root).parts[0]

  def record_of(self, session_id: str, path: Path) -> ArchiveRecord | None:
    # The record in the project of the file wins over one in another project.
    records = self.records.get(session_id, [])
    project = self.project_of(path)
    near = [record for record in records if project in record.summary.parents]
    return next(iter(near or records), None)

  def has_content(self, project_dir: Path) -> bool:
    files = [path for paths in self.files.values() for path in paths]
    # A file without a readable header still makes the directory a project to report.
    files.extend(self.unreadable)
    files.extend(path for path, _ in self.failed)
    summaries = [record.summary for record in self.all_records()]
    return any(project_dir in path.parents for path in [*files, *summaries])

  def cwds_in(self, project_dir: Path) -> set[str]:
    cwds = {
      record.entry.cwd
      for records in self.records.values()
      for record in records if project_dir in record.summary.parents
    }
    for path, header in self.headers.items():
      cwd = text_of(header.get('cwd'))
      if cwd and project_dir in path.parents:
        cwds.add(canonical_cwd(cwd))
    return cwds

  def all_records(self) -> list[ArchiveRecord]:
    return [record for records in self.records.values() for record in records]

  def origin_dirs(self) -> list[Path]:
    records = self.all_records()
    dirs = {Path(record.entry.original_path).parent
            for record in records}
    return sorted(dirs)


@dataclass
class Placement:
  # Where --repair puts an archived session file and its record.
  path: Path
  cwd: str
  slot: TreeSlot
  entry: ArchiveEntry
  records: list[ArchiveRecord]
  header: dict[str, object]

  @property
  def target(self) -> Path:
    return self.slot.day_dir / self.path.name


class OriginMap:
  # The original paths the records have after --repair and the ones they had before.
  def __init__(self) -> None:
    self.moved: dict[str, str] = {}
    self.known: set[str] = set()
    self.by_name: dict[str, list[str]] = {}

  def add(self, origin: str) -> None:
    if origin in self.known:
      return
    self.known.add(origin)
    self.by_name.setdefault(file_key(Path(origin).name), []).append(origin)


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
    say(f'naming {session.path.name}{ELLIPSIS}')
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
        errors='replace',
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


class Progress:
  # A line with the processed count and the counters so far, once in a while.
  def __init__(self, counts: Counter[str]) -> None:
    self.counts = counts
    self.total: int | None = None
    self.done = 0
    self.shown = time.monotonic()

  def start(self, total: int | None) -> None:
    self.total = total
    self.done = 0
    self.shown = time.monotonic()

  def step(self) -> None:
    self.done += 1
    self.show()

  def show(self) -> None:
    now = time.monotonic()
    if now - self.shown < PROGRESS_SECONDS:
      return
    self.shown = now
    total = '' if self.total is None else f'/{self.total}'
    counts = ''.join(f', {key}: {value}' for key, value in sorted(self.counts.items()))
    say(f'processed {self.done}{total}{counts}')


def whole(value: object) -> int:
  return value if isinstance(value, int) and not isinstance(value, bool) else 0


def object_of(value: object) -> dict[str, object]:
  return value if isinstance(value, dict) else {}


def without_surrogates(value: object) -> object:
  # A lone surrogate of a cut text in the database becomes a replacement character.
  if isinstance(value, str):
    return LONE_SURROGATE.sub(REPLACEMENT_CHARACTER, value)
  if isinstance(value, list):
    return [without_surrogates(item) for item in value]
  if isinstance(value, dict):
    return {
      without_surrogates(key): without_surrogates(item)
      for key, item in value.items()
    }
  return value


def load_object(text: object) -> dict[str, object]:
  if not isinstance(text, str):
    return {}
  try:
    value = json.loads(text)
  except ValueError:
    return {}
  return object_of(without_surrogates(value))


def decode_text(raw: bytes) -> str:
  return raw.decode('utf-8', 'replace')


def projects_path(data_dir: Path) -> Path:
  configured = os.environ.get('PI_WEB_PROJECTS_FILE', '')
  return absolute(Path(configured)) if configured else data_dir / PROJECTS_NAME


def registered_projects(data_dir: Path) -> set[str]:
  projects = read_json_object(projects_path(data_dir)).get('projects')
  paths: set[str] = set()
  for project in projects if isinstance(projects, list) else []:
    path = text_of(object_of(project).get('path'))
    if path:
      paths.add(canonical_cwd(path))
  return paths


def get_path(root: object, path: Sequence[str | int]) -> object:
  # The value at a path of object keys and list indexes, or MISSING.
  current = root
  for key in path:
    if isinstance(key, str) and isinstance(current, dict) and key in current:
      current = current[key]
      continue
    if isinstance(key, int) and isinstance(current, list) and 0 <= key < len(current):
      current = current[key]
      continue
    return MISSING
  return current


def child_of(container: object, key: str | int, fresh: object) -> object:
  if isinstance(container, dict) and isinstance(key, str):
    return container.setdefault(key, fresh)
  if isinstance(container, list) and isinstance(key, int):
    container.extend([None] * (key + 1 - len(container)))
    if container[key] is None:
      container[key] = fresh
    return container[key]
  message = f'cannot follow {key!r} in a JSON path'
  raise TypeError(message)


def put_path(root: dict[str, object], path: Sequence[str | int], value: object) -> None:
  # Sets the value at a path, creating the missing objects and lists on the way.
  current: object = root
  for key, following in zip(path, path[1:]):
    current = child_of(current, key, [] if isinstance(following, int) else {})
  last = path[-1]
  if isinstance(current, dict) and isinstance(last, str):
    current[last] = value
  elif isinstance(current, list) and isinstance(last, int):
    current.extend([None] * (last + 1 - len(current)))
    current[last] = value
  else:
    message = f'cannot set {last!r} in a JSON path'
    raise TypeError(message)


def drop_path(root: object, path: Sequence[str | int]) -> None:
  holder = get_path(root, path[:-1])
  key = path[-1]
  if isinstance(holder, dict) and isinstance(key, str):
    holder.pop(key, None)


def same_json(first: object, second: object) -> bool:
  # Equal as JSON: a number does not equal a boolean and an integer not a float.
  if isinstance(first, str) or isinstance(second, str):
    return isinstance(first, str) and isinstance(second, str) and first == second
  return json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def without_cookies(data: dict[str, object]) -> dict[str, object]:
  # The cookies an API error response set are not kept.
  headers = get_path(data, ('error', 'data', 'responseHeaders'))
  if not isinstance(headers, dict):
    return data
  cookies = [key for key in headers if str(key).lower() == SET_COOKIE]
  if not cookies:
    return data
  cleaned = copy.deepcopy(data)
  for key in cookies:
    drop_path(cleaned, ('error', 'data', 'responseHeaders', key))
  return cleaned


@dataclass(frozen=True)
class DiffPart:
  value: str
  added: bool = False
  removed: bool = False

  @property
  def changed(self) -> bool:
    return self.added or self.removed


@dataclass(frozen=True)
class DiffRun:
  # A run of equal, added or removed lines after the run before it.
  count: int
  added: bool
  removed: bool
  previous: DiffRun | None


@dataclass
class DiffPath:
  old_pos: int
  last: DiffRun | None

  def then(self, *, added: bool, step: int) -> DiffPath:
    # One more added or removed line at the end of the path, as addToPath of jsdiff.
    removed = not added
    last = self.last
    if last is not None and last.added == added and last.removed == removed:
      run = DiffRun(
        count=last.count + 1,
        added=added,
        removed=removed,
        previous=last.previous,
      )
    else:
      run = DiffRun(count=1, added=added, removed=removed, previous=last)
    return DiffPath(old_pos=self.old_pos + step, last=run)


def line_tokens(text: str) -> list[str]:
  # The lines of jsdiff diffLines: each one with its line break, empty ones dropped.
  pieces = DIFF_LINE_BREAK.split(text)
  if not pieces[-1]:
    pieces.pop()
  tokens: list[str] = []
  for index, piece in enumerate(pieces):
    if index % 2:
      tokens[-1] += piece
    else:
      tokens.append(piece)
  return [token for token in tokens if token]


class LineDiff:
  # diffLines of the npm diff 8.0.4 behind the edit diffs of pi, ported line for line:
  # the Myers algorithm with the same choice between paths of equal length.
  def __init__(self, old: str, new: str) -> None:
    self.old = line_tokens(old)
    self.new = line_tokens(new)
    self.best: dict[int, DiffPath | None] = {}
    total = len(self.old) + len(self.new)
    self.lowest = -total - 1
    self.highest = total + 1

  def parts(self) -> list[DiffPart]:
    start = DiffPath(old_pos=-1, last=None)
    if self._done(start, self._common(start, 0)):
      return self._values(start.last)
    self.best = {
      0: start,
    }
    limit = min(len(self.old) + len(self.new), DIFF_EDIT_LIMIT)
    for length in range(1, limit + 1):
      found = self._extend(length)
      if found is not None:
        return self._values(found.last)
    return self._blocks()

  def _extend(self, length: int) -> DiffPath | None:
    diagonal = max(self.lowest, -length)
    while diagonal <= min(self.highest, length):
      path = self._choose(diagonal)
      if path is None:
        self.best[diagonal] = None
      else:
        new_pos = self._common(path, diagonal)
        if self._done(path, new_pos):
          return path
        self.best[diagonal] = path
        if path.old_pos + 1 >= len(self.old):
          self.highest = min(self.highest, diagonal - 1)
        if new_pos + 1 >= len(self.new):
          self.lowest = max(self.lowest, diagonal + 1)
      diagonal += 2
    return None

  def _choose(self, diagonal: int) -> DiffPath | None:
    remove = self.best.get(diagonal - 1)
    add = self.best.get(diagonal + 1)
    if remove is not None:
      self.best[diagonal - 1] = None
    if add is not None and not 0 <= add.old_pos - diagonal < len(self.new):
      add = None
    if remove is not None and remove.old_pos + 1 >= len(self.old):
      remove = None
    if add is not None and (remove is None or remove.old_pos < add.old_pos):
      return add.then(added=True, step=0)
    if remove is not None:
      return remove.then(added=False, step=1)
    return None

  def _common(self, path: DiffPath, diagonal: int) -> int:
    old_pos = path.old_pos
    new_pos = old_pos - diagonal
    count = 0
    while (
      new_pos + 1 < len(self.new) and old_pos + 1 < len(self.old)
      and self.old[old_pos + 1] == self.new[new_pos + 1]
    ):
      new_pos += 1
      old_pos += 1
      count += 1
    if count:
      path.last = DiffRun(count=count, added=False, removed=False, previous=path.last)
    path.old_pos = old_pos
    return new_pos

  def _done(self, path: DiffPath, new_pos: int) -> bool:
    return path.old_pos + 1 >= len(self.old) and new_pos + 1 >= len(self.new)

  def _values(self, last: DiffRun | None) -> list[DiffPart]:
    runs: list[DiffRun] = []
    while last is not None:
      runs.append(last)
      last = last.previous
    parts: list[DiffPart] = []
    old_pos = 0
    new_pos = 0
    for run in reversed(runs):
      if run.removed:
        value = ''.join(self.old[old_pos:old_pos + run.count])
        old_pos += run.count
      else:
        value = ''.join(self.new[new_pos:new_pos + run.count])
        new_pos += run.count
        if not run.added:
          old_pos += run.count
      parts.append(DiffPart(value=value, added=run.added, removed=run.removed))
    return parts

  def _blocks(self) -> list[DiffPart]:
    # Too many changes to pair the lines in time: the common head and tail stay, the
    # rest is one removed and one added block.
    shortest = min(len(self.old), len(self.new))
    head = 0
    while head < shortest and self.old[head] == self.new[head]:
      head += 1
    tail = 0
    while tail < shortest - head and self.old[-1 - tail] == self.new[-1 - tail]:
      tail += 1
    blocks = [
      DiffPart(value=''.join(self.new[:head])),
      DiffPart(value=''.join(self.old[head:len(self.old) - tail]), removed=True),
      DiffPart(value=''.join(self.new[head:len(self.new) - tail]), added=True),
      DiffPart(value=''.join(self.new[len(self.new) - tail:])),
    ]
    return [part for part in blocks if part.value]


class DiffView:
  # generateDiffString of pi: the changed lines with their numbers, four lines of
  # context around them and an ellipsis line for every skipped stretch.
  def __init__(self, parts: list[DiffPart], old: str, new: str) -> None:
    self.width = len(str(max(len(old.split('\n')), len(new.split('\n')))))
    self.old_line = 1
    self.new_line = 1
    self.lines: list[str] = []
    self.first_changed: int | None = None
    changed = False
    for index, part in enumerate(parts):
      rows = part.value.split('\n')
      if not rows[-1]:
        rows.pop()
      if part.changed:
        self._change(part, rows)
      else:
        following = index + 1 < len(parts) and parts[index + 1].changed
        self._context(rows, leading=changed, trailing=following)
      changed = part.changed
    self.text = '\n'.join(self.lines)

  def _change(self, part: DiffPart, rows: list[str]) -> None:
    if self.first_changed is None:
      self.first_changed = self.new_line
    for row in rows:
      if part.added:
        self.lines.append(f'+{self.new_line:>{self.width}} {row}')
        self.new_line += 1
      else:
        self.lines.append(f'-{self.old_line:>{self.width}} {row}')
        self.old_line += 1

  def _context(self, rows: list[str], *, leading: bool, trailing: bool) -> None:
    if leading and trailing and len(rows) <= DIFF_CONTEXT * 2:
      self._show(rows)
    elif leading and trailing:
      self._show(rows[:DIFF_CONTEXT])
      self._skip(len(rows) - DIFF_CONTEXT - DIFF_CONTEXT)
      self._show(rows[-DIFF_CONTEXT:])
    elif leading:
      self._show(rows[:DIFF_CONTEXT])
      if len(rows) > DIFF_CONTEXT:
        self._skip(len(rows) - DIFF_CONTEXT)
    elif trailing:
      skipped = max(0, len(rows) - DIFF_CONTEXT)
      if skipped:
        self._skip(skipped)
      self._show(rows[skipped:])
    else:
      self._skip(len(rows), shown=False)

  def _show(self, rows: list[str]) -> None:
    for row in rows:
      self.lines.append(f' {self.old_line:>{self.width}} {row}')
      self.old_line += 1
      self.new_line += 1

  def _skip(self, count: int, *, shown: bool = True) -> None:
    if shown:
      self.lines.append(' ' + ' ' * self.width + ' ...')
    self.old_line += count
    self.new_line += count


def patch_lines(value: str) -> list[str]:
  # splitLines of jsdiff: every line keeps its line break, the last one may have none.
  lines = [line + '\n' for line in value.split('\n')]
  if value.endswith('\n'):
    lines.pop()
  else:
    lines[-1] = lines[-1][:-1]
  return lines


@dataclass
class Hunk:
  old_start: int
  old_lines: int
  new_start: int
  new_lines: int
  lines: list[str]

  def render(self) -> list[str]:
    old_start = self.old_start - 1 if self.old_lines == 0 else self.old_start
    new_start = self.new_start - 1 if self.new_lines == 0 else self.new_start
    rendered = [f'@@ -{old_start},{self.old_lines} +{new_start},{self.new_lines} @@']
    for line in self.lines:
      if line.endswith('\n'):
        rendered.append(line[:-1])
      else:
        rendered.extend((line, NO_NEWLINE))
    return rendered


class UnifiedPatch:
  # createTwoFilesPatch of jsdiff with four lines of context and only the file headers,
  # as generateUnifiedPatch of pi writes it.
  def __init__(self, path: str, parts: list[DiffPart]) -> None:
    self.hunks: list[Hunk] = []
    self.old_line = 1
    self.new_line = 1
    self.old_start = 0
    self.new_start = 0
    self.current: list[str] = []
    every = [*parts, DiffPart(value='')]
    previous: list[str] = []
    for index, part in enumerate(every):
      rows = [] if index == len(every) - 1 else patch_lines(part.value)
      if part.changed:
        self._change(part, rows, previous)
      else:
        self._equal(rows, closing=index >= len(every) - 2)
      previous = rows
    lines = [f'--- {path}', f'+++ {path}']
    for hunk in self.hunks:
      lines.extend(hunk.render())
    self.text = '\n'.join(lines) + '\n'

  def _change(self, part: DiffPart, rows: list[str], previous: list[str]) -> None:
    if not self.old_start:
      self.current = [' ' + row for row in previous[-DIFF_CONTEXT:]]
      self.old_start = self.old_line - len(self.current)
      self.new_start = self.new_line - len(self.current)
    sign = '+' if part.added else '-'
    self.current.extend(sign + row for row in rows)
    if part.added:
      self.new_line += len(rows)
    else:
      self.old_line += len(rows)

  def _equal(self, rows: list[str], *, closing: bool) -> None:
    if self.old_start and len(rows) <= DIFF_CONTEXT * 2 and not closing:
      self.current.extend(' ' + row for row in rows)
    elif self.old_start:
      size = min(len(rows), DIFF_CONTEXT)
      self.current.extend(' ' + row for row in rows[:size])
      self.hunks.append(
        Hunk(
          old_start=self.old_start,
          old_lines=self.old_line - self.old_start + size,
          new_start=self.new_start,
          new_lines=self.new_line - self.new_start + size,
          lines=self.current,
        ),
      )
      self.old_start = 0
      self.new_start = 0
      self.current = []
    self.old_line += len(rows)
    self.new_line += len(rows)


@dataclass
class ShownHunk:
  old_line: int
  new_line: int
  old_left: int
  new_left: int
  rows: list[str] = field(default_factory=list)


class HunkView:
  # The display diff of pi built from the hunks of a unified diff without the file
  # texts: the numbers come from the hunk headers, an ellipsis line parts the hunks.
  def __init__(self, patch: str) -> None:
    self.hunks: list[ShownHunk] = []
    self.last = 1
    for raw in patch.split('\n'):
      self._take(raw[:-1] if raw.endswith('\r') else raw)
    self.width = len(str(self.last))
    self.first_changed: int | None = None
    lines: list[str] = []
    for index, hunk in enumerate(self.hunks):
      if index:
        lines.append(' ' + ' ' * self.width + ' ...')
      lines.extend(self._render(hunk))
    self.text = '\n'.join(lines)

  def _take(self, line: str) -> None:
    match = HUNK_HEADER.match(line)
    if match is not None:
      old_count = 1 if match[2] is None else int(match[2])
      new_count = 1 if match[4] is None else int(match[4])
      # An empty side starts one line before its first line, as unified diffs write it.
      old_line = int(match[1]) + (0 if old_count else 1)
      new_line = int(match[3]) + (0 if new_count else 1)
      self.last = max(self.last, old_line + old_count - 1, new_line + new_count - 1)
      self.hunks.append(ShownHunk(old_line, new_line, old_count, new_count))
      return
    hunk = self.hunks[-1] if self.hunks else None
    if hunk is None or (hunk.old_left <= 0 and hunk.new_left <= 0):
      return
    sign = line[:1] or ' '
    if sign == '\\':
      return
    if sign not in {' ', '-', '+'}:
      # Not a line of a hunk: the hunk ends here.
      hunk.old_left = 0
      hunk.new_left = 0
      return
    if sign != '+':
      hunk.old_left -= 1
    if sign != '-':
      hunk.new_left -= 1
    hunk.rows.append(sign + line[1:])

  def _render(self, hunk: ShownHunk) -> list[str]:
    old_line = hunk.old_line
    new_line = hunk.new_line
    lines: list[str] = []
    for row in hunk.rows:
      sign = row[0]
      if sign != ' ' and self.first_changed is None:
        self.first_changed = new_line
      number = new_line if sign == '+' else old_line
      lines.append(f'{sign}{number:>{self.width}} {row[1:]}')
      if sign != '+':
        old_line += 1
      if sign != '-':
        new_line += 1
    return lines


def normalized_text(text: str) -> str:
  # The text pi diffs: without a byte order mark and with LF line breaks only.
  bare = text[1:] if text.startswith('\ufeff') else text
  return bare.replace('\r\n', '\n').replace('\r', '\n')


def text_details(path: str, before: str, after: str) -> dict[str, object]:
  old = normalized_text(before)
  new = normalized_text(after)
  parts = LineDiff(old, new).parts()
  view = DiffView(parts, old, new)
  details: dict[str, object] = {
    'diff': view.text,
    'patch': UnifiedPatch(path, parts).text,
  }
  if view.first_changed is not None:
    details['firstChangedLine'] = view.first_changed
  return details


def patch_details(patch: str) -> dict[str, object]:
  view = HunkView(patch)
  details: dict[str, object] = {
    'diff': view.text,
    'patch': patch,
  }
  if view.first_changed is not None:
    details['firstChangedLine'] = view.first_changed
  return details


def file_details(path: str, change: dict[str, object]) -> dict[str, object] | None:
  # From both texts of the file when opencode kept them, else from its unified diff.
  before = text_of(change.get('before'))
  after = text_of(change.get('after'))
  if before is not None and after is not None:
    return text_details(path, before, after)
  patch = text_of(change.get('patch')) or text_of(change.get('diff'))
  return patch_details(patch) if patch else None


def zero_based_reads(version: str | None) -> bool:
  match = OPENCODE_VERSION.match(version or '')
  if match is None:
    return False
  return tuple(int(part) for part in match.groups()) <= LAST_ZERO_BASED_READ


def pi_read_offset(offset: object, output: object, version: str | None) -> object:
  # opencode up to 1.1.59 counted lines from 0 and wrote <file>, later ones count from
  # 1, take 0 for 1 and write <path>; pi counts from 1.
  if not isinstance(offset, int) or isinstance(offset, bool):
    return offset
  shown = output if isinstance(output, str) else ''
  if shown.startswith(OLD_READ_OUTPUT):
    return offset + 1
  if not shown.startswith(NEW_READ_OUTPUT) and zero_based_reads(version):
    return offset + 1
  return offset or 1


def pi_timeout(value: object) -> object:
  # opencode takes milliseconds, pi seconds.
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    return value
  if isinstance(value, int) and value % MS_PER_SECOND == 0:
    return value // MS_PER_SECOND
  return value / MS_PER_SECOND


def pi_argument(
  tool: str,
  key: str,
  value: object,
  *,
  output: object,
  version: str | None,
) -> object:
  if tool == 'read' and key == 'offset':
    return pi_read_offset(value, output, version)
  if tool == 'bash' and key == 'timeout':
    return pi_timeout(value)
  return value


class PiArguments:
  # The pi tool name and arguments of an opencode tool call, with the input keys whose
  # values went into the arguments unchanged and where they went.
  def __init__(
    self,
    tool: str,
    given: dict[str, object],
    *,
    output: object,
    version: str | None,
  ) -> None:
    self.name = tool
    mapping: dict[str, tuple[str | int, ...]] = {}
    if tool in PI_TOOLS:
      self.name, mapping = PI_TOOLS[tool]
    self.values: dict[str, object] = {}
    self.kept: list[tuple[str, tuple[str | int, ...]]] = []
    for key, path in mapping.items():
      if key not in given:
        continue
      value = given[key]
      converted = pi_argument(tool, key, value, output=output, version=version)
      put_path(self.values, path, converted)
      if same_json(converted, value):
        self.kept.append((key, path))
    # Keys without a pi counterpart keep their names unless pi uses the name.
    for key, value in given.items():
      if key not in mapping and key not in self.values:
        self.values[key] = value
        self.kept.append((key, (key, )))


def patch_sections(text: str) -> list[str] | None:
  # The files of an apply_patch text, each one wrapped as a patch of its own.
  lines = patch_lines(text)
  bare = [line.strip() for line in lines]
  if PATCH_BEGIN not in bare:
    return None
  begin = bare.index(PATCH_BEGIN)
  if PATCH_END not in bare[begin:]:
    return None
  end = bare.index(PATCH_END, begin)
  sections: list[list[str]] = []
  for line, stripped in zip(lines[begin + 1:end], bare[begin + 1:end]):
    if stripped.startswith(PATCH_FILE_HEADERS):
      sections.append([line])
    elif sections:
      sections[-1].append(line)
    elif stripped:
      return None
  head = PATCH_BEGIN + '\n'
  tail = PATCH_END + '\n'
  return [''.join([head, *section, tail]) for section in sections]


def patch_outputs(output: str, files: list[dict[str, object]]) -> list[str] | None:
  # The output of apply_patch for each file: its summary line and its LSP errors.
  if not output.startswith(PATCH_OUTPUT_HEAD):
    return None
  body = output[len(PATCH_OUTPUT_HEAD):]
  starts = [match.start() for match in PATCH_LSP_BLOCK.finditer(body)]
  rows = body[:starts[0] if starts else len(body)].split('\n')
  relative = [text_of(item.get('relativePath')) or '' for item in files]
  if len(rows) != len(files) or any(row[2:] != path for row, path in zip(rows, relative)):
    return None
  texts = [PATCH_OUTPUT_HEAD + row for row in rows]
  bounds = [*starts, len(body)]
  for begin, end in zip(bounds, bounds[1:]):
    match = PATCH_LSP_BLOCK.match(body, begin)
    path = '' if match is None else match[1]
    if relative.count(path) != 1:
      return None
    texts[relative.index(path)] += body[begin:end]
  return texts


@dataclass(frozen=True)
class Moved:
  # A value of opencode kept only in an entry of pi: where it was and where it is now.
  source: tuple[str | int, ...]
  entry: str
  target: tuple[str | int, ...]

  def record(self) -> dict[str, object]:
    return {
      'from': list(self.source),
      'entry': self.entry,
      'to': list(self.target),
    }


@dataclass(frozen=True)
class ToolLink:
  # A value of an opencode tool part that a pi tool call or its result holds unchanged.
  source: tuple[str | int, ...]
  call: int
  in_result: bool
  target: tuple[str | int, ...]


@dataclass
class ToolCall:
  call_id: str
  name: str
  arguments: dict[str, object]
  content: list[dict[str, object]]
  is_error: bool
  details: dict[str, object] | None = None
  block_index: int = 0

  def block(self) -> dict[str, object]:
    return {
      'type': 'toolCall',
      'id': self.call_id,
      'name': self.name,
      'arguments': self.arguments,
    }

  def result(self, moment_ms: int) -> dict[str, object]:
    result: dict[str, object] = {
      'role': 'toolResult',
      'toolCallId': self.call_id,
      'toolName': self.name,
      'content': self.content,
    }
    if self.details is not None:
      result['details'] = self.details
    result['isError'] = self.is_error
    result['timestamp'] = moment_ms
    return result


@dataclass(frozen=True)
class Attachment:
  mime: str
  url: str

  @classmethod
  def of(cls, part: dict[str, object]) -> Attachment:
    return cls(
      mime=text_of(part.get('mime')) or 'application/octet-stream',
      url=text_of(part.get('url')) or '',
    )

  def block(self) -> dict[str, object]:
    # Images stay images; any other file becomes a note with its type and size.
    header, comma, payload = self.url.partition(',')
    if not comma or not header.startswith('data:'):
      return {
        'type': 'text',
        'text': f'[attachment {self.mime}, linked file omitted]',
      }
    mime = header[len('data:'):].split(';', 1)[0] or self.mime
    if header.endswith(';base64'):
      if mime.strip().lower() in PI_IMAGE_TYPES:
        # Providers compare the type as is, so it goes in lower case.
        return {
          'type': 'image',
          'data': payload,
          'mimeType': mime.strip().lower(),
        }
      size = len(payload.rstrip('=')) * 3 // 4
    else:
      size = len(unquote_to_bytes(payload))
    return {
      'type': 'text',
      'text': f'[attachment {mime}, {size} bytes omitted]',
    }


@dataclass(frozen=True)
class OpencodePart:
  part_id: str
  data: dict[str, object]
  # The other columns of the part row: times and the like.
  columns: dict[str, object] = field(default_factory=dict)


class ToolPlan:
  # The pi tool calls of one opencode tool part: one call, or one per file of an
  # apply_patch on several files, with the values of the part they hold unchanged.
  def __init__(self, part: OpencodePart, version: str | None, finished: int) -> None:
    self.part = part
    self.state = object_of(part.data.get('state'))
    self.tool = text_of(part.data.get('tool')) or ''
    # The tool result refers to the call by this id.
    self.call_id = text_of(part.data.get('callID')) or part.part_id
    times = object_of(self.state.get('time'))
    self.moment = whole(times.get('end')) or whole(times.get('start')) or finished
    self.calls: list[ToolCall] = []
    self.links: list[ToolLink] = []
    if not self._patches():
      self._single(version)
    for index in range(len(self.calls)):
      self._link_values(index)

  @property
  def completed(self) -> bool:
    return self.state.get('status') == 'completed'

  def _single(self, version: str | None) -> None:
    given = object_of(self.state.get('input'))
    arguments = PiArguments(
      self.tool,
      given,
      output=self.state.get('output'),
      version=version,
    )
    self.calls.append(
      ToolCall(
        call_id=self.call_id,
        name=arguments.name,
        arguments=arguments.values,
        content=self._content(),
        is_error=not self.completed,
        details=self._details(arguments.values),
      ),
    )
    self.links.extend(
      ToolLink(source=('state', 'input', key), call=0, in_result=False, target=path)
      for key, path in arguments.kept
    )

  def _details(self, arguments: dict[str, object]) -> dict[str, object] | None:
    if not self.completed:
      return None
    metadata = object_of(self.state.get('metadata'))
    filediff = object_of(metadata.get('filediff'))
    path = text_of(arguments.get('path')) or ''
    if self.tool == 'edit':
      diff = text_of(metadata.get('diff'))
      return file_details(path, filediff) or (patch_details(diff) if diff else None)
    before = text_of(filediff.get('before'))
    after = text_of(filediff.get('after'))
    if self.tool == 'write' and before is not None and after is not None:
      # pi writes no details for write: a convention of this script.
      return text_details(path, before, after)
    return None

  def _content(self) -> list[dict[str, object]]:
    status = self.state.get('status')
    if status == 'completed':
      output = self.state.get('output')
      content: list[dict[str, object]] = [{
        'type': 'text',
        'text': output if isinstance(output, str) else '',
      }]
      attachments = self.state.get('attachments')
      if isinstance(attachments, list):
        content.extend(
          Attachment.of(item).block() for item in attachments if isinstance(item, dict)
        )
      return content
    if status == 'error':
      return [{
        'type': 'text',
        'text': text_of(self.state.get('error')) or 'error',
      }]
    return [{
      'type': 'text',
      'text': UNFINISHED_TOOL,
    }]

  def _patch_files(self) -> list[dict[str, object]]:
    if self.tool != 'apply_patch' or not self.completed:
      return []
    items = object_of(self.state.get('metadata')).get('files')
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
      return []
    return [object_of(item) for item in items]

  def _patches(self) -> bool:
    # An apply_patch on several files becomes one call per file with its own result.
    files = self._patch_files()
    if len(files) == 1:
      self._one_patch(files[0])
      return True
    patch_text = text_of(object_of(self.state.get('input')).get('patchText')) or ''
    sections = patch_sections(patch_text) if files else None
    if sections is None or len(sections) != len(files):
      return False
    output = text_of(self.state.get('output')) or ''
    outputs = patch_outputs(output, files) or [output, *[''] * (len(files) - 1)]
    extra = self._content()[1:]
    for index, change in enumerate(files):
      path = text_of(change.get('filePath')) or ''
      content: list[dict[str, object]] = [{
        'type': 'text',
        'text': outputs[index],
      }]
      if index == 0:
        content.extend(extra)
      self.calls.append(
        ToolCall(
          call_id=f'{self.call_id}-{index + 1}',
          name='apply_patch',
          arguments={
            'path': path,
            'patchText': sections[index],
          },
          content=content,
          is_error=False,
          details=file_details(path, change),
        ),
      )
    return True

  def _one_patch(self, change: dict[str, object]) -> None:
    path = text_of(change.get('filePath')) or ''
    arguments: dict[str, object] = {
      'path': path,
    }
    for key, value in object_of(self.state.get('input')).items():
      if key not in arguments:
        arguments[key] = value
        source = ('state', 'input', key)
        target = (key, )
        self.links.append(ToolLink(source=source, call=0, in_result=False, target=target))
    self.calls.append(
      ToolCall(
        call_id=self.call_id,
        name='apply_patch',
        arguments=arguments,
        content=self._content(),
        is_error=False,
        details=file_details(path, change),
      ),
    )

  def _link_values(self, index: int) -> None:
    call = self.calls[index]
    result = call.result(self.moment)
    for source, target in RESULT_LINKS:
      # Each call of an apply_patch on several files holds the patch of its file.
      several = len(self.calls) > 1 and source[:3] == ('state', 'metadata', 'files')
      linked = (*source[:3], index, *source[4:]) if several else source
      self._link(
        result,
        ToolLink(source=linked, call=index, in_result=True, target=target),
      )
    for source, target in CALL_LINKS:
      self._link(
        call.arguments,
        ToolLink(source=source, call=index, in_result=False, target=target),
      )

  def _link(self, holder: object, link: ToolLink) -> None:
    value = get_path(self.part.data, link.source)
    held = get_path(holder, link.target)
    if value is MISSING or held is MISSING or not same_json(value, held):
      return
    if all(item.source != link.source for item in self.links):
      self.links.append(link)

  def record(
    self,
    message_id: str,
    assistant_id: str,
    result_ids: list[str],
  ) -> dict[str, object]:
    moved = [self._moved(link, assistant_id, result_ids) for link in self.links]
    return {
      'messageID': message_id,
      'callID': self.call_id,
      **stored_part(self.part, moved),
    }

  def _moved(self, link: ToolLink, assistant_id: str, result_ids: list[str]) -> Moved:
    if link.in_result:
      return Moved(link.source, result_ids[link.call], ('message', *link.target))
    index = self.calls[link.call].block_index
    target = ('message', 'content', index, 'arguments', *link.target)
    return Moved(link.source, assistant_id, target)


def amount(value: object) -> float:
  return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def opencode_usage(
  data: dict[str, object],
  parts: list[dict[str, object]],
) -> dict[str, object]:
  # The message keeps the tokens of its last step only, so its steps are summed.
  steps = [part for part in parts if part.get('type') == 'step-finish'] or [data]
  sums: Counter[str] = Counter()
  cost = 0.0
  for step in steps:
    tokens = object_of(step.get('tokens'))
    cache = object_of(tokens.get('cache'))
    counts = {
      'input': whole(tokens.get('input')),
      # pi counts reasoning as a part of the output.
      'output': whole(tokens.get('output')) + whole(tokens.get('reasoning')),
      'reasoning': whole(tokens.get('reasoning')),
      'cacheRead': whole(cache.get('read')),
      'cacheWrite': whole(cache.get('write')),
    }
    sums.update(counts)
    sums['totalTokens'] += whole(tokens.get('total')) or sum(
      counts[key] for key in ('input', 'output', 'cacheRead', 'cacheWrite')
    )
    cost += amount(step.get('cost'))
  keys = ('input', 'output', 'cacheRead', 'cacheWrite', 'totalTokens')
  usage: dict[str, object] = {
    key: sums[key]
    for key in keys
  }
  usage['cost'] = {
    'input': 0,
    'output': 0,
    'cacheRead': 0,
    'cacheWrite': 0,
    'total': cost,
  }
  if sums['reasoning']:
    usage['reasoning'] = sums['reasoning']
  return usage


def has_opencode_usage(data: dict[str, object], parts: list[dict[str, object]]) -> bool:
  if any(part.get('type') == 'step-finish' for part in parts):
    return True
  usage = opencode_usage(data, [])
  keys = ('input', 'output', 'cacheRead', 'cacheWrite', 'totalTokens')
  cost = amount(object_of(usage['cost']).get('total'))
  return bool(cost) or any(whole(usage[key]) for key in keys)


def opencode_stop_reason(data: dict[str, object]) -> str:
  error = data.get('error')
  if isinstance(error, dict):
    return 'aborted' if error.get('name') == 'MessageAbortedError' else 'error'
  return OPENCODE_STOP_REASONS.get(text_of(data.get('finish')) or '', 'stop')


def opencode_error(data: dict[str, object]) -> str | None:
  error = data.get('error')
  if not isinstance(error, dict):
    return None
  name = text_of(error.get('name')) or 'error'
  message = text_of(object_of(error.get('data')).get('message'))
  return f'{name}: {message}' if message else name


def assistant_text(part: dict[str, object]) -> dict[str, object] | None:
  kind = part.get('type')
  text = text_of(part.get('text'))
  if kind == 'text' and text:
    return {
      'type': 'text',
      'text': text,
    }
  if kind == 'reasoning' and text:
    return {
      'type': 'thinking',
      'thinking': text,
    }
  return None


@dataclass(frozen=True)
class OpencodeSession:
  session_id: str
  parent_id: str | None
  directory: str
  title: str
  created: int
  updated: int
  archived: int | None
  # The whole session row as opencode stored it.
  row: dict[str, object] = field(default_factory=dict, compare=False)

  @classmethod
  def of(cls, row: dict[str, object]) -> OpencodeSession:
    archived = row.get('time_archived')
    return cls(
      session_id=str(row.get('id')),
      parent_id=text_of(row.get('parent_id')),
      directory=text_of(row.get('directory')) or '',
      title=text_of(row.get('title')) or '',
      created=whole(row.get('time_created')),
      updated=whole(row.get('time_updated')),
      archived=archived if isinstance(archived, int) else None,
      row=row,
    )

  @property
  def file_name(self) -> str:
    stamp = iso_ms(self.created).replace(':', '-').replace('.', '-')
    return f'{stamp}_{self.session_id}.jsonl'

  @property
  def name(self) -> str | None:
    # The title opencode gives a new session is a time, not a name.
    title = self.title.strip()
    return None if not title or OPENCODE_DEFAULT_TITLE.match(title) else title

  @property
  def version(self) -> str | None:
    return text_of(self.row.get('version'))


@dataclass(frozen=True)
class OpencodeMessage:
  message_id: str
  created: int
  data: dict[str, object]
  # The other columns of the message row: times and the like.
  columns: dict[str, object] = field(default_factory=dict)

  @property
  def finished(self) -> int:
    return whole(object_of(self.data.get('time')).get('completed')) or self.created


@dataclass(frozen=True)
class OpencodePlace:
  session: OpencodeSession
  cwd: str
  slot: TreeSlot
  original: Path
  parent_path: str | None

  @property
  def target(self) -> Path:
    return self.slot.day_dir / self.session.file_name

  def header(self) -> dict[str, object]:
    header: dict[str, object] = {
      'type': 'session',
      'version': PI_SESSION_VERSION,
      'id': self.session.session_id,
      'timestamp': iso_ms(self.session.created),
      'cwd': self.cwd,
    }
    if self.parent_path is not None:
      header['parentSession'] = self.parent_path
    return header

  def entry(self, message_count: int, name: str | None) -> ArchiveEntry:
    archived = self.session.archived
    return ArchiveEntry(
      session_id=self.session.session_id,
      cwd=self.cwd,
      archived_at=iso_ms(self.session.updated if archived is None else archived),
      original_path=str(self.original),
      message_count=message_count,
      name=name,
      parent_session_path=self.parent_path,
    )


def kept_header(
  header: dict[str, object],
  stored: dict[str, object],
) -> dict[str, object]:
  # An update keeps the cwd and the parent of the archived file, as --repair set them.
  kept = {
    key: value
    for key, value in header.items() if key != 'parentSession'
  }
  cwd = stored.get('cwd')
  if isinstance(cwd, str):
    kept['cwd'] = cwd
  parent = stored.get('parentSession')
  if parent is not None:
    kept['parentSession'] = parent
  return kept


def opencode_root(
  session: OpencodeSession,
  by_id: dict[str, OpencodeSession],
) -> OpencodeSession:
  root = session
  seen = {root.session_id}
  parent = by_id.get(root.parent_id or '')
  while parent is not None and parent.session_id not in seen:
    root = parent
    seen.add(root.session_id)
    parent = by_id.get(root.parent_id or '')
  return root


def ancestors_first(
  sessions: list[OpencodeSession],
  by_id: dict[str, OpencodeSession],
) -> list[OpencodeSession]:
  # A child refers to the file of its parent, so the parent is handled first.
  ordered: list[OpencodeSession] = []
  done: set[str] = set()
  for session in sessions:
    chain: list[OpencodeSession] = []
    current: OpencodeSession | None = session
    while current is not None and current.session_id not in done:
      done.add(current.session_id)
      chain.append(current)
      current = by_id.get(current.parent_id or '')
    ordered.extend(reversed(chain))
  return ordered


def other_columns(row: dict[str, object], *keys: str) -> dict[str, object]:
  return {
    key: value
    for key, value in row.items() if key not in keys
  }


class OpencodeReader:
  # Only reads: the database stays exactly as opencode left it.
  def __init__(self, path: Path) -> None:
    if not path.is_file():
      message = f'opencode database {path} is not a file'
      raise FileNotFoundError(message)
    # The journal lies next to the file a symlink leads to, not next to the link.
    path = path.resolve()
    wal = path.with_name(f'{path.name}-wal')
    if wal.exists() and wal.stat().st_size:
      # An immutable database is read without its write-ahead log.
      message = f'{wal} is not empty, quit opencode first'
      raise ValueError(message)
    self.connection = sqlite3.connect(f'{path.as_uri()}?mode=ro&immutable=1', uri=True)
    self.connection.text_factory = decode_text
    self.connection.execute('PRAGMA query_only=ON')
    rows = self.connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    tables = {str(row[0])
              for row in rows}
    self.tables = tables

  def close(self) -> None:
    self.connection.close()

  def _rows(self, sql: str, values: tuple[object, ...]) -> list[dict[str, object]]:
    cursor = self.connection.execute(sql, values)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor]

  def sessions(self) -> list[OpencodeSession]:
    rows = self._rows('SELECT * FROM session ORDER BY time_created, id', ())
    return [OpencodeSession.of(row) for row in rows]

  def has_messages(self, session_id: str) -> bool:
    rows = self.connection.execute(
      'SELECT 1 FROM message WHERE session_id = ? LIMIT 1',
      (session_id, ),
    )
    return rows.fetchone() is not None

  def messages(self, session_id: str) -> Iterator[OpencodeMessage]:
    # The order of opencode itself: creation time, then id.
    cursor = self.connection.execute(
      'SELECT * FROM message WHERE session_id = ? ORDER BY time_created, id',
      (session_id, ),
    )
    names = [column[0] for column in cursor.description]
    for values in cursor:
      row = dict(zip(names, values))
      yield OpencodeMessage(
        message_id=str(row.get('id')),
        created=whole(row.get('time_created')),
        data=load_object(row.get('data')),
        columns=other_columns(row, 'id', 'session_id', 'data'),
      )

  def parts(self, message_id: str) -> list[OpencodePart]:
    sql = 'SELECT * FROM part WHERE message_id = ? ORDER BY id'
    rows = self._rows(sql, (message_id, ))
    return [
      OpencodePart(
        part_id=str(row.get('id')),
        data=load_object(row.get('data')),
        columns=other_columns(row, 'id', 'message_id', 'session_id', 'data'),
      ) for row in rows
    ]

  def project(self, project_id: object) -> dict[str, object] | None:
    if 'project' not in self.tables or not isinstance(project_id, str):
      return None
    rows = self._rows('SELECT * FROM project WHERE id = ?', (project_id, ))
    return rows[0] if rows else None

  def todos(self, session_id: str) -> list[dict[str, object]]:
    if 'todo' not in self.tables:
      return []
    return self._rows(
      'SELECT * FROM todo WHERE session_id = ? ORDER BY position',
      (session_id, ),
    )

  def events(self, session_id: str) -> list[dict[str, object]]:
    # The agent and model switches of the session.
    if 'session_message' not in self.tables:
      return []
    return self._rows(
      'SELECT * FROM session_message WHERE session_id = ? ORDER BY time_created, id',
      (session_id, ),
    )

  def session_record(self, session: OpencodeSession) -> dict[str, object]:
    # The share link of a session, like the share secret, stays out of the archive.
    return {
      'sessionID': session.session_id,
      'session': other_columns(session.row, SHARE_URL),
      'project': self.project(session.row.get('project_id')),
      'todos': self.todos(session.session_id),
    }


class PiSessionWriter:
  # Writes a pi session file entry by entry, each entry continuing the previous one.
  def __init__(
    self,
    handle: IO[bytes],
    header: dict[str, object],
    *,
    taken: set[str],
  ) -> None:
    self.handle = handle
    self.seed = str(header.get('id'))
    self.ids: set[str] = set(taken)
    self.sequence = 0
    self.last_id: str | None = None
    self.latest_stamp: str | None = None
    self.name: str | None = None
    self.message_count = 0
    # A compaction that keeps no earlier entry waits for the entry after it, and the
    # custom entries after it wait with it.
    self.held: dict[str, object] | None = None
    self.held_after: list[dict[str, object]] = []
    self.write(header)

  def write(self, data: dict[str, object]) -> None:
    line = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    # The written file stays strict UTF-8 whatever text reaches it.
    line = LONE_SURROGATE.sub(REPLACEMENT_CHARACTER, line)
    self.handle.write(line.encode() + b'\n')
    self.latest_stamp = later_stamp(self.latest_stamp, text_of(data.get('timestamp')))
    if data.get('type') == 'session_info':
      name = text_of(data.get('name'))
      self.name = (name or '').strip() or None

  def next_id(self) -> str:
    # The ids depend only on the session and the order of its entries, so the same
    # session is written byte for byte the same way again.
    while True:
      self.sequence += 1
      seed = f'{self.seed}\n{self.sequence}'.encode()
      candidate = hashlib.sha256(seed).hexdigest()[:ENTRY_ID_LENGTH]
      if candidate not in self.ids:
        self.ids.add(candidate)
        return candidate

  def add(self, kind: str, moment_ms: int, fields: dict[str, object]) -> str:
    entry_id = self.next_id()
    entry: dict[str, object] = {
      'type': kind,
      'id': entry_id,
      'parentId': self.last_id,
      'timestamp': iso_ms(moment_ms),
      **fields,
    }
    self.last_id = entry_id
    if kind == 'message':
      self.message_count += 1
    if kind == 'custom' and self.held is not None:
      self.held_after.append(entry)
      return entry_id
    self._release(entry_id)
    if kind == 'compaction' and not entry.get('firstKeptEntryId'):
      self.held = entry
    else:
      self.write(entry)
    return entry_id

  def carry(self, entry: dict[str, object]) -> None:
    # An entry of an earlier file keeps its id and time at the end of the new one.
    carried = {
      **entry,
      'parentId': self.last_id,
    }
    self.last_id = text_of(entry.get('id'))
    self.write(carried)

  def close(self) -> None:
    # A compaction at the end keeps nothing before it: it points to itself.
    if self.held is not None:
      self._release(str(self.held['id']))

  def _release(self, first_kept: str) -> None:
    if self.held is None:
      return
    self.held['firstKeptEntryId'] = first_kept
    self.write(self.held)
    for entry in self.held_after:
      self.write(entry)
    self.held = None
    self.held_after = []


class AssistantContent:
  # The content of a pi assistant message from the parts of an opencode message, with
  # where each text went and the tool calls of each tool part.
  def __init__(
    self,
    parts: list[OpencodePart],
    version: str | None,
    finished: int,
  ) -> None:
    self.blocks: list[dict[str, object]] = []
    self.texts: dict[str, tuple[str | int, ...]] = {}
    self.plans: list[ToolPlan] = []
    for part in parts:
      block = assistant_text(part.data)
      if block is not None:
        key = 'text' if block['type'] == 'text' else 'thinking'
        self.texts[part.part_id] = ('message', 'content', len(self.blocks), key)
        self.blocks.append(block)
      elif part.data.get('type') == 'tool':
        plan = ToolPlan(part, version, finished)
        for call in plan.calls:
          call.block_index = len(self.blocks)
          self.blocks.append(call.block())
        self.plans.append(plan)


def stored_part(part: OpencodePart, moved: list[Moved]) -> dict[str, object]:
  data = copy.deepcopy(part.data)
  for item in moved:
    drop_path(data, item.source)
  stored: dict[str, object] = {
    'partID': part.part_id,
    'part': part.columns,
    'data': data,
  }
  if moved:
    stored['moved'] = [item.record() for item in moved]
  return stored


def message_record(message: OpencodeMessage, items: list[dict[str, object]]) -> object:
  return {
    'messageID': message.message_id,
    'message': message.columns,
    'data': without_cookies(message.data),
    'parts': items,
  }


class OpencodeTranscript:
  # Turns opencode messages with their parts into the entries of one pi session: the
  # standard entries, each followed by a custom entry with the rest of opencode data.
  def __init__(self, writer: PiSessionWriter, version: str | None) -> None:
    self.writer = writer
    self.version = version
    self.provider: str | None = None
    self.model_id: str | None = None
    self.entries: dict[str, str] = {}
    self.order: list[str] = []
    self.compaction: dict[str, object] | None = None

  def add(self, message: OpencodeMessage, parts: list[OpencodePart]) -> None:
    role = message.data.get('role')
    if role == 'user':
      self._user(message, parts)
    elif role != 'assistant':
      self._record(message, parts, {}, set())
    elif not (message.data.get('summary') is True and self._compact(message, parts)):
      self._assistant(message, parts)
    self.order.append(message.message_id)

  def _first_kept(self, tail: str | None) -> str:
    # The tail starts at the first message from tail_start_id that left entries.
    if not tail or tail not in self.order:
      return ''
    for message_id in self.order[self.order.index(tail):]:
      if message_id in self.entries:
        return self.entries[message_id]
    return ''

  def _add(
    self,
    message_id: str,
    kind: str,
    moment_ms: int,
    fields: dict[str, object],
  ) -> str:
    entry_id = self.writer.add(kind, moment_ms, fields)
    self.entries.setdefault(message_id, entry_id)
    return entry_id

  def _record(
    self,
    message: OpencodeMessage,
    parts: list[OpencodePart],
    moved: dict[str, list[Moved]],
    tools: set[str],
  ) -> None:
    # Everything of the message that the standard entries do not hold; a tool part has
    # a custom entry of its own after its results.
    items: list[dict[str, object]] = [{
      'partID': part.part_id,
      'record': PART_RECORD,
    } if part.part_id in tools else stored_part(part, moved.get(part.part_id, []))
                                      for part in parts]
    self.writer.add(
      'custom',
      message.created,
      {
        'customType': MESSAGE_RECORD,
        'data': message_record(message, items),
      },
    )

  def _model(self, message: OpencodeMessage, provider: object, model_id: object) -> None:
    if not isinstance(provider, str) or not isinstance(model_id, str):
      return
    if not provider or not model_id:
      return
    if provider == self.provider and model_id == self.model_id:
      return
    self.provider = provider
    self.model_id = model_id
    self._add(
      message.message_id,
      'model_change',
      message.created,
      {
        'provider': provider,
        'modelId': model_id,
      },
    )

  def _user(self, message: OpencodeMessage, parts: list[OpencodePart]) -> None:
    model = object_of(message.data.get('model'))
    self._model(message, model.get('providerID'), model.get('modelID'))
    content: list[dict[str, object]] = []
    texts: dict[str, int] = {}
    for part in parts:
      kind = part.data.get('type')
      text = text_of(part.data.get('text'))
      if kind == 'text' and text:
        texts[part.part_id] = len(content)
        content.append({
          'type': 'text',
          'text': text,
        })
      elif kind == 'file':
        content.append(Attachment.of(part.data).block())
      elif kind == 'compaction':
        # The summary comes with the next assistant message.
        self.compaction = part.data
    moved: dict[str, list[Moved]] = {}
    if content:
      entry_id = self._add(
        message.message_id,
        'message',
        message.created,
        {
          'message': {
            'role': 'user',
            'content': content,
            'timestamp': message.created,
          },
        },
      )
      moved = {
        part_id: [Moved(('text', ), entry_id, ('message', 'content', index, 'text'))]
        for part_id, index in texts.items()
      }
    self._record(message, parts, moved, set())

  def _assistant(self, message: OpencodeMessage, parts: list[OpencodePart]) -> None:
    data = message.data
    self._model(message, data.get('providerID'), data.get('modelID'))
    built = AssistantContent(parts, self.version, message.finished)
    error = opencode_error(data)
    steps = [part.data for part in parts]
    # An empty message still keeps its usage: pi drops it from the model context.
    if not built.blocks and error is None and not has_opencode_usage(data, steps):
      self._record(message, parts, {}, set())
      return
    reply: dict[str, object] = {
      'role': 'assistant',
      'content': built.blocks,
      'api': OPENCODE_API,
      'provider': text_of(data.get('providerID')) or '',
      'model': text_of(data.get('modelID')) or '',
      'usage': opencode_usage(data, steps),
      'stopReason': opencode_stop_reason(data),
      'timestamp': message.created,
    }
    if error is not None:
      reply['errorMessage'] = error
    entry_id = self._add(
      message.message_id,
      'message',
      message.created,
      {
        'message': reply,
      },
    )
    moved = {
      part_id: [Moved(('text', ), entry_id, target)]
      for part_id, target in built.texts.items()
    }
    tool_ids = {plan.part.part_id
                for plan in built.plans}
    self._record(message, parts, moved, tool_ids)
    for plan in built.plans:
      self._tool_results(message, plan, entry_id)

  def _tool_results(
    self,
    message: OpencodeMessage,
    plan: ToolPlan,
    entry_id: str,
  ) -> None:
    results = [
      self._add(
        message.message_id,
        'message',
        plan.moment,
        {
          'message': call.result(plan.moment),
        },
      ) for call in plan.calls
    ]
    self.writer.add(
      'custom',
      plan.moment,
      {
        'customType': PART_RECORD,
        'data': plan.record(message.message_id, entry_id, results),
      },
    )

  def _compact(self, message: OpencodeMessage, parts: list[OpencodePart]) -> bool:
    texts = [part for part in parts if assistant_text(part.data) is not None]
    summaries = [part for part in texts if part.data.get('type') == 'text']
    thoughts = [part for part in texts if part.data.get('type') == 'reasoning']
    pending = object_of(self.compaction)
    self.compaction = None
    if not summaries:
      # A failed compaction stays an ordinary message with its error.
      return False
    data = message.data
    self._model(message, data.get('providerID'), data.get('modelID'))
    fields = self._compaction(message, summaries, thoughts, parts)
    fields['firstKeptEntryId'] = self._first_kept(text_of(pending.get('tail_start_id')))
    entry_id = self._add(message.message_id, 'compaction', message.created, fields)
    moved: dict[str, list[Moved]] = {}
    # A single text is kept only where the compaction holds it.
    if len(summaries) == 1:
      moved[summaries[0].part_id] = [Moved(('text', ), entry_id, ('summary', ))]
    if len(thoughts) == 1:
      target = ('details', 'reasoning')
      moved[thoughts[0].part_id] = [Moved(('text', ), entry_id, target)]
    self._record(message, parts, moved, set())
    return True

  @staticmethod
  def _compaction(
    message: OpencodeMessage,
    summaries: list[OpencodePart],
    thoughts: list[OpencodePart],
    parts: list[OpencodePart],
  ) -> dict[str, object]:
    usage = opencode_usage(message.data, [part.data for part in parts])
    before = sum(whole(usage[key]) for key in ('input', 'cacheRead', 'cacheWrite'))
    fields: dict[str, object] = {
      'summary': '\n\n'.join(text_of(part.data.get('text')) or '' for part in summaries),
      'firstKeptEntryId': '',
      'tokensBefore': before,
      'usage': usage,
    }
    # The rest of the summarizing message stays with the compaction.
    details: dict[str, object] = {}
    reasoning = '\n\n'.join(text_of(part.data.get('text')) or '' for part in thoughts)
    if reasoning:
      details['reasoning'] = reasoning
    error = opencode_error(message.data)
    if error is not None:
      details['errorMessage'] = error
    if details:
      fields['details'] = details
    return fields


def write_events(
  writer: PiSessionWriter,
  events: list[dict[str, object]],
  until: int | None,
) -> list[dict[str, object]]:
  # Writes the switch events up to a moment and returns the later ones.
  for index, event in enumerate(events):
    moment = whole(event.get('time_created'))
    if until is not None and moment > until:
      return events[index:]
    writer.add(
      'custom',
      moment,
      {
        'customType': EVENT_RECORD,
        'data': {
          'eventID': event.get('id'),
          'row': event,
        },
      },
    )
  return []


class Transcription:
  # One opencode session as a pi session file: the session data, then the messages
  # with the switch events between them by time, then the names of an earlier file.
  def __init__(self, reader: OpencodeReader, place: OpencodePlace) -> None:
    self.reader = reader
    self.place = place
    self.header = place.header()
    self.carried: list[dict[str, object]] = []
    self.message_count = 0
    self.latest_stamp: str | None = None
    self.name: str | None = None

  @classmethod
  def updating(
    cls,
    reader: OpencodeReader,
    place: OpencodePlace,
    stored: SessionFile,
  ) -> Transcription:
    # An update keeps the header values and the later names of the archived file.
    transcription = cls(reader, place)
    transcription.header = kept_header(transcription.header, stored.header)
    transcription.carried = stored.carried()
    return transcription

  def write(self, handle: IO[bytes], progress: Progress) -> None:
    session = self.place.session
    used = {text_of(entry.get('id')) or ''
            for entry in self.carried}
    writer = PiSessionWriter(handle, self.header, taken=used)
    writer.add(
      'custom',
      session.created,
      {
        'customType': SESSION_RECORD,
        'data': self.reader.session_record(session),
      },
    )
    if session.name:
      writer.add('session_info', session.created, {
        'name': session.name,
      })
    transcript = OpencodeTranscript(writer, session.version)
    events = self.reader.events(session.session_id)
    for message in self.reader.messages(session.session_id):
      events = write_events(writer, events, message.created)
      transcript.add(message, self.reader.parts(message.message_id))
      # A long session keeps the progress going.
      progress.show()
    write_events(writer, events, None)
    writer.close()
    for entry in self.carried:
      writer.carry(entry)
    self.message_count = writer.message_count
    self.latest_stamp = writer.latest_stamp
    self.name = writer.name


@dataclass
class Options:
  data_dir: Path
  mode: str
  dry_run: bool
  naming: NamingOptions | None
  opencode: Path | None = None
  fix_mtime: bool = False


class Converter:
  def __init__(self, options: Options) -> None:
    self.options = options
    self.tree_root = options.data_dir / TREE_DIR_NAME
    self.summaries = Summaries(dry_run=options.dry_run)
    naming = options.naming
    self.namer = None if naming is None or options.dry_run else Namer(naming)
    self.counts: Counter[str] = Counter()
    self.progress = Progress(self.counts)
    self.cleanup: set[Path] = set()
    self.parked: dict[Path, list[Path]] = {}
    self.known_dirs: list[Path] = []
    # The originalPath of every opencode session that has a file in the archive.
    self.origins: dict[str, str] = {}
    self.unplaced_parked: list[Path] = []
    # The files --repair has placed, the only ones --fix-mtime touches.
    self.settled: list[Path] = []
    self.archive = ArchiveIndex(self.tree_root, self.summaries)
    FIRST_TIMES.clear()
    REWRITTEN.clear()
    self.session_dirs: dict[str, Path] = {}

  def run(self) -> None:
    try:
      if self.options.mode == 'reverse':
        self.reverse()
      elif self.options.mode == 'repair':
        self.repair()
      elif self.options.mode == 'opencode':
        self.import_opencode()
      else:
        self.forward()
    finally:
      try:
        # Files moved before an error or Ctrl+C keep their summary records.
        self.flush()
      finally:
        restore_rewritten()
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

  def _warning(self, message: str) -> None:
    say(f'warning: {message}')
    self.counts['warnings'] += 1

  @contextmanager
  def _unreadable(self, left: str) -> Generator[None, None, None]:
    # A file that cannot be read is a problem of its session, the others go on; write
    # and move errors are not caught here.
    try:
      yield
    except ReadError as failure:
      reason = failure.strerror or failure
      self._problem(f'cannot read {failure.path}: {reason}, {left} left as is')

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
      self._warning(f'{path} is read-only, left unnamed')
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
    referenced = {record_file_name(record)
                  for record in records}
    orphans = [
      path for path in sorted(flat_dir.glob('*.jsonl'))
      if path.is_file() and path.name not in referenced
    ]
    self.progress.start(len(records) + len(orphans))
    remaining: list[dict[str, object]] = []
    for record in records:
      if not self.forward_record(record, flat_dir):
        remaining.append(record)
      self.progress.step()
    for path in orphans:
      self.forward_orphan(path, session_dirs)
      self.progress.step()
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
      self._warning(f'{entry.original_path} is only a guess of where {path} came from')
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
      months = list(self._months())
      total = sum(len(self.summaries.entries(month / SUMMARY_NAME)) for month in months)
      self.progress.start(total)
      for month_dir in months:
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
      self.progress.step()
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

  def _scan_archive(self) -> None:
    self.archive.scan()
    for path, error in self.archive.failed:
      self._problem(f'cannot read {path}: {error.strerror or error}, left as is')

  def repair(self) -> None:
    self.progress.start(None)
    months = list(self._months())
    # Everything is indexed before the first change, with the records of a summary
    # lost by a failed undo read from its newest backup.
    for month_dir in months:
      self._read_backup(month_dir)
    self._scan_archive()
    for month_dir in months:
      self._clean_leftovers(month_dir)
    # A header rewrite leaves its unfinished copy next to the file at any depth.
    flat_dir = self.options.data_dir / FLAT_DIR_NAME
    copies = set(flat_dir.glob('.*.copying'))
    for project_dir in child_dirs(self.tree_root):
      copies.update(project_dir.rglob('.*.copying'))
    for leftover in sorted(copies):
      self._remove_leftover(leftover)
    self.known_dirs = self.archive.origin_dirs()
    for parked in self.unplaced_parked:
      with self._unreadable(str(parked)):
        self._resolve_unplaced(parked)
    cwds = self._project_cwds()
    placements = self._placements(cwds)
    origins = self._planned_origins(placements)
    for placement in placements:
      with self._unreadable(f'session {placement.entry.session_id}'):
        self._repair_file(placement, origins)
      self.progress.step()
    self._drop_lost_records(cwds)
    if self.options.fix_mtime:
      for path in self.settled:
        with self._unreadable(str(path)):
          self._fix_mtime(path)

  @staticmethod
  def _backups(month_dir: Path) -> list[Path]:
    return sorted(
      month_dir.glob(f'.{SUMMARY_NAME}.*.previous'),
      key=lambda path: path.stat().st_mtime_ns,
    )

  def _read_backup(self, month_dir: Path) -> None:
    summary = month_dir / SUMMARY_NAME
    backups = self._backups(month_dir)
    if backups and not summary.exists():
      self.summaries.loaded[summary] = read_summary(backups[-1])

  def _clean_leftovers(self, month_dir: Path) -> None:
    summary = month_dir / SUMMARY_NAME
    backups = self._backups(month_dir)
    if backups and not summary.exists():
      # An undo failed after the last record of the month was removed; the index has
      # already read its records from the backup.
      newest = backups.pop()
      if self.options.dry_run:
        say(f'would restore {summary} from {newest}')
      else:
        newest.rename(summary)
        say(f'restored {summary} from {newest}')
      self.counts['repaired'] += 1
    leftovers = [*backups, *month_dir.glob(f'.{SUMMARY_NAME}.*.tmp')]
    for day_dir in child_dirs(month_dir):
      for parked in sorted(day_dir.glob('*.moving')):
        kept = day_dir / parked.name.rsplit('.', 2)[0]
        if kept.is_file():
          with self._unreadable(str(parked)):
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

  def _warn_prefix(self, shorter: Path, longer: Path) -> None:
    # An unfinished copy or an older version that pi may still be writing: only reported.
    self._warning(f'{shorter} is a strict prefix of {longer}, both files are kept')

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
    self._warning(
      f'{parked} is left by an interrupted operation, its original is not '
      f'in {searched or "any known directory"}, the file is kept',
    )

  def _resolve_parked(self, parked: Path, kept: Path) -> None:
    if not same_contents(parked, kept):
      self._warning(f'{parked} is left by an interrupted operation, compare with {kept}')
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

  def _project_cwds(self) -> dict[Path, str]:
    # The cwd of a project directory is the one candidate whose name it has.
    registered = registered_projects(self.options.data_dir)
    cwds: dict[Path, str] = {}
    for project_dir in child_dirs(self.tree_root):
      if not self.archive.has_content(project_dir):
        continue
      candidates = registered | self.archive.cwds_in(project_dir)
      matching = sorted(
        cwd for cwd in candidates if project_dir_name(cwd) == project_dir.name
      )
      listed = ', '.join(matching)
      if len(matching) == 1:
        cwds[project_dir] = matching[0]
      elif matching:
        self._problem(f'{project_dir} fits several cwds: {listed}, left as is')
      else:
        self._problem(
          f'no record, session header or PI WEB project gives a cwd that fits '
          f'{project_dir}, left as is',
        )
    return cwds

  def _placements(self, cwds: dict[Path, str]) -> list[Placement]:
    placements: list[Placement] = []
    for session_id, paths in sorted(self.archive.files.items()):
      placement = None
      with self._unreadable(f'session {session_id}'):
        placement = self._placement(session_id, paths, cwds)
      if placement is not None:
        placements.append(placement)
    for path in self.archive.unreadable:
      if self.archive.project_of(path) in cwds:
        self._problem(f'{path} has no session header with an id')
    return placements

  def _placement(
    self,
    session_id: str,
    paths: list[Path],
    cwds: dict[Path, str],
  ) -> Placement | None:
    path = self._single_file(session_id, paths, cwds)
    if path is None:
      return None
    cwd = cwds.get(self.archive.project_of(path))
    if cwd is None:
      return None
    records = self.archive.records.get(session_id, [])
    record = self.archive.record_of(session_id, path)
    # A file without a date is left as it is, even next to an identical original.
    dated = self._moment(session_id, path) is not None
    if dated and record is not None and self._drop_identical(records, record, path):
      return None
    slot = self._slot(session_id, path, cwd)
    if slot is None:
      return None
    entry = self._repaired_entry(record, path, cwd)
    if entry is None:
      return None
    return Placement(
      path=path,
      cwd=cwd,
      slot=slot,
      entry=entry,
      records=records,
      header=self.archive.headers[path],
    )

  def _slot(self, session_id: str, path: Path, cwd: str) -> TreeSlot | None:
    moment = self._moment(session_id, path)
    if moment is None:
      self._problem(f'cannot date session file {path}')
      return None
    slot = TreeSlot.at(self.tree_root, cwd, moment)
    target = slot.day_dir / path.name
    if target != path and target.exists():
      # Planned before any move, so no child links to a parent that stays where it is.
      self._problem(f'{target} exists, {path} is left as is')
      return None
    return slot

  def _moment(self, session_id: str, path: Path) -> int | None:
    moment = created_ms(session_id, path.name)
    if moment is None:
      moment = parse_iso_ms(self.archive.headers[path].get('timestamp'))
    return moment

  def _single_file(
    self,
    session_id: str,
    paths: list[Path],
    cwds: dict[Path, str],
  ) -> Path | None:
    # Identical copies in one project are the names a move stopped between the link
    # and the unlink left: the one in its day directory stays, the others go.
    if len(paths) == 1:
      return paths[0]
    projects = {self.archive.project_of(path)
                for path in paths}
    listed = ', '.join(str(path) for path in paths)
    identical = all(same_contents(paths[0], path) for path in paths[1:])
    if not identical or len(projects) != 1 or not projects <= cwds.keys():
      self._problem(f'session {session_id} has several files: {listed}, left as is')
      return None
    if any(self._moment(session_id, path) is None for path in paths):
      self._problem(f'cannot date session files {listed}, left as is')
      return None
    kept = next(
      (path for path in paths if path == self._target_of(session_id, path, cwds)),
      paths[0],
    )
    for path in paths:
      if path == kept:
        continue
      if self.options.dry_run:
        say(f'would remove {path}, identical to {kept}')
      else:
        drop_duplicate(kept, path)
        self.cleanup.add(path.parent)
        say(f'removed {path}, identical to {kept}')
      self.counts['repaired'] += 1
    return kept

  def _target_of(self, session_id: str, path: Path, cwds: dict[Path, str]) -> Path | None:
    moment = self._moment(session_id, path)
    cwd = cwds.get(self.archive.project_of(path))
    if moment is None or cwd is None:
      return None
    return TreeSlot.at(self.tree_root, cwd, moment).day_dir / path.name

  def _session_dir(self, cwd: str) -> Path:
    # The session directory of pi that the other records of the project name the most.
    if cwd not in self.session_dirs:
      parents = Counter(
        Path(record.entry.original_path).parent for record in self.archive.all_records()
        if record.entry.cwd == cwd
      )
      self.session_dirs[cwd] = min(
        parents,
        key=lambda parent: (-parents[parent], str(parent)),
      ) if parents else default_session_dir(cwd)
    return self.session_dirs[cwd]

  def _repaired_entry(
    self,
    record: ArchiveRecord | None,
    path: Path,
    cwd: str,
  ) -> ArchiveEntry | None:
    if record is not None:
      entry = replace(record.entry)
      if entry.cwd != cwd:
        entry.cwd = cwd
        entry.original_path = str(self._session_dir(cwd) / path.name)
      return entry
    session = SessionFile(path)
    entry = ArchiveEntry(
      session_id=text_of(session.header.get('id')) or '',
      cwd=cwd,
      archived_at=mtime_iso(path),
      original_path=str(self._session_dir(cwd) / path.name),
      message_count=session.message_count,
      name=session.name,
      parent_session_path=text_of(session.header.get('parentSession')),
    )
    return None if self._orphan_settled(entry, path) else entry

  def _orphan_settled(self, entry: ArchiveEntry, path: Path) -> bool:
    # A file without a record whose original is there is left or dropped, not recorded.
    original = Path(entry.original_path)
    if not original.exists():
      return False
    if original.resolve() == path.resolve():
      # A symbolic link to the file itself: the file is the only copy.
      self._warning(f'{original} links to {path}, both are kept')
      return True
    if is_prefix(path, original):
      # Left without a record: an archived copy is never preferred to a longer original.
      self._warn_prefix(path, original)
      return True
    if same_contents(original, path):
      if self.options.dry_run:
        say(f'would remove {path}')
      else:
        drop_duplicate(original, path)
        self.cleanup.add(path.parent)
        say(f'removed {path} without a record: {original} has the same contents')
      self.counts['repaired'] += 1
      return True
    if is_prefix(original, path):
      self._warn_prefix(original, path)
    else:
      self._warning(f'{original} exists with other contents, both files are kept')
    return False

  def _drop_identical(
    self,
    records: list[ArchiveRecord],
    record: ArchiveRecord,
    path: Path,
  ) -> bool:
    for leftover in self._leftovers_near(Path(record.entry.original_path)):
      if leftover.name.endswith('.copying'):
        self._remove_leftover(leftover)
      else:
        self._resolve_parked(leftover, path)
    original = Path(record.entry.original_path)
    if not original.exists() or original.resolve() == path.resolve():
      return False
    if is_prefix(original, path):
      self._warn_prefix(original, path)
      return False
    if is_prefix(path, original):
      self._warn_prefix(path, original)
      return False
    if not same_contents(original, path):
      self._warning(f'{original} exists again with other contents, both files are kept')
      return False
    if self.options.dry_run:
      say(f'would remove {path}')
    else:
      drop_duplicate(original, path)
      self.cleanup.add(path.parent)
      say(f'removed {path} and its record: {original} has the same contents')
    for item in records:
      self.summaries.remove(item.summary, item.entry)
    self.counts['repaired'] += 1
    return True

  def _planned_origins(self, placements: list[Placement]) -> OriginMap:
    # The parents are looked up among the original paths the records have after repair.
    origins = OriginMap()
    placed = {placement.entry.session_id
              for placement in placements}
    for placement in placements:
      origins.add(placement.entry.original_path)
      for record in placement.records:
        if record.entry.original_path != placement.entry.original_path:
          origins.moved[record.entry.original_path] = placement.entry.original_path
    # A record without a file anywhere in the tree is no parent: it is about to go.
    for record in self.archive.all_records():
      session_id = record.entry.session_id
      if session_id not in placed and session_id in self.archive.files:
        origins.add(record.entry.original_path)
    return origins

  def _parent_path(self, parent: str, placement: Placement, origins: OriginMap) -> str:
    if parent in origins.moved:
      return origins.moved[parent]
    if parent in origins.known or Path(parent).is_file():
      return parent
    name = Path(parent).name
    named = origins.by_name.get(file_key(name), [])
    if len(named) == 1:
      return named[0]
    dirs = sorted({*self.known_dirs, default_session_dir(placement.cwd)})
    found = [directory / name for directory in dirs if (directory / name).is_file()]
    if len(found) > 1:
      self._warning(f'{name} is in several session directories, took {found[0]}')
    if found:
      return str(found[0])
    self._warning(f'parent {parent} of {placement.path} is not found, left as is')
    return parent

  def _repair_file(self, placement: Placement, origins: OriginMap) -> None:
    path = placement.path
    entry = placement.entry
    header = {
      **placement.header,
      'cwd': placement.cwd,
    }
    parent = text_of(header.get('parentSession'))
    if parent is not None:
      header['parentSession'] = self._parent_path(parent, placement, origins)
      entry.parent_session_path = text_of(header['parentSession'])
    elif entry.parent_session_path is not None:
      old = entry.parent_session_path
      entry.parent_session_path = origins.moved.get(old, old)
    if header != placement.header:
      self._rewrite_header(path, header)
    target = placement.target
    if target != path:
      if self.place(path, target, entry.session_id) not in PLACED:
        return
      folder = target.parent
      say(self._past(f'moved {path} to {folder}', f'move {path} to {folder}'))
      self.counts['repaired'] += 1
    readable = target if target.exists() else path
    self._file_record(placement, readable)
    self.settled.append(readable)

  def _rewrite_header(self, path: Path, header: dict[str, object]) -> None:
    # The modification time stays, so the session keeps its place in the list.
    if not self.options.dry_run:
      replace_header(path, header)
    cwd = header.get('cwd')
    say(
      self._past(
        f'rewrote the header of {path} for cwd {cwd}',
        f'rewrite the header of {path} for cwd {cwd}',
      ),
    )
    self.counts['repaired'] += 1

  def _file_record(self, placement: Placement, readable: Path) -> None:
    entry = placement.entry
    summary = placement.slot.summary
    records = placement.records
    unchanged = [
      record for record in records if record.summary == summary and record.entry == entry
    ]
    if len(records) == 1 and unchanged:
      self._sync_name(entry, summary, readable)
      return
    for record in records:
      if record.summary != summary or not record.entry.same_session(entry):
        self.summaries.remove(record.summary, record.entry)
    self.summaries.upsert(summary, entry)
    name = readable.name
    if not records:
      say(self._past(f'added a record of {readable}', f'add a record of {readable}'))
    elif all(record.summary == summary for record in records):
      say(
        self._past(
          f'updated the record of {name} in {summary}',
          f'update the record of {name} in {summary}',
        ),
      )
    else:
      say(
        self._past(
          f'moved the record of {name} to {summary}',
          f'move the record of {name} to {summary}',
        ),
      )
    self.counts['repaired'] += 1
    self._sync_name(entry, summary, readable)

  def _drop_lost_records(self, cwds: dict[Path, str]) -> None:
    # A record without a file anywhere in the tree goes, unless its project is unknown;
    # a file with its name is still there and keeps it, readable, hidden or not.
    seen = {
      file_key(path.name)
      for path in self.tree_root.rglob('*.jsonl') if path.is_file()
    }
    for session_id, records in sorted(self.archive.records.items()):
      if session_id in self.archive.files:
        continue
      for record in records:
        if file_key(record.entry.file_name) in seen:
          continue
        if self.archive.project_of(record.summary) in cwds:
          self._drop_lost(record)

  def _drop_lost(self, record: ArchiveRecord) -> None:
    entry = record.entry
    flat_file = self.options.data_dir / FLAT_DIR_NAME / entry.file_name
    if flat_file.is_file():
      self._problem(f'{entry.file_name} is in {FLAT_DIR_NAME}/, run --reverse again')
      return
    self.summaries.remove(record.summary, entry)
    say(
      self._past(
        f'removed the record of missing {entry.file_name} from {record.summary}',
        f'remove the record of missing {entry.file_name} from {record.summary}',
      ),
    )
    self.counts['repaired'] += 1

  def _fix_mtime(self, path: Path) -> None:
    # A copy of PI WEB or an earlier import got the time of the copy as its mtime.
    moment = parse_iso_ms(SessionFile(path).latest_timestamp)
    if moment is None or path.stat().st_mtime_ns <= moment * NS_PER_MS:
      return
    if not self.options.dry_run:
      set_mtime(path, moment)
    say(
      self._past(
        f'set the mtime of {path} to {iso_ms(moment)}',
        f'set the mtime of {path} to {iso_ms(moment)}',
      ),
    )
    self.counts['repaired'] += 1

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

  def import_opencode(self) -> None:
    database = self.options.opencode
    if database is None:
      return
    placed: Counter[str] = Counter()
    with closing(OpencodeReader(database)) as reader:
      sessions = reader.sessions()
      by_id = {
        session.session_id: session
        for session in sessions
      }
      self._scan_archive()
      self.progress.start(len(sessions))
      for session in ancestors_first(sessions, by_id):
        cwd = self.import_session(reader, session, by_id)
        if cwd is not None:
          placed[cwd] += 1
        self.progress.step()
    self._warn_unregistered(placed)

  def import_session(
    self,
    reader: OpencodeReader,
    session: OpencodeSession,
    by_id: dict[str, OpencodeSession],
  ) -> str | None:
    # A child session goes into the project of its root session to lie under it.
    cwd = canonical_cwd(opencode_root(session, by_id).directory)
    if cwd in {'', '/'}:
      self._problem(
        f'opencode session {session.session_id} has no directory other than /',
      )
      return None
    session_dir = default_session_dir(cwd)
    place = OpencodePlace(
      session=session,
      cwd=cwd,
      slot=TreeSlot.at(self.tree_root, cwd, session.created),
      original=session_dir / session.file_name,
      # A parent without a file in the archive (skipped, problem) has no origin.
      parent_path=self.origins.get(session.parent_id or ''),
    )
    blocked = self.archive.blocked.get(session.session_id)
    if blocked is not None:
      self._problem(
        f'session {session.session_id} may be in {blocked}, which cannot be read, '
        'left as is',
      )
      return None
    status = 'conflict'
    with self._unreadable(f'session {session.session_id}'):
      status = self._import_present(reader, place)
      if status == 'absent':
        status = self._import_new(reader, place)
    if status == 'imported':
      self.origins[session.session_id] = str(place.original)
    return cwd if status in {'present', 'imported'} else None

  def _import_present(self, reader: OpencodeReader, place: OpencodePlace) -> str:
    session_id = place.session.session_id
    # The archive tree decides first, wherever --repair has put the file.
    found = self.archive.files.get(session_id, [])
    if len(found) > 1:
      listed = ', '.join(str(path) for path in found)
      self._problem(f'session {session_id} has several files: {listed}, left as is')
      return 'conflict'
    if found:
      return self._import_update(reader, place, found[0])
    target = place.target
    if target.exists():
      self._problem(f'{target} exists but is not a file of session {session_id}')
      return 'conflict'
    # Restored into pi or moved back into the flat archive by --reverse.
    flat = self._flat_file(session_id, place.session.file_name)
    recorded = self._recorded_origin(place)
    if recorded is not None:
      self.origins[session_id] = recorded
    elif read_header(place.original).get('id') == session_id:
      self.origins[session_id] = str(place.original)
    elif read_header(flat).get('id') == session_id:
      self.origins[session_id] = self._flat_origin(session_id, flat)
      self._import_flat(reader, place, flat)
      return 'present'
    else:
      # A record without its file is replaced along with the file.
      return 'absent'
    self.counts['present'] += 1
    return 'present'

  def _recorded_origin(self, place: OpencodePlace) -> str | None:
    # A session restored under another session directory stays where its record says.
    for item in self.summaries.entries(place.slot.summary):
      if item.session_id != place.session.session_id or item.cwd != place.cwd:
        continue
      if read_header(Path(item.original_path)).get('id') == item.session_id:
        return item.original_path
    return None

  def _flat_file(self, session_id: str, file_name: str) -> Path:
    # The record of archived-sessions.json names the file, also a renamed one.
    flat_dir = self.options.data_dir / FLAT_DIR_NAME
    try:
      records = read_index(self.options.data_dir / INDEX_NAME)
    except (OSError, TypeError, ValueError):
      records = []
    for record in records:
      recorded = record_file_name(record)
      if record.get('sessionId') != session_id or not recorded:
        continue
      if read_header(flat_dir / recorded).get('id') == session_id:
        return flat_dir / recorded
    return flat_dir / file_name

  def _flat_origin(self, session_id: str, flat: Path) -> str:
    # The record of archived-sessions.json knows where the session came from.
    try:
      records = read_index(self.options.data_dir / INDEX_NAME)
    except (OSError, TypeError, ValueError):
      return str(flat)
    for record in records:
      original = text_of(record.get('originalPath'))
      if record.get('sessionId') == session_id and original:
        return original
    return str(flat)

  def _updatable(self, stored: SessionFile, origin: str) -> bool:
    # A session continued in pi and archived again is not overwritten.
    session_id = text_of(stored.header.get('id')) or ''
    if not stored.foreign:
      return True
    kinds = ', '.join(sorted(stored.foreign))
    self._warning(
      f'{stored.path} has entries the opencode import does not write ({kinds}), '
      'left as is',
    )
    self.origins[session_id] = origin
    self.counts['present'] += 1
    return False

  def _import_update(
    self,
    reader: OpencodeReader,
    place: OpencodePlace,
    path: Path,
  ) -> str:
    session_id = place.session.session_id
    record = self.archive.record_of(session_id, path)
    origin = str(place.original) if record is None else record.entry.original_path
    stored = SessionFile(path)
    if not self._updatable(stored, origin):
      return 'present'
    # A file moved away without its summary gets its record from --repair, which
    # knows the project of the directory it lies in.
    homeless = record is None and path != place.target
    if homeless:
      self._warning(f'{path} has no summary record, run --repair to add one')
    transcription = Transcription.updating(reader, place, stored)
    changed = self._write_transcription(transcription, path, replace_target=True)
    # Written by a run stopped before its summary record, the file gets one now.
    entry = place.entry(0, None) if record is None else replace(record.entry)
    entry.message_count = transcription.message_count
    entry.name = transcription.name
    summary = place.slot.summary if record is None else record.summary
    naming = entry.name is None and self.options.naming is not None
    if not homeless and (record is None or entry != record.entry or naming):
      self.summaries.upsert(summary, entry)
    self.origins[session_id] = entry.original_path
    if changed:
      say(self._past(f'updated {path}', f'update {path}'))
    self.counts['updated' if changed else 'present'] += 1
    if not homeless:
      self.name_entry(entry, path)
    return 'present'

  def _import_flat(
    self,
    reader: OpencodeReader,
    place: OpencodePlace,
    flat: Path,
  ) -> None:
    # The flat archive is updated in place like the tree, its sessions are never named.
    stored = SessionFile(flat)
    if not self._updatable(stored, self.origins[place.session.session_id]):
      return
    transcription = Transcription.updating(reader, place, stored)
    if self._write_transcription(transcription, flat, replace_target=True):
      say(self._past(f'updated {flat}', f'update {flat}'))
      self.counts['updated'] += 1
    else:
      self.counts['present'] += 1
    # Also for an unchanged file: a run may have stopped before the index got its record.
    index_path = self.options.data_dir / INDEX_NAME
    data = read_json(index_path) if index_path.exists() else None
    sessions = data.get('sessions') if isinstance(data, dict) else None
    records = [
      record for record in sessions
      if isinstance(record, dict) and record.get('sessionId') == place.session.session_id
    ] if isinstance(sessions, list) else []
    stale = False
    for record in records:
      before = dict(record)
      record['messageCount'] = transcription.message_count
      record.pop('name', None)
      if transcription.name:
        record['name'] = transcription.name
      stale = stale or record != before
    if stale:
      write_json(index_path, data, dry_run=self.options.dry_run)
      say(
        self._past(
          f'updated the record of {flat.name} in {INDEX_NAME}',
          f'update the record of {flat.name} in {INDEX_NAME}',
        ),
      )

  def _import_new(self, reader: OpencodeReader, place: OpencodePlace) -> str:
    if not reader.has_messages(place.session.session_id):
      self.counts['skipped'] += 1
      return 'skipped'
    transcription = Transcription(reader, place)
    self._write_transcription(transcription, place.target, replace_target=False)
    if self.options.dry_run:
      say(f'would import {place.session.session_id} -> {place.target}')
    else:
      self.archive.add(place.target, transcription.header)
    entry = place.entry(transcription.message_count, transcription.name)
    self.summaries.upsert(place.slot.summary, entry)
    self.name_entry(entry, place.target)
    self.counts['imported'] += 1
    return 'imported'

  def _write_transcription(
    self,
    transcription: Transcription,
    target: Path,
    *,
    replace_target: bool,
  ) -> bool:
    # Returns whether the file changed; its mtime is the time of its latest entry.
    if self.options.dry_run and not replace_target:
      with Path(os.devnull).open('wb') as sink:
        transcription.write(sink, self.progress)
      return True
    if self.options.dry_run:
      with tempfile.TemporaryFile() as sink:
        transcription.write(sink, self.progress)
        return not same_stream(sink, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    # A failed write leaves its new day directory empty for the cleanup.
    self.cleanup.add(target.parent)
    temp = target.with_name(f'.{target.name}.{uuid.uuid4()}.copying')
    try:
      with temp.open('xb') as writer:
        transcription.write(writer, self.progress)
        writer.flush()
        os.fsync(writer.fileno())
      changed = settle(
        temp,
        target,
        replace_target=replace_target,
        moment_ms=parse_iso_ms(transcription.latest_stamp),
      )
    finally:
      remove_file(temp, missing_ok=True, keep=target)
    return changed

  def _warn_unregistered(self, placed: Counter[str]) -> None:
    # PI WEB shows archived sessions only in the project of their cwd.
    projects = registered_projects(self.options.data_dir)
    for cwd, count in sorted(placed.items()):
      if cwd not in projects:
        self._warning(
          f'{cwd} is not a pi-web project, add it to see its {count} archived sessions',
        )


def parse_options(argv: list[str]) -> Options:
  parser = argparse.ArgumentParser(
    description='Convert the pi-web session archive between archived-sessions.json '
    'with archived-sessions/ and the archived-projects/ tree, repair the tree or import '
    'the sessions of an opencode database into it; stop pi-web first.',
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
    help='put the files and records of archived-projects/ where their project says',
  )
  modes.add_argument(
    '--opencode',
    type=Path,
    metavar='DB',
    help='import or update the sessions of an opencode.db in archived-projects/',
  )
  parser.add_argument(
    '--data-dir',
    type=Path,
    help='default: $PI_WEB_DATA_DIR or ~/.pi-web',
  )
  parser.add_argument('--dry-run', action='store_true', help='report without changes')
  parser.add_argument(
    '--fix-mtime',
    action='store_true',
    help='--repair: set the mtime of a session file later than its last entry to it',
  )
  parser.add_argument(
    '--name-unnamed',
    action='store_true',
    help='forward and --opencode modes: name unnamed sessions with pi -p',
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
  if args.opencode is not None:
    mode = 'opencode'
  if args.name_unnamed and mode not in {'forward', 'opencode'}:
    parser.error('--name-unnamed works only in the forward and --opencode modes')
  if args.fix_mtime and mode != 'repair':
    parser.error('--fix-mtime works only with --repair')
  naming = None
  if args.name_unnamed:
    naming = NamingOptions(
      pi=args.pi,
      model=args.model,
      limit=args.name_limit,
      timeout=args.name_timeout,
    )
  data_dir = default_data_dir() if args.data_dir is None else absolute(args.data_dir)
  opencode = None if args.opencode is None else absolute(args.opencode)
  return Options(
    data_dir=data_dir,
    mode=mode,
    dry_run=args.dry_run,
    naming=naming,
    opencode=opencode,
    fix_mtime=args.fix_mtime,
  )


def main(argv: list[str] | None = None) -> int:
  options = parse_options(sys.argv[1:] if argv is None else argv)
  converter = Converter(options)
  try:
    converter.run()
  except (OSError, TypeError, ValueError, sqlite3.Error) as error:
    say(f'error: {error}')
    converter.report()
    return 2
  converter.report()
  return 1 if converter.counts['problems'] else 0


if __name__ == '__main__':
  sys.exit(main())
