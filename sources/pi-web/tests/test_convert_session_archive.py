# Regression tests of convert-session-archive.py: the opencode import and --repair.
# ruff: file-ignore[implicit-namespace-package]
# cspell:ignore Csvg executescript opencode QUJD QUJDRA filediff todowrite todos
# cspell:ignore mtime atime utime deepcopy cwds ufeffa cfuvid relatime ENOSPC

from __future__ import annotations

import contextlib
import copy
import errno
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from operator import attrgetter, itemgetter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from unittest import mock

if TYPE_CHECKING:
  from collections.abc import Callable, Iterator
  from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'convert-session-archive.py'
CWD = '/work/proj-a'
OTHER_CWD = '/work/proj-b'
BASE_MS = 1_760_000_000_000
NS_PER_MS = 1_000_000
DEFAULT_TITLE = 'New session - 2025-10-09T08:53:20.000Z'
HIDDEN_VALUE = 'share-secret-1f2e3d'
SHARE_LINK = 'https://share.example/s/abc123'
ENV_KEYS = (
  'PI_WEB_AGENT_DIR',
  'PI_CODING_AGENT_DIR',
  'PI_WEB_AGENT_SESSION_DIR',
  'PI_CODING_AGENT_SESSION_DIR',
  'PI_WEB_CONFIG',
  'PI_WEB_DATA_DIR',
  'PI_WEB_PROJECTS_FILE',
)
TABLES = (
  'CREATE TABLE project (id TEXT PRIMARY KEY, worktree TEXT, vcs TEXT, name TEXT,'
  ' time_created INTEGER, time_updated INTEGER, sandboxes TEXT);'
  'CREATE TABLE session (id TEXT PRIMARY KEY, project_id TEXT, parent_id TEXT,'
  ' slug TEXT, directory TEXT, title TEXT, version TEXT, share_url TEXT,'
  ' time_created INTEGER, time_updated INTEGER, time_archived INTEGER);'
  'CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER,'
  ' time_updated INTEGER, data TEXT);'
  'CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,'
  ' time_created INTEGER, time_updated INTEGER, data TEXT);'
  'CREATE TABLE todo (session_id TEXT, content TEXT, status TEXT, priority TEXT,'
  ' position INTEGER, time_created INTEGER, time_updated INTEGER);'
  'CREATE TABLE session_message (id TEXT PRIMARY KEY, session_id TEXT, type TEXT,'
  ' time_created INTEGER, time_updated INTEGER, data TEXT);'
  'CREATE TABLE session_share (session_id TEXT PRIMARY KEY, id TEXT, secret TEXT,'
  ' url TEXT, time_created INTEGER, time_updated INTEGER);'
)
THIRTY_LINES = '\n'.join(f'line {number}' for number in range(1, 31))
PI_DIFF = (
  '  1 line 1\n  2 line 2\n- 3 line 3\n+ 3 LINE 3\n+ 4 extra\n  4 line 4\n'
  '  5 line 5\n  6 line 6\n  7 line 7\n    ...\n 16 line 16\n 17 line 17\n'
  ' 18 line 18\n 19 line 19\n-20 line 20\n 21 line 21\n 22 line 22\n 23 line 23\n'
  ' 24 line 24\n    ...'
)
HUNKS = (
  '--- a.py\n+++ a.py\n@@ -10,3 +10,3 @@\n x\n-y\n+Y\n z\n'
  '@@ -40,2 +40,3 @@\n p\n+q\n r\n'
)
HUNKS_DIFF = ' 10 x\n-11 y\n+11 Y\n 12 z\n    ...\n 40 p\n+41 q\n 41 r'
PATCH_HEAD = 'Success. Updated the following files:\n'
LSP_BLOCK = (
  '\n\nLSP errors detected in b.md, please fix:\n<diagnostics file="/work/proj-a/b.md">'
  '\nERROR [1:1] bad\n</diagnostics>'
)
TWO_FILES_PATCH = (
  '*** Begin Patch\n*** Update File: a.py\n@@\n-x\n+y\n*** Add File: b.md\n+new\n'
  '*** End Patch\n'
)
PI_CONTEXT_KINDS = frozenset({'message', 'compaction', 'branch_summary'})


def load_script() -> ModuleType:
  spec = importlib.util.spec_from_file_location('convert_session_archive', SCRIPT)
  if spec is None or spec.loader is None:
    message = f'cannot load {SCRIPT}'
    raise ImportError(message)
  module = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = module
  spec.loader.exec_module(module)
  return module


class OpencodeDb:
  # A synthetic opencode database with the tables of opencode 1.15.13 the script reads.
  def __init__(self, path: Path) -> None:
    self.path = path
    self.parts = 0
    with contextlib.closing(sqlite3.connect(path)) as connection:
      connection.executescript(TABLES)

  def _run(self, sql: str, values: tuple[object, ...]) -> None:
    with contextlib.closing(sqlite3.connect(self.path)) as connection:
      connection.execute('PRAGMA synchronous=OFF')
      connection.execute(sql, values)
      connection.commit()

  def rows(self, sql: str, values: tuple[object, ...] = ()) -> list[dict[str, Any]]:
    with contextlib.closing(sqlite3.connect(self.path)) as connection:
      cursor = connection.execute(sql, values)
      names = [column[0] for column in cursor.description]
      return [dict(zip(names, row)) for row in cursor]

  def session(
    self,
    session_id: str,
    offset: int,
    parent: str | None = None,
    **fields: object,
  ) -> None:
    moment = BASE_MS + offset
    row = {
      'id': session_id,
      'project_id': 'prj_a',
      'parent_id': parent,
      'slug': 'quick-falcon',
      'directory': CWD,
      'title': 'A real title',
      'version': '1.2.16',
      'share_url': None,
      'time_created': moment,
      'time_updated': moment + 1,
      'time_archived': None,
      **fields,
    }
    self.insert('session', row)

  def insert(self, table: str, row: dict[str, object]) -> None:
    names = ', '.join(row)
    marks = ', '.join('?' for _ in row)
    sql = f'INSERT INTO {table} ({names}) VALUES ({marks})'  # noqa: S608  # nosec B608
    self._run(sql, tuple(row.values()))

  def message(
    self,
    session_id: str,
    message_id: str,
    offset: int,
    data: dict[str, object],
    parts: list[dict[str, object]] | None = None,
  ) -> None:
    moment = BASE_MS + offset
    self.insert(
      'message',
      {
        'id': message_id,
        'session_id': session_id,
        'time_created': moment,
        'time_updated': moment + 2,
        'data': json.dumps(data),
      },
    )
    for part in parts or []:
      self.parts += 1
      self.insert(
        'part',
        {
          'id': f'prt_{self.parts:05d}',
          'message_id': message_id,
          'session_id': session_id,
          'time_created': moment,
          'time_updated': moment + 1,
          'data': json.dumps(part),
        },
      )

  def exchange(self, session_id: str, offset: int) -> None:
    self.message(session_id, f'{session_id}-u', offset, user_data(), [text('hi')])
    self.message(
      session_id,
      f'{session_id}-a',
      offset + 1,
      assistant_data(),
      [text('hello')],
    )


def text(value: str) -> dict[str, object]:
  return {
    'type': 'text',
    'text': value,
  }


def user_data(model: str = 'm1') -> dict[str, object]:
  return {
    'role': 'user',
    'model': {
      'providerID': 'p',
      'modelID': model,
    },
  }


def assistant_data(**fields: object) -> dict[str, object]:
  return {
    'role': 'assistant',
    'providerID': 'p',
    'modelID': 'm1',
    **fields,
  }


def tool(
  name: str,
  given: dict[str, object],
  output: str | None = 'done',
  **state: object,
) -> dict[str, object]:
  # A completed opencode tool part unless the state says otherwise.
  body: dict[str, object] = {
    'status': 'completed',
    'input': given,
    'title': name,
    'metadata': {
      'truncated': False,
    },
    'time': {
      'start': BASE_MS + 50,
      'end': BASE_MS + 60,
    },
    **state,
  }
  if output is not None:
    body['output'] = output
  return {
    'type': 'tool',
    'callID': f'call_{name}',
    'tool': name,
    'state': body,
  }


class BrokenFile(io.BytesIO):
  # A file that gives its header and fails with EIO on the lines after it.
  def __init__(self, path: Path, contents: bytes) -> None:
    super().__init__(contents)
    self.path = path

  def __iter__(self) -> Iterator[bytes]:
    yield self.readline()
    raise OSError(errno.EIO, 'Input/output error', str(self.path))


def broken(*paths: Path, header: bool) -> Any:  # noqa: ANN401
  # Reading the files of these names fails with EIO: at the open, or after the header.
  real = Path.open
  names = set(map(attrgetter('name'), paths))

  def opener(path: Path, mode: str = 'r', *args: object, **kwargs: object) -> object:
    if path.name not in names or mode != 'rb':
      return real(path, mode, *args, **kwargs)  # type: ignore[call-overload]
    if not header:
      raise OSError(errno.EIO, 'Input/output error', str(path))
    with real(path, mode) as handle:
      return BrokenFile(path, handle.read())

  return mock.patch.object(Path, 'open', opener)


def message_text(entry: dict[str, object]) -> str | None:
  if entry['type'] != 'message':
    return None
  content = entry['message']['content']  # type: ignore[index]
  return content[0].get('text') if content else None


def step(
  input_tokens: int,
  output_tokens: int,
  cost: float,
  reasoning: int = 0,
) -> dict[str, object]:
  return {
    'type': 'step-finish',
    'cost': cost,
    'tokens': {
      'input': input_tokens,
      'output': output_tokens,
      'reasoning': reasoning,
      'cache': {
        'read': 0,
        'write': 0,
      },
    },
  }


def read_entries(path: Path) -> list[dict[str, Any]]:
  return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def full_tools() -> list[dict[str, object]]:
  # One part of every kind a tool step of opencode leaves.
  filediff = {
    'file': 'a.py',
    'before': 'x\n',
    'after': 'y\n',
    'additions': 1,
  }
  edit = tool(
    'edit',
    {
      'filePath': 'a.py',
      'oldString': 'x',
      'newString': 'y',
    },
    metadata={
      'filediff': filediff,
      'diff': 'Index: a.py',
      'diagnostics': {},
    },
  )
  edit['metadata'] = {
    'openai': {
      'itemId': 'i1',
    },
  }
  todos = [{
    'content': 'a',
    'status': 'pending',
  }]
  return [
    {
      'type': 'step-start',
      'snapshot': 'abc',
    },
    {
      'type': 'reasoning',
      'text': 'think',
      'metadata': {
        'signature': 's',
      },
    },
    text('done'),
    edit,
    tool('bash', {
      'command': 'ls',
    }, 'out', metadata={
      'output': 'out',
      'exit': 0,
    }),
    tool('todowrite', {
      'todos': todos,
    }, '[]', metadata={
      'todos': todos,
    }),
    tool('read', {
      'filePath': 'a.py',
      'offset': 3,
    }, '<file>\n00004| x'),
    tool('bash', {
      'command': 'false',
    }, None, status='error', error='exit 1'),
    tool('bash', {
      'command': 'sleep',
    }, None, status='running'),
    step(10, 5, 0.25),
    {
      'type': 'patch',
      'hash': 'h1',
      'files': ['a.py'],
    },
    {
      'type': 'agent',
      'name': 'explore',
    },
  ]


class Restored:
  # The opencode rows rebuilt from the standard and custom entries of a pi session file.
  def __init__(self, module: ModuleType, entries: list[dict[str, Any]]) -> None:
    session_id = entries[0]['id']
    by_id = {
      entry['id']: entry
      for entry in entries[1:]
    }
    customs = [entry for entry in entries if entry['type'] == 'custom']
    self.session = next(
      entry['data'] for entry in customs
      if entry['customType'] == 'opencode-import.session'
    )
    self.events = [
      entry['data']['row'] for entry in customs
      if entry['customType'] == 'opencode-import.event'
    ]
    parts = {
      entry['data']['partID']: entry['data']
      for entry in customs if entry['customType'] == 'opencode-import.part'
    }
    self.messages: list[dict[str, Any]] = []
    self.parts: list[dict[str, Any]] = []
    for entry in customs:
      if entry['customType'] != 'opencode-import.message':
        continue
      record = entry['data']
      message = {
        'id': record['messageID'],
        'session_id': session_id,
        **record['message'],
        'data': record['data'],
      }
      self.messages.append(message)
      for item in record['parts']:
        stored = parts[item['partID']] if 'record' in item else item
        data = copy.deepcopy(stored['data'])
        for moved in stored.get('moved', []):
          value = module.get_path(by_id[moved['entry']], moved['to'])
          module.put_path(data, moved['from'], value)
        part = {
          'id': item['partID'],
          'message_id': record['messageID'],
          'session_id': session_id,
          **stored['part'],
          'data': data,
        }
        self.parts.append(part)


class Slot(Protocol):
  # The places of TreeSlot of the script the tests look at.
  day_dir: Path
  month_dir: Path
  summary: Path


def pi_context(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
  # buildContextEntries of pi 0.85.1: the path to the leaf cut at its last compaction.
  by_id = {
    entry['id']: entry
    for entry in entries[1:]
  }
  path: list[dict[str, Any]] = []
  current: dict[str, Any] | None = entries[-1]
  while current is not None:
    path.append(current)
    current = by_id.get(current['parentId'])
  path.reverse()
  kinds = [entry['type'] for entry in path]
  compactions = [index for index, kind in enumerate(kinds) if kind == 'compaction']
  if not compactions:
    return path
  last = compactions[-1]
  kept = path[last]['firstKeptEntryId']
  start = next(
    (index for index, entry in enumerate(path[:last]) if entry['id'] == kept),
    last,
  )
  return [path[last], *path[start:last], *path[last + 1:]]


class ConvertTestCase(unittest.TestCase):
  def setUp(self) -> None:
    self.module = load_script()
    self.module.PROGRESS_SECONDS = 1e9  # type: ignore[attr-defined]
    holder = tempfile.TemporaryDirectory()
    self.addCleanup(holder.cleanup)
    self.root = Path(holder.name)
    self.data_dir = self.root / 'data'
    self.data_dir.mkdir()
    self.tree = self.data_dir / self.module.TREE_DIR_NAME
    self.db = OpencodeDb(self.root / 'opencode.db')
    self.use_sessions('sessions-a')

  def same(self, actual: object, expected: object, note: str = '') -> None:
    if actual != expected:
      self.fail(f'{actual!r} != {expected!r} {note}')

  def has(self, item: object, container: object) -> None:
    if item not in container:  # type: ignore[operator]
      self.fail(f'{item!r} is not in {container!r}')

  def lacks(self, item: object, container: object) -> None:
    if item in container:  # type: ignore[operator]
      self.fail(f'{item!r} is in {container!r}')

  def use_sessions(self, name: str) -> None:
    self.agent = self.root / 'agent'
    self.sessions = self.root / name
    environment: dict[str, str] = dict.fromkeys(ENV_KEYS, '')
    environment['PI_WEB_AGENT_DIR'] = str(self.agent)
    environment['PI_WEB_AGENT_SESSION_DIR'] = str(self.sessions)
    environment['PI_WEB_CONFIG'] = str(self.root / 'config.json')
    self.environment(environment)

  def use_agent_sessions(self) -> None:
    # Every project gets its own session directory under the agent, as in pi.
    self.environment({
      'PI_WEB_AGENT_SESSION_DIR': '',
    })

  def environment(self, values: dict[str, str]) -> None:
    patcher = mock.patch.dict(os.environ, values)
    patcher.start()
    self.addCleanup(patcher.stop)
    self.module.agent_dir.cache_clear()
    self.module.default_session_dir.cache_clear()

  def run_script(self, *argv: str) -> tuple[int, str]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
      code = self.module.main(['--data-dir', str(self.data_dir), *argv])
    return code, output.getvalue()

  def run_import(self, *extra: str) -> tuple[int, str]:
    return self.run_script('--opencode', str(self.db.path), *extra)

  def run_repair(self, *extra: str) -> tuple[int, str]:
    return self.run_script('--repair', *extra)

  def slot(self, offset: int, cwd: str = CWD) -> Slot:
    return self.module.TreeSlot.at(self.tree, cwd, BASE_MS + offset)

  def file_of(self, session_id: str, offset: int, cwd: str = CWD) -> Path:
    slot = self.slot(offset, cwd)
    return next(slot.day_dir.glob(f'*_{session_id}.jsonl'))

  def entries_of(self, session_id: str, offset: int) -> list[dict[str, Any]]:
    return read_entries(self.file_of(session_id, offset))

  def summary_of(
    self,
    session_id: str,
    offset: int,
    cwd: str = CWD,
  ) -> dict[str, Any]:
    slot = self.slot(offset, cwd)
    sessions = json.loads(slot.summary.read_text(encoding='utf-8'))['sessions']
    return next(item for item in sessions if item['sessionId'] == session_id)

  def messages_of(self, session_id: str, offset: int) -> list[dict[str, Any]]:
    entries = self.entries_of(session_id, offset)
    return [entry['message'] for entry in entries if entry['type'] == 'message']

  def latest_ms(self, path: Path) -> int:
    # The time of the latest entry, which the file gets as its mtime.
    entries = read_entries(path)
    moments: list[int | None] = [
      self.module.parse_iso_ms(entry['timestamp']) for entry in entries
    ]
    return max(moment for moment in moments if moment is not None)

  def latest_moment_ns(self, path: Path) -> int:
    return self.latest_ms(path) * NS_PER_MS

  def imported(self, session_id: str, offset: int, parent: str | None = None) -> Path:
    self.db.session(session_id, offset, parent)
    self.db.exchange(session_id, offset + 10)
    code, output = self.run_import()
    self.same(code, 0, output)
    return self.file_of(session_id, offset)

  def stashed(
    self,
    path: Path,
    *,
    hidden: bool = False,
    keep_record: bool = True,
  ) -> Path:
    # The file moves into a stash directory of its project, maybe under a hidden name.
    if not keep_record:
      self.slot(0).summary.unlink()
    stash = self.tree / self.module.project_dir_name(CWD) / 'stash'
    stash.mkdir(exist_ok=True)
    moved = stash / (f'.{path.name}' if hidden else path.name)
    path.replace(moved)
    return moved


class ParentLinkTest(ConvertTestCase):
  def test_parent_file_conflict_leaves_child_without_link(self) -> None:
    self.db.session('ses_parent', 0)
    self.db.exchange('ses_parent', 10)
    self.db.session('ses_child', 100, 'ses_parent')
    self.db.exchange('ses_child', 110)
    slot = self.slot(0)
    squatter = slot.day_dir / self.module.OpencodeSession(
      'ses_parent',
      None,
      CWD,
      '',
      BASE_MS,
      BASE_MS,
      None,
    ).file_name
    squatter.parent.mkdir(parents=True)
    squatter.write_text('{"type":"session","id":"other"}\n', encoding='utf-8')
    code, output = self.run_import()
    self.same(code, 1, output)
    self.has('problems: 1', output)
    self.lacks('parentSession', self.entries_of('ses_child', 100)[0])
    self.lacks('parentSessionPath', self.summary_of('ses_child', 100))

  def test_parent_origin_is_kept_after_session_dir_change(self) -> None:
    self.db.session('ses_parent', 0)
    self.db.exchange('ses_parent', 10)
    self.same(self.run_import()[0], 0)
    origin = self.summary_of('ses_parent', 0)['originalPath']
    self.db.session('ses_child', 100, 'ses_parent')
    self.db.exchange('ses_child', 110)
    self.use_sessions('sessions-b')
    self.same(self.run_import()[0], 0)
    self.same(self.entries_of('ses_child', 100)[0]['parentSession'], origin)
    self.same(self.summary_of('ses_child', 100)['parentSessionPath'], origin)
    self.same(self.summary_of('ses_parent', 0)['originalPath'], origin)

  def test_child_rows_before_parent_rows_still_link(self) -> None:
    self.db.session('ses_child', 0, 'ses_parent')
    self.db.exchange('ses_child', 10)
    self.db.session('ses_parent', 100)
    self.db.exchange('ses_parent', 110)
    self.same(self.run_import()[0], 0)
    origin = self.summary_of('ses_parent', 100)['originalPath']
    self.same(self.entries_of('ses_child', 0)[0]['parentSession'], origin)
    self.same(self.summary_of('ses_child', 0)['parentSessionPath'], origin)

  def test_grandchild_before_its_ancestors_links_the_chain(self) -> None:
    self.db.session('ses_c', 0, 'ses_b')
    self.db.exchange('ses_c', 10)
    self.db.session('ses_b', 100, 'ses_a')
    self.db.exchange('ses_b', 110)
    self.db.session('ses_a', 200)
    self.db.exchange('ses_a', 210)
    self.same(self.run_import()[0], 0)
    for child, parent, child_at, parent_at in (
      ('ses_c', 'ses_b', 0, 100),
      ('ses_b', 'ses_a', 100, 200),
    ):
      origin = self.summary_of(parent, parent_at)['originalPath']
      self.same(self.entries_of(child, child_at)[0]['parentSession'], origin)

  def test_dry_run_counts_are_unchanged_by_the_order(self) -> None:
    self.db.session('ses_child', 0, 'ses_parent')
    self.db.exchange('ses_child', 10)
    self.db.session('ses_parent', 100)
    self.db.exchange('ses_parent', 110)
    code, output = self.run_import('--dry-run')
    self.same(code, 0, output)
    self.has('imported: 2', output)
    self.same(list(self.data_dir.glob(self.module.TREE_DIR_NAME)), [])

  def test_child_of_skipped_empty_parent_has_no_link(self) -> None:
    self.db.session('ses_parent', 0)
    self.db.session('ses_child', 100, 'ses_parent')
    self.db.exchange('ses_child', 110)
    code, output = self.run_import()
    self.same(code, 0, output)
    self.has('skipped: 1', output)
    self.lacks('parentSession', self.entries_of('ses_child', 100)[0])
    self.lacks('parentSessionPath', self.summary_of('ses_child', 100))

  def test_parent_restored_to_its_original_path_is_linked_there(self) -> None:
    self.db.session('ses_parent', 0)
    self.db.exchange('ses_parent', 10)
    self.same(self.run_import()[0], 0)
    original = Path(str(self.summary_of('ses_parent', 0)['originalPath']))
    original.parent.mkdir(parents=True, exist_ok=True)
    self.file_of('ses_parent', 0).replace(original)
    self.db.session('ses_child', 100, 'ses_parent')
    self.db.exchange('ses_child', 110)
    self.same(self.run_import()[0], 0)
    self.same(self.entries_of('ses_child', 100)[0]['parentSession'], str(original))

  def test_parent_in_the_flat_archive_is_linked_by_its_index_record(self) -> None:
    self.db.session('ses_parent', 0)
    self.db.exchange('ses_parent', 10)
    self.same(self.run_import()[0], 0)
    recorded = '/elsewhere/ses_parent.jsonl'
    flat = self.data_dir / self.module.FLAT_DIR_NAME
    flat.mkdir()
    archived = self.file_of('ses_parent', 0)
    archived.replace(flat / archived.name)
    index = {
      'sessions': [{
        'sessionId': 'ses_parent',
        'originalPath': recorded,
      }],
    }
    (self.data_dir / self.module.INDEX_NAME).write_text(json.dumps(index))
    self.db.session('ses_child', 100, 'ses_parent')
    self.db.exchange('ses_child', 110)
    self.same(self.run_import()[0], 0)
    self.same(self.entries_of('ses_child', 100)[0]['parentSession'], recorded)


class EmptyAssistantTest(ConvertTestCase):
  def test_step_finish_only_assistant_keeps_usage_and_cost(self) -> None:
    self.db.session('ses_a', 0)
    self.db.message('ses_a', 'm1', 10, user_data(), [text('hi')])
    steps = [step(10, 5, 0.25, reasoning=2), step(20, 7, 0.5)]
    self.db.message('ses_a', 'm2', 11, assistant_data(finish='stop'), steps)
    self.db.message('ses_a', 'm3', 12, user_data(), [text('more')])
    self.db.message('ses_a', 'm4', 13, assistant_data(finish='length'), [step(3, 4, 1)])
    code, output = self.run_import()
    self.same(code, 0, output)
    messages = self.messages_of('ses_a', 0)
    empty = [item for item in messages if item.get('content') == []]
    self.same([item['stopReason'] for item in empty], ['stop', 'length'])
    first, second = (item['usage'] for item in empty)
    counts = first['input'], first['output'], first['reasoning']
    self.same(counts, (30, 14, 2))
    self.same(first['totalTokens'], 44)
    self.same(first['cost']['total'], 0.75)
    self.same((second['input'], second['output']), (3, 4))
    self.same(second['cost']['total'], 1)
    self.same(self.summary_of('ses_a', 0)['messageCount'], len(messages))

  def test_assistant_with_tokens_in_data_and_no_parts_is_kept(self) -> None:
    self.db.session('ses_a', 0)
    self.db.message('ses_a', 'm1', 10, user_data(), [text('hi')])
    tokens = {
      'input': 5,
      'output': 6,
      'cache': {
        'read': 0,
        'write': 0,
      },
    }
    self.db.message('ses_a', 'm2', 11, assistant_data(tokens=tokens, cost=0.5))
    self.same(self.run_import()[0], 0)
    reply = self.messages_of('ses_a', 0)[1]
    self.same(reply['content'], [])
    counts = reply['usage']['input'], reply['usage']['output']
    self.same(counts, (5, 6))
    self.same(reply['usage']['cost']['total'], 0.5)

  def kept_usage_only(self, data: dict[str, object]) -> dict[str, Any]:
    self.db.session('ses_a', 0)
    self.db.message('ses_a', 'm1', 10, user_data(), [text('hi')])
    self.db.message('ses_a', 'm2', 11, assistant_data(**data))
    self.same(self.run_import()[0], 0)
    messages = self.messages_of('ses_a', 0)
    self.same([item['role'] for item in messages], ['user', 'assistant'])
    self.same(messages[1]['content'], [])
    self.same(self.summary_of('ses_a', 0)['messageCount'], len(messages))
    return messages[1]['usage']  # type: ignore[no-any-return]

  def test_assistant_with_only_cost_in_data_and_no_parts_is_kept(self) -> None:
    usage = self.kept_usage_only({
      'tokens': {
        'input': 0,
        'output': 0,
      },
      'cost': 0.5,
    })
    self.same(usage['cost']['total'], 0.5)
    self.same(usage['totalTokens'], 0)

  def test_assistant_with_only_total_tokens_in_data_and_no_parts_is_kept(self) -> None:
    usage = self.kept_usage_only({
      'tokens': {
        'total': 9,
      },
      'cost': 0,
    })
    self.same(usage['totalTokens'], 9)
    self.same((usage['input'], usage['output']), (0, 0))
    self.same(usage['cost']['total'], 0)

  def test_assistant_is_written_only_with_step_finish_or_usage(self) -> None:
    self.db.session('ses_a', 0)
    self.db.message('ses_a', 'm1', 10, user_data(), [text('hi')])
    self.db.message('ses_a', 'm2', 11, assistant_data(finish='stop'))
    self.db.message('ses_a', 'm3', 12, assistant_data(finish='unknown'), [step(0, 0, 0)])
    self.db.message(
      'ses_a',
      'm4',
      13,
      assistant_data(tokens={
        'input': 0,
        'output': 0,
      }, cost=0),
    )
    self.same(self.run_import()[0], 0)
    messages = self.messages_of('ses_a', 0)
    self.same([item['role'] for item in messages], ['user', 'assistant'])
    self.same(self.summary_of('ses_a', 0)['messageCount'], 2)


class CompactionTailTest(ConvertTestCase):
  def compaction(self, tail: str | None, at: int, suffix: str = '') -> None:
    self.db.message(
      'ses_a',
      f'cu{suffix}',
      at,
      user_data(),
      [{
        'type': 'compaction',
        'tail_start_id': tail,
      }],
    )
    self.db.message(
      'ses_a',
      f'cs{suffix}',
      at + 1,
      assistant_data(summary=True),
      [text('the summary'), step(1, 1, 0.1)],
    )

  def compaction_entry(self) -> dict[str, Any]:
    entries = self.entries_of('ses_a', 0)
    return next(entry for entry in entries if entry['type'] == 'compaction')

  def rows(self, third: dict[str, object], parts: list[dict[str, object]]) -> None:
    self.db.session('ses_a', 0)
    self.db.message('ses_a', 'm1', 10, user_data(), [text('first')])
    self.db.message('ses_a', 'm2', 11, assistant_data(), [text('answer')])
    self.db.message('ses_a', 'm3', 12, third, parts)

  def kept(self, tail: str, wanted: Callable[[dict[str, Any]], bool]) -> None:
    self.compaction(tail, 20)
    self.same(self.run_import()[0], 0)
    entry = next(item for item in self.entries_of('ses_a', 0) if wanted(item))
    self.same(self.compaction_entry()['firstKeptEntryId'], entry['id'])

  def test_tail_on_message_without_entries_keeps_the_next_model_change(self) -> None:
    self.rows({
      'role': 'user',
    }, [])
    self.db.message('ses_a', 'm4', 13, assistant_data(modelID='m2'), [text('tail')])
    self.kept('m3', lambda e: e['type'] == 'model_change' and e['modelId'] == 'm2')

  def test_tail_on_message_without_entries_keeps_a_text_message(self) -> None:
    self.rows({
      'role': 'user',
    }, [])
    self.db.message('ses_a', 'm4', 13, assistant_data(), [text('tail')])
    self.kept('m3', lambda e: message_text(e) == 'tail')

  def test_tail_on_ordinary_message_points_to_it(self) -> None:
    self.rows(user_data(), [text('second')])
    self.db.message('ses_a', 'm4', 13, assistant_data(), [text('tail')])
    self.kept('m2', lambda e: message_text(e) == 'answer')

  def test_tail_on_empty_assistant_with_usage_points_to_its_entry(self) -> None:
    self.rows(assistant_data(), [step(1, 1, 0.1)])

    def empty_reply(e: dict[str, Any]) -> bool:
      message = e.get('message')
      return e['type'] == 'message' and message['content'] == []  # type: ignore[index]

    self.kept('m3', empty_reply)

  def test_tail_on_assistant_without_entries_keeps_the_next_model_change(self) -> None:
    self.rows(assistant_data(), [])
    self.db.message('ses_a', 'm4', 13, assistant_data(modelID='m2'), [text('tail')])
    self.kept('m3', lambda e: e['type'] == 'model_change' and e['modelId'] == 'm2')

  def test_tail_on_assistant_without_entries_keeps_the_next_text_message(self) -> None:
    self.rows(assistant_data(), [])
    self.db.message('ses_a', 'm4', 13, user_data(), [text('tail')])
    self.kept('m3', lambda e: message_text(e) == 'tail')

  def test_tail_on_assistant_without_entries_or_followers_points_to_compaction(
    self,
  ) -> None:
    self.rows(assistant_data(), [])
    self.kept('m3', lambda e: e['type'] == 'compaction')

  def test_tail_without_later_entries_points_to_the_compaction(self) -> None:
    self.rows({
      'role': 'user',
    }, [])
    self.kept('m3', lambda e: e['type'] == 'compaction')

  def test_tail_of_another_session_keeps_the_old_rule(self) -> None:
    self.rows(user_data(), [text('second')])
    self.kept('elsewhere', lambda e: e['type'] == 'compaction')

  def test_custom_entries_stay_out_of_the_context_and_the_history(self) -> None:
    self.rows(user_data(), [text('second')])
    self.db.message('ses_a', 'm4', 13, assistant_data(), [text('tail'), step(1, 1, 0)])
    self.compaction('m4', 20)
    self.db.message('ses_a', 'm5', 30, user_data(), [text('after')])
    self.same(self.run_import()[0], 0)
    entries = self.entries_of('ses_a', 0)
    seen = {entry['type']
            for entry in entries}
    self.has('custom', seen)
    context = pi_context(entries)
    kept = self.compaction_entry()['firstKeptEntryId']
    self.same(next(entry for entry in entries if entry['id'] == kept)['type'], 'message')
    texts = [message_text(entry) for entry in context if entry['type'] == 'message']
    self.same(texts, ['tail', 'after'])
    # The browser history of PI WEB takes only these kinds and no custom entries.
    history = [entry for entry in entries if entry['type'] in PI_CONTEXT_KINDS]
    self.lacks('custom', {entry['type']
                          for entry in history})


class RecordedOriginTest(ConvertTestCase):
  def restored_parent(self) -> tuple[Path, dict[str, Any]]:
    self.db.session('ses_parent', 0)
    self.db.exchange('ses_parent', 10)
    self.same(self.run_import()[0], 0)
    record = self.summary_of('ses_parent', 0)
    self.db.session('ses_child', 100, 'ses_parent')
    self.db.exchange('ses_child', 110)
    original = Path(str(record['originalPath']))
    original.parent.mkdir(parents=True, exist_ok=True)
    self.file_of('ses_parent', 0).replace(original)
    self.use_sessions('sessions-b')
    return original, record

  def parent_files(self) -> list[Path]:
    return list(self.tree.rglob('*_ses_parent.jsonl'))

  def test_restored_file_at_the_recorded_path_is_present(self) -> None:
    original, record = self.restored_parent()
    code, output = self.run_import()
    self.same(code, 0, output)
    self.has('present: 1', output)
    self.has('imported: 1', output)
    self.same(self.parent_files(), [])
    self.same(self.summary_of('ses_parent', 0), record)
    self.same(self.entries_of('ses_child', 100)[0]['parentSession'], str(original))
    self.same(self.summary_of('ses_child', 100)['parentSessionPath'], str(original))

  def test_other_session_at_the_recorded_path_is_not_present(self) -> None:
    original, _ = self.restored_parent()
    original.write_text('{"type":"session","id":"other"}\n', encoding='utf-8')
    code, output = self.run_import()
    self.same(code, 0, output)
    self.has('imported: 2', output)
    self.same(len(self.parent_files()), 1)

  def test_missing_file_at_the_recorded_path_is_imported_again(self) -> None:
    original, _ = self.restored_parent()
    original.unlink()
    code, output = self.run_import()
    self.same(code, 0, output)
    self.has('imported: 2', output)
    self.same(len(self.parent_files()), 1)

  def test_file_at_the_recorded_path_wins_over_the_copy_in_the_session_dir(
    self,
  ) -> None:
    original, _ = self.restored_parent()
    copied = self.module.default_session_dir(CWD) / original.name
    copied.parent.mkdir(parents=True, exist_ok=True)
    copied.write_bytes(original.read_bytes())
    code, output = self.run_import()
    self.same(code, 0, output)
    self.has('present: 1', output)
    self.has('imported: 1', output)
    self.same(self.entries_of('ses_child', 100)[0]['parentSession'], str(original))
    self.same(self.summary_of('ses_child', 100)['parentSessionPath'], str(original))

  def test_copy_in_the_session_dir_without_a_record_file_is_the_parent(self) -> None:
    original, _ = self.restored_parent()
    copied = self.module.default_session_dir(CWD) / original.name
    copied.parent.mkdir(parents=True, exist_ok=True)
    original.replace(copied)
    code, output = self.run_import()
    self.same(code, 0, output)
    self.has('present: 1', output)
    self.same(self.entries_of('ses_child', 100)[0]['parentSession'], str(copied))
    self.same(self.summary_of('ses_child', 100)['parentSessionPath'], str(copied))


class SymlinkedDatabaseTest(ConvertTestCase):
  def run_linked(self, journal: bytes | None) -> tuple[int, str]:
    self.db.session('ses_a', 0)
    self.db.exchange('ses_a', 10)
    real = self.root / 'real'
    real.mkdir()
    if journal is not None:
      (real / 'real.db-wal').write_bytes(journal)
    self.db.path.replace(real / 'real.db')
    alias = self.root / 'alias.db'
    alias.symlink_to(real / 'real.db')
    self.db.path = alias
    return self.run_import()

  def test_symlink_to_a_database_with_a_journal_is_refused(self) -> None:
    code, output = self.run_linked(b'x')
    self.same(code, 2, output)
    self.has('real.db-wal is not empty, quit opencode first', output)
    self.same(list(self.data_dir.iterdir()), [])

  def test_symlink_to_a_database_without_a_journal_is_imported(self) -> None:
    code, output = self.run_linked(None)
    self.same(code, 0, output)
    self.has('imported: 1', output)

  def test_symlink_to_a_database_with_an_empty_journal_is_imported(self) -> None:
    code, output = self.run_linked(b'')
    self.same(code, 0, output)
    self.has('imported: 1', output)


class ImageTypeTest(ConvertTestCase):
  def blocks(self, *urls: str) -> list[dict[str, Any]]:
    self.db.session('ses_a', 0)
    parts = [{
      'type': 'file',
      'mime': 'image/x',
      'url': url,
    } for url in urls]
    self.db.message('ses_a', 'm-u', 10, user_data(), parts)  # type: ignore[arg-type]
    self.same(self.run_import()[0], 0)
    return self.messages_of('ses_a', 0)[0]['content']  # type: ignore[no-any-return]

  def test_providers_images_stay_images(self) -> None:
    blocks = self.blocks('data:image/png;base64,QUJD', 'data:image/jpeg;base64,QUJD')
    self.same([item['type'] for item in blocks], ['image', 'image'])
    self.same([item['mimeType'] for item in blocks], ['image/png', 'image/jpeg'])

  def test_mime_case_and_parameters_do_not_matter(self) -> None:
    blocks = self.blocks('data:IMAGE/PNG;charset=x;base64,QUJD')
    self.same(blocks, [{
      'type': 'image',
      'data': 'QUJD',
      'mimeType': 'image/png',
    }])

  def test_bmp_becomes_a_note_with_the_decoded_size(self) -> None:
    blocks = self.blocks('data:image/bmp;base64,QUJDRA==')
    self.same(blocks[0]['text'], '[attachment image/bmp, 4 bytes omitted]')

  def test_svg_becomes_a_note_with_the_decoded_size(self) -> None:
    blocks = self.blocks(
      'data:image/svg+xml,%3Csvg%2F%3E',
      'data:image/svg+xml;base64,PHN2Zz4=',
    )
    self.same(blocks[0]['text'], '[attachment image/svg+xml, 6 bytes omitted]')
    self.same(blocks[1]['text'], '[attachment image/svg+xml, 5 bytes omitted]')


class ToolTestCase(ConvertTestCase):
  def calls(self, *parts: dict[str, object], version: str = '1.2.16') -> list[Any]:
    # The tool calls and results of one assistant message with the given parts.
    self.db.session('ses_t', 0, version=version)
    self.db.message('ses_t', 'm1', 10, user_data(), [text('go')])
    self.db.message('ses_t', 'm2', 11, assistant_data(), list(parts))
    code, output = self.run_import()
    self.same(code, 0, output)
    messages = self.messages_of('ses_t', 0)
    blocks = [block for block in messages[1]['content'] if block['type'] == 'toolCall']
    results = [message for message in messages if message['role'] == 'toolResult']
    return [*zip(blocks, results)]


class ToolTranslationTest(ToolTestCase):
  def test_tools_with_a_pi_counterpart_take_its_name_and_arguments(self) -> None:
    cases = (
      (
        tool(
          'edit',
          {
            'filePath': 'a.py',
            'oldString': 'x',
            'newString': 'y',
            'replaceAll': True,
          },
        ),
        'edit',
        {
          'path': 'a.py',
          'edits': [{
            'oldText': 'x',
            'newText': 'y',
          }],
          'replaceAll': True,
        },
      ),
      (
        tool('write', {
          'filePath': 'b.py',
          'content': 'z',
        }),
        'write',
        {
          'path': 'b.py',
          'content': 'z',
        },
      ),
      (
        tool('bash', {
          'command': 'ls',
          'timeout': 120000,
          'description': 'list',
        }),
        'bash',
        {
          'command': 'ls',
          'timeout': 120,
          'description': 'list',
        },
      ),
      (
        tool('bash', {
          'command': 'ls',
          'timeout': 1500,
        }),
        'bash',
        {
          'command': 'ls',
          'timeout': 1.5,
        },
      ),
      (
        tool('grep', {
          'pattern': 'x',
          'path': 'src',
          'include': '*.py',
        }),
        'grep',
        {
          'pattern': 'x',
          'path': 'src',
          'glob': '*.py',
        },
      ),
      (
        tool('glob', {
          'pattern': '*.md',
          'path': 'docs',
        }),
        'find',
        {
          'pattern': '*.md',
          'path': 'docs',
        },
      ),
      (
        tool('list', {
          'path': 'src',
          'ignore': ['dist'],
        }),
        'ls',
        {
          'path': 'src',
          'ignore': ['dist'],
        },
      ),
      (
        tool('todowrite', {
          'todos': [{
            'content': 'a',
          }],
        }),
        'todowrite',
        {
          'todos': [{
            'content': 'a',
          }],
        },
      ),
      (tool('mcp_search', {
        'query': 'q',
      }), 'mcp_search', {
        'query': 'q',
      }),
    )
    pairs = self.calls(*(case[0] for case in cases))
    for (part, name, arguments), (block, result) in zip(cases, pairs):
      with self.subTest(tool=part['tool']):
        self.same((block['name'], block['arguments']), (name, arguments))
        self.same((result['toolName'], result['toolCallId']), (name, block['id']))

  def test_a_key_pi_uses_stays_only_in_the_custom_entry(self) -> None:
    part = tool('grep', {
      'pattern': 'x',
      'include': '*.py',
      'glob': '*.md',
    })
    block, _ = self.calls(part)[0]
    self.same(block['arguments'], {
      'pattern': 'x',
      'glob': '*.py',
    })
    restored = Restored(self.module, self.entries_of('ses_t', 0))
    rebuilt = next(item for item in restored.parts if item['data']['type'] == 'tool')
    self.same(rebuilt['data'], part)

  def read_offset(self, offset: int, output: str | None, version: str) -> object:
    given = {
      'filePath': 'a.py',
      'offset': offset,
      'limit': 5,
    }
    failed = {} if output is not None else {
      'status': 'error',
      'error': 'failed',
    }
    block, _ = self.calls(tool('read', given, output, **failed), version=version)[0]
    self.same(block['arguments']['limit'], 5)
    return block['arguments']['offset']  # type: ignore[no-any-return]

  def test_read_offset_from_the_old_output_counts_from_zero(self) -> None:
    self.same(self.read_offset(10, '<file>\n00011| x', '1.2.16'), 11)

  def test_read_offset_from_the_new_output_counts_from_one(self) -> None:
    output = '<path>/w/a.py</path>\n<content>\n10: x'
    self.same(self.read_offset(10, output, '1.1.53'), 10)

  def test_read_offset_zero_in_the_new_output_is_one(self) -> None:
    self.same(self.read_offset(0, '<path>/w/a.py</path>', '1.2.16'), 1)

  def test_read_offset_without_output_follows_the_session_version(self) -> None:
    self.same(self.read_offset(10, None, '1.1.53'), 11)

  def test_read_offset_without_output_of_a_new_version_is_kept(self) -> None:
    self.same(self.read_offset(10, None, 'v1.2.16'), 10)


class DetailsTest(ToolTestCase):
  def test_edit_details_match_the_diff_of_pi(self) -> None:
    after = THIRTY_LINES.replace('line 3\n', 'LINE 3\nextra\n').replace('line 20\n', '')
    filediff = {
      'file': 'a.txt',
      'before': THIRTY_LINES,
      'after': after,
    }
    part = tool(
      'edit',
      {
        'filePath': 'a.txt',
        'oldString': 'line 3',
        'newString': 'LINE 3\nextra',
      },
      metadata={
        'filediff': filediff,
        'diff': 'Index: a.txt\n',
      },
    )
    _, result = self.calls(part)[0]
    details = result['details']
    self.same(details['diff'], PI_DIFF)
    self.same(details['firstChangedLine'], 3)
    self.same(details['patch'][:28], '--- a.txt\n+++ a.txt\n@@ -1,7 ')

  def test_diff_and_patch_of_short_texts_match_pi(self) -> None:
    details = self.module.text_details('f.txt', '', 'x\ny\n')
    self.same((details['diff'], details['firstChangedLine']), ('+1 x\n+2 y', 1))
    details = self.module.text_details('f.txt', 'a\nb', 'a\nb\n')
    self.same((details['diff'], details['firstChangedLine']), (' 1 a\n-2 b\n+2 b', 2))
    details = self.module.text_details('f.txt', 'a\nb\nc\n', 'a\nB\nc\n')
    self.same(details['patch'], '--- f.txt\n+++ f.txt\n@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n')

  def test_crlf_and_bom_are_normalized_like_pi(self) -> None:
    plain = self.module.text_details('f.txt', 'a\nb\n', 'a\nc\n')
    marked = self.module.text_details('f.txt', '\ufeffa\r\nb\r\n', 'a\r\nc\r\n')
    self.same(marked, plain)

  def test_edit_without_texts_takes_the_hunks_of_its_patch(self) -> None:
    part = tool(
      'edit',
      {
        'filePath': 'a.py',
        'oldString': 'y',
        'newString': 'Y',
      },
      metadata={
        'filediff': {
          'file': 'a.py',
          'patch': HUNKS,
        },
        'diff': HUNKS,
      },
    )
    _, result = self.calls(part, version='1.15.13')[0]
    self.same(
      result['details'],
      {
        'diff': HUNKS_DIFF,
        'patch': HUNKS,
        'firstChangedLine': 11,
      },
    )

  def test_write_gets_details_only_with_both_texts(self) -> None:
    with_texts = tool(
      'write',
      {
        'filePath': 'w.txt',
        'content': 'b\n',
      },
      metadata={
        'filediff': {
          'before': 'a\n',
          'after': 'b\n',
        },
      },
    )
    without = tool('write', {
      'filePath': 'v.txt',
      'content': 'b\n',
    })
    (_, first), (_, second) = self.calls(with_texts, without)
    self.same(first['details']['diff'], '-1 a\n+1 b')
    self.lacks('details', second)

  def two_files(self, output: str) -> list[Any]:
    files = [
      {
        'filePath': '/work/proj-a/a.py',
        'relativePath': 'a.py',
        'type': 'update',
        'before': 'x\n',
        'after': 'y\n',
      },
      {
        'filePath': '/work/proj-a/b.md',
        'relativePath': 'b.md',
        'type': 'add',
        'before': '',
        'after': 'new\n',
      },
    ]
    part = tool(
      'apply_patch',
      {
        'patchText': TWO_FILES_PATCH,
      },
      output,
      metadata={
        'files': files,
        'diff': 'joined',
      },
    )
    return self.calls(part)

  def test_apply_patch_on_two_files_becomes_two_calls(self) -> None:
    output = f'{PATCH_HEAD}M a.py\nA b.md{LSP_BLOCK}'
    (first, first_result), (second, second_result) = self.two_files(output)
    self.same((first['id'], second['id']), ('call_apply_patch-1', 'call_apply_patch-2'))
    self.same(
      first['arguments'],
      {
        'path':
          '/work/proj-a/a.py',
        'patchText':
          '*** Begin Patch\n*** Update File: a.py\n@@\n-x\n+y\n*** End Patch\n',
      },
    )
    self.same(
      second['arguments']['patchText'],
      '*** Begin Patch\n*** Add File: b.md\n+new\n*** End Patch\n',
    )
    self.same(first_result['content'][0]['text'], f'{PATCH_HEAD}M a.py')
    self.same(second_result['content'][0]['text'], f'{PATCH_HEAD}A b.md{LSP_BLOCK}')
    self.same(first_result['details']['diff'], '-1 x\n+1 y')
    self.same(second_result['details']['firstChangedLine'], 1)
    restored = Restored(self.module, self.entries_of('ses_t', 0))
    rebuilt = next(item for item in restored.parts if item['data']['type'] == 'tool')
    self.same(rebuilt['data']['state']['output'], output)

  def test_apply_patch_output_that_does_not_split_goes_to_the_first_call(self) -> None:
    output = 'something else'
    (_, first_result), (_, second_result) = self.two_files(output)
    self.same(first_result['content'][0]['text'], output)
    self.same(second_result['content'][0]['text'], '')


class LosslessTest(ConvertTestCase):
  def full_session(self) -> None:
    self.db.insert(
      'project',
      {
        'id': 'prj_a',
        'worktree': CWD,
        'vcs': 'git',
        'name': 'demo',
        'time_created': BASE_MS,
        'time_updated': BASE_MS,
        'sandboxes': '[]',
      },
    )
    self.db.session('ses_f', 0, share_url=SHARE_LINK, version='1.1.53')
    self.db.insert(
      'session_share',
      {
        'session_id': 'ses_f',
        'id': 'abc123',
        'secret': HIDDEN_VALUE,
        'url': SHARE_LINK,
        'time_created': BASE_MS,
        'time_updated': BASE_MS,
      },
    )
    self.db.insert(
      'todo',
      {
        'session_id': 'ses_f',
        'content': 'write tests',
        'status': 'pending',
        'priority': 'high',
        'position': 0,
        'time_created': BASE_MS,
        'time_updated': BASE_MS,
      },
    )
    self.event('evt_1', 5, 'agent-switched', {
      'agent': 'build',
    })
    self.event(
      'evt_2',
      15,
      'model-switched',
      {
        'model': {
          'id': 'm2',
          'providerID': 'p',
        },
      },
    )
    summary = {
      'diffs': [{
        'file': 'a.py',
        'before': 'x\n',
        'after': 'y\n',
      }],
    }
    image: dict[str, object] = {
      'type': 'file',
      'mime': 'image/png',
      'url': 'data:image/png;base64,QUJD',
    }
    self.db.message(
      'ses_f',
      'm1',
      10,
      {
        **user_data(),
        'summary': summary,
      },
      [text('please'), image],
    )
    self.db.message('ses_f', 'm2', 20, assistant_data(path={
      'cwd': CWD,
    }), full_tools())
    error = {
      'name': 'APIError',
      'data': {
        'message': 'bad gateway',
        'responseHeaders': {
          'Set-Cookie': '_cfuvid=1',
          'request-id': 'req_1',
        },
      },
    }
    self.db.message('ses_f', 'm3', 30, assistant_data(error=error), [step(1, 1, 0)])
    compaction = {
      'type': 'compaction',
      'auto': True,
    }
    self.db.message('ses_f', 'm4', 40, user_data(), [compaction])
    self.db.message(
      'ses_f',
      'm5',
      41,
      assistant_data(summary=True),
      [text('summary text'), {
        'type': 'reasoning',
        'text': 'why',
      }],
    )

  def event(self, event_id: str, offset: int, kind: str, data: dict[str, object]) -> None:
    moment = BASE_MS + offset
    self.db.insert(
      'session_message',
      {
        'id': event_id,
        'session_id': 'ses_f',
        'type': kind,
        'time_created': moment,
        'time_updated': moment,
        'data': json.dumps({
          **data,
          'time': {
            'created': moment,
          },
        }),
      },
    )

  def expected_messages(self) -> list[dict[str, Any]]:
    rows = self.db.rows('SELECT * FROM message ORDER BY id')
    for row in rows:
      row['data'] = json.loads(row['data'])
      headers = row['data'].get('error', {}).get('data', {}).get('responseHeaders', {})
      headers.pop('Set-Cookie', None)
    return rows

  def test_rows_of_opencode_are_rebuilt_from_the_session_file(self) -> None:
    self.full_session()
    code, output = self.run_import()
    self.same(code, 0, output)
    restored = Restored(self.module, self.entries_of('ses_f', 0))
    parts = self.db.rows('SELECT * FROM part ORDER BY id')
    for row in parts:
      row['data'] = json.loads(row['data'])
    self.same(sorted(restored.parts, key=itemgetter('id')), parts)
    messages = sorted(restored.messages, key=itemgetter('id'))
    self.same(messages, self.expected_messages())
    session = self.db.rows('SELECT * FROM session')[0]
    del session['share_url']
    self.same(restored.session['session'], session)
    self.same(restored.session['project'], self.db.rows('SELECT * FROM project')[0])
    self.same(restored.session['todos'], self.db.rows('SELECT * FROM todo'))
    self.same(restored.events, self.db.rows('SELECT * FROM session_message ORDER BY id'))

  def test_share_data_and_cookies_stay_out_of_the_archive(self) -> None:
    self.full_session()
    self.same(self.run_import()[0], 0)
    contents = self.file_of('ses_f', 0).read_text(encoding='utf-8')
    for value in (HIDDEN_VALUE, SHARE_LINK, '_cfuvid', 'Set-Cookie'):
      self.lacks(value, contents)
    self.has('req_1', contents)

  def test_events_lie_between_the_messages_by_time(self) -> None:
    self.full_session()
    self.same(self.run_import()[0], 0)
    entries = self.entries_of('ses_f', 0)
    order = [
      entry['data'].get('eventID') or entry['data'].get('messageID') for entry in entries
      if entry['type'] == 'custom'
      and entry['customType'] in {'opencode-import.event', 'opencode-import.message'}
    ]
    self.same(order, ['evt_1', 'm1', 'evt_2', 'm2', 'm3', 'm4', 'm5'])
    self.same(entries[1]['customType'], 'opencode-import.session')
    self.same(entries[1]['parentId'], None)


class FileTimeTest(ConvertTestCase):
  def fake_pi(self) -> Path:
    program = self.root / 'pi'
    script = '#!/bin/sh\ncat >/dev/null\necho "Generated name"\n'
    program.write_text(script, encoding='utf-8')
    program.chmod(0o700)
    return program

  def test_imported_file_has_the_time_of_its_last_entry(self) -> None:
    path = self.imported('ses_a', 0)
    self.same(path.stat().st_mtime_ns, self.latest_moment_ns(path))

  def test_naming_appends_at_the_time_of_the_latest_entry(self) -> None:
    self.db.session('ses_a', 0, title=DEFAULT_TITLE)
    self.db.exchange('ses_a', 10)
    code, output = self.run_import('--name-unnamed', '--pi', str(self.fake_pi()))
    self.same(code, 0, output)
    path = self.file_of('ses_a', 0)
    mtime = path.stat().st_mtime_ns
    entries = read_entries(path)
    self.same(entries[-1]['type'], 'session_info')
    named_at = self.module.parse_iso_ms(entries[-1]['timestamp'])
    self.same(named_at, self.latest_ms(path))
    self.same(mtime, self.latest_moment_ns(path))
    self.same(self.summary_of('ses_a', 0)['name'], 'Generated name')

  def test_appended_name_keeps_the_access_and_modification_times(self) -> None:
    path = self.imported('ses_a', 0)
    session = self.module.SessionFile(path)
    os.utime(path, ns=(BASE_MS * NS_PER_MS, BASE_MS * NS_PER_MS))
    before = path.stat()
    session.append_name('Named')
    after = path.stat()
    times = before.st_atime_ns, before.st_mtime_ns
    self.same((after.st_atime_ns, after.st_mtime_ns), times)
    self.same(read_entries(path)[-1]['name'], 'Named')

  def unnamed_pair(self) -> tuple[Path, Path]:
    # A read moves the access time of a file whose access time is not later than its
    # modification time or older than a day, as the file systems of most hosts do.
    for offset, session_id in ((0, 'ses_a'), (100, 'ses_b')):
      self.db.session(session_id, offset, title=DEFAULT_TITLE)
      self.db.exchange(session_id, offset + 10)
    self.same(self.run_import()[0], 0)
    return self.file_of('ses_a', 0), self.file_of('ses_b', 100)

  def stamp(self, early: Path, late: Path) -> list[tuple[Path, int, int]]:
    stamps = []
    for path, shift in ((early, -10**15), (late, 10**15)):
      mtime = self.latest_moment_ns(path)
      os.utime(path, ns=(mtime + shift, mtime))
      stamps.append((path, mtime + shift, mtime))
    return stamps

  def check_named(self, stamps: list[tuple[Path, int, int]], *argv: str) -> None:
    code, output = self.run_script(*argv, '--name-unnamed', '--pi', str(self.fake_pi()))
    self.same(code, 0, output)
    for stored, atime, mtime in stamps:
      path = stored if stored.exists() else next(self.tree.rglob(stored.name))
      status = path.stat()
      self.same((status.st_atime_ns, status.st_mtime_ns), (atime, mtime), str(path))
      self.same(read_entries(path)[-1]['name'], 'Generated name')

  def test_naming_in_the_import_keeps_the_times_of_a_stored_file(self) -> None:
    stamps = self.stamp(*self.unnamed_pair())
    self.check_named(stamps, '--opencode', str(self.db.path))

  def test_direct_naming_keeps_the_times_of_an_archived_file(self) -> None:
    self.unnamed_pair()
    self.same(self.run_script('--reverse')[0], 0)
    flat = sorted((self.data_dir / self.module.FLAT_DIR_NAME).glob('*.jsonl'))
    stamps = self.stamp(*flat)
    self.check_named(stamps)

  def test_direct_naming_of_a_file_without_a_record_keeps_its_times(self) -> None:
    self.unnamed_pair()
    self.same(self.run_script('--reverse')[0], 0)
    (self.data_dir / self.module.INDEX_NAME).unlink()
    flat = sorted((self.data_dir / self.module.FLAT_DIR_NAME).glob('*.jsonl'))
    stamps = self.stamp(*flat)
    self.check_named(stamps)

  def check_rewritten_and_named(self, *argv: str, named: bool) -> None:
    # Both files change in the import, whatever happens to their names.
    pair = self.unnamed_pair()
    counts = [len(read_entries(path)) for path in pair]
    stamps = self.stamp(*pair)
    for session_id, offset in (('ses_a', 30), ('ses_b', 130)):
      self.db.message(session_id, f'{session_id}-u2', offset, user_data(), [text('more')])
    pi = ('--pi', str(self.fake_pi()))
    code, output = self.run_script('--opencode', str(self.db.path), *argv, *pi)
    self.same(code, 0, output)
    self.has('updated: 2', output)
    for (path, atime, _), count in zip(stamps, counts, strict=True):
      status = path.stat()
      self.same(status.st_atime_ns, atime, str(path))
      entries = read_entries(path)
      self.has(len(entries), range(count + 1, count + 100))
      self.same(status.st_mtime_ns, self.latest_moment_ns(path), str(path))
      self.same(entries[-1]['type'] == 'session_info', named)

  def test_rewritten_file_left_unnamed_keeps_its_access_time(self) -> None:
    self.check_rewritten_and_named('--name-unnamed', '--name-limit', '0', named=False)

  def test_rewritten_file_that_gets_a_name_keeps_its_access_time(self) -> None:
    self.check_rewritten_and_named('--name-unnamed', named=True)

  def test_rewritten_read_only_file_keeps_its_access_time(self) -> None:
    stamps = self.stamp(*self.unnamed_pair())
    for path, _, _ in stamps:
      path.chmod(0o444)
    for session_id, offset in (('ses_a', 30), ('ses_b', 130)):
      self.db.message(session_id, f'{session_id}-u2', offset, user_data(), [text('more')])
    code, output = self.run_import('--name-unnamed', '--pi', str(self.fake_pi()))
    self.same(code, 0, output)
    for path, atime, _ in stamps:
      self.same(path.stat().st_atime_ns, atime, str(path))


class ReimportTest(ConvertTestCase):
  def import_more(self) -> None:
    self.db.message('ses_a', 'ses_a-u2', 30, user_data(), [text('more')])
    code, output = self.run_import()
    self.same(code, 0, output)
    self.has('updated: 1', output)

  def test_unchanged_reimport_keeps_the_file_as_it_is(self) -> None:
    path = self.imported('ses_a', 0)
    contents = path.read_bytes()
    mtime = path.stat().st_mtime_ns
    code, output = self.run_import()
    self.same(code, 0, output)
    self.has('present: 1', output)
    self.same(path.read_bytes(), contents)
    self.same(path.stat().st_mtime_ns, mtime)

  def test_unchanged_reimport_keeps_a_copied_files_mtime(self) -> None:
    # A copy without the file times or a restored backup has any mtime.
    early = self.imported('ses_a', 0)
    late = self.imported('ses_b', 100)
    stamps = []
    for path, shift in ((early, -10**15), (late, 10**15)):
      mtime = self.latest_moment_ns(path) + shift
      os.utime(path, ns=(mtime, mtime))
      stamps.append((path, path.read_bytes(), mtime))
    code, output = self.run_import()
    self.same(code, 0, output)
    self.has('present: 2', output)
    for path, contents, mtime in stamps:
      self.same((path.read_bytes(), path.stat().st_mtime_ns), (contents, mtime))

  def test_rewritten_file_gets_the_latest_time_and_keeps_its_mode(self) -> None:
    path = self.imported('ses_a', 0)
    path.chmod(0o640)
    mtime = self.latest_moment_ns(path) + 10**15
    os.utime(path, ns=(mtime, mtime))
    self.import_more()
    self.same(path.stat().st_mode & 0o777, 0o640)
    self.same(path.stat().st_mtime_ns, self.latest_moment_ns(path))

  def aged(self, early: Path, late: Path) -> list[tuple[Path, int, int]]:
    # The access time before and after the modification time, as a file system with
    # relatime moves it on a read; nothing reads the files until the stat.
    stamps = []
    for path, shift in ((early, -10**15), (late, 10**15)):
      path.chmod(0o640)
      mtime = self.latest_moment_ns(path)
      count = len(read_entries(path))
      os.utime(path, ns=(mtime + shift, mtime))
      stamps.append((path, path.stat().st_atime_ns, count))
    return stamps

  def check_rewritten(self, stamps: list[tuple[Path, int, int]]) -> None:
    code, output = self.run_import()
    self.same(code, 0, output)
    self.has('updated: 2', output)
    for path, atime, count in stamps:
      status = path.stat()
      self.has(len(read_entries(path)), range(count + 1, count + 100))
      self.same(status.st_atime_ns, atime, str(path))
      self.same(status.st_mtime_ns, self.latest_moment_ns(path), str(path))
      self.same(status.st_mode & 0o777, 0o640, str(path))

  def more(self) -> None:
    for session_id, offset in (('ses_a', 30), ('ses_b', 130)):
      self.db.message(session_id, f'{session_id}-u2', offset, user_data(), [text('more')])

  def test_rewritten_file_keeps_its_access_time(self) -> None:
    stamps = self.aged(self.imported('ses_a', 0), self.imported('ses_b', 100))
    self.more()
    self.check_rewritten(stamps)

  def test_rewritten_flat_file_keeps_its_access_time(self) -> None:
    self.imported('ses_a', 0)
    self.imported('ses_b', 100)
    self.same(self.run_script('--reverse')[0], 0)
    flat = sorted((self.data_dir / self.module.FLAT_DIR_NAME).glob('*.jsonl'))
    stamps = self.aged(*flat)
    self.more()
    self.check_rewritten(stamps)

  def test_reimport_keeps_names_and_archive_time(self) -> None:
    path = self.imported('ses_a', 0)
    self.module.SessionFile(path).append_name('Renamed')
    renamed = read_entries(path)[-1]
    slot = self.slot(0)
    summary = json.loads(slot.summary.read_text(encoding='utf-8'))
    summary['sessions'][0]['archivedAt'] = '2020-01-01T00:00:00.000Z'
    slot.summary.write_text(json.dumps(summary), encoding='utf-8')
    self.import_more()
    entries = read_entries(path)
    self.same(entries[-1], {
      **renamed,
      'parentId': entries[-2]['id'],
    })
    record = self.summary_of('ses_a', 0)
    self.same((record['name'], record['messageCount']), ('Renamed', 3))
    self.same(record['archivedAt'], '2020-01-01T00:00:00.000Z')
    self.same(path.stat().st_mtime_ns, self.latest_moment_ns(path))

  def test_reimport_updates_the_file_where_repair_moved_it(self) -> None:
    self.imported('ses_a', 0)
    projects = {
      'projects': [{
        'id': 'b',
        'name': 'b',
        'path': OTHER_CWD,
      }],
    }
    (self.data_dir / 'projects.json').write_text(json.dumps(projects), encoding='utf-8')
    month = self.slot(0).month_dir
    moved = self.slot(0, OTHER_CWD).month_dir
    moved.parent.mkdir(parents=True)
    month.replace(moved)
    self.same(self.run_repair()[0], 0)
    path = self.file_of('ses_a', 0, OTHER_CWD)
    self.import_more()
    self.same(list(self.tree.rglob('*_ses_a.jsonl')), [path])
    self.same(read_entries(path)[0]['cwd'], OTHER_CWD)
    self.same(self.summary_of('ses_a', 0, OTHER_CWD)['messageCount'], 3)

  def test_session_in_a_file_named_after_a_dot_is_found_by_its_id(self) -> None:
    path = self.imported('ses_a', 0)
    hidden = path.with_name(f'.{path.name}')
    path.replace(hidden)
    contents = hidden.read_bytes()
    code, output = self.run_import()
    self.same(code, 0, output)
    self.has('present: 1', output)
    self.same(list(self.tree.rglob('*.jsonl')), [hidden])
    self.same(hidden.read_bytes(), contents)

  def test_unreadable_file_of_another_session_does_not_stop_the_import(self) -> None:
    other = self.imported('ses_a', 0)
    other.chmod(0)
    self.addCleanup(other.chmod, 0o600)
    if os.access(other, os.R_OK):
      self.skipTest('the file is readable without its permission')
    self.db.session('ses_b', 100)
    self.db.exchange('ses_b', 110)
    code, output = self.run_import()
    self.same(code, 1, output)
    self.has(f'problem: cannot read {other}', output)
    self.has('imported: 1', output)
    self.same(len(list(self.slot(100).day_dir.glob('*_ses_b.jsonl'))), 1)

  def test_session_continued_in_pi_is_left_as_it_is(self) -> None:
    path = self.imported('ses_a', 0)
    entries = read_entries(path)
    reply = {
      'type': 'message',
      'id': 'pi000001',
      'parentId': entries[-1]['id'],
      'timestamp': entries[-1]['timestamp'],
      'message': {
        'role': 'assistant',
        'api': 'anthropic-messages',
        'content': [],
      },
    }
    with path.open('a', encoding='utf-8') as handle:
      handle.write(json.dumps(reply) + '\n')
    contents = path.read_bytes()
    self.db.message('ses_a', 'ses_a-u2', 30, user_data(), [text('more')])
    code, output = self.run_import()
    self.same(code, 0, output)
    self.has('present: 1', output)
    self.has('warning: ', output)
    self.same(path.read_bytes(), contents)

  def test_file_without_a_record_is_updated_where_it_lies(self) -> None:
    path = self.imported('ses_a', 0)
    self.slot(0).summary.unlink()
    stash = self.tree / self.module.project_dir_name(CWD) / 'stash'
    stash.mkdir()
    moved = stash / path.name
    path.replace(moved)
    header = read_entries(moved)[0]
    self.db.message('ses_a', 'ses_a-u2', 30, user_data(), [text('more')])
    code, output = self.run_import()
    self.same(code, 0, output)
    self.has('updated: 1', output)
    self.has('run --repair to add one', output)
    self.same(list(self.tree.rglob('*_ses_a.jsonl')), [moved])
    entries = read_entries(moved)
    self.same(entries[0], header)
    self.same(sum(entry['type'] == 'message' for entry in entries), 3)

  def test_interrupted_update_of_the_flat_archive_is_finished_by_the_next_run(
    self,
  ) -> None:
    path = self.imported('ses_a', 0)
    flat = self.data_dir / self.module.FLAT_DIR_NAME
    flat.mkdir()
    path.replace(flat / path.name)
    self.slot(0).summary.unlink()
    index_path = self.data_dir / self.module.INDEX_NAME
    index = {
      'sessions': [{
        'sessionId': 'ses_a',
        'originalPath': '/elsewhere/ses_a.jsonl',
        'messageCount': 2,
      }],
    }
    index_path.write_text(json.dumps(index), encoding='utf-8')
    self.db.message('ses_a', 'ses_a-u2', 30, user_data(), [text('more')])
    stop = OSError('stopped')
    with mock.patch.object(self.module, 'write_json', side_effect=stop):
      self.same(self.run_import()[0], 2)
    code, output = self.run_import()
    self.same(code, 0, output)
    record = json.loads(index_path.read_text(encoding='utf-8'))['sessions'][0]
    self.same((record['messageCount'], record['name']), (3, 'A real title'))


class ReimportFailureTest(ConvertTestCase):
  def test_failure_after_the_header_does_not_stop_the_import(self) -> None:
    stuck = self.imported('ses_a', 0)
    kept = self.imported('ses_b', 100)
    count = len(read_entries(kept))
    self.db.message('ses_b', 'ses_b-u2', 130, user_data(), [text('more')])
    self.db.session('ses_c', 200)
    self.db.exchange('ses_c', 210)
    contents = stuck.read_bytes()
    with broken(stuck, header=True):
      code, output = self.run_import()
    self.same(code, 1, output)
    self.has(f'problem: cannot read {stuck}', output)
    self.has('updated: 1', output)
    self.has('imported: 1', output)
    self.same(stuck.read_bytes(), contents)
    self.has(len(read_entries(kept)), range(count + 1, count + 100))
    self.same(len(list(self.slot(200).day_dir.glob('*_ses_c.jsonl'))), 1)

  def import_beside(self, bad: Path, *, known: bool) -> str:
    # The import of a new session next to a file that cannot be read.
    self.db.session('ses_b', 100)
    self.db.exchange('ses_b', 110)
    with broken(bad, header=False):
      code, output = self.run_import()
    self.has('imported: 1', output)
    self.has(f'problem: cannot read {bad}', output)
    self.same(len(list(self.slot(100).day_dir.glob('*_ses_b.jsonl'))), 1)
    if known:
      self.same(list(self.tree.rglob('*_ses_a.jsonl')), [bad])
    self.same(code, 1, output)
    return output

  def test_session_of_an_unreadable_file_away_from_its_place_is_not_imported_again(
    self,
  ) -> None:
    moved = self.stashed(self.imported('ses_a', 0))
    contents = moved.read_bytes()
    output = self.import_beside(moved, known=True)
    self.has('ses_a', output)
    self.same(moved.read_bytes(), contents)

  def test_session_of_an_unreadable_file_named_after_a_dot_is_not_imported_again(
    self,
  ) -> None:
    moved = self.stashed(self.imported('ses_a', 0), hidden=True)
    self.import_beside(moved, known=True)

  def test_unreadable_file_gives_its_session_by_the_name_without_a_record(self) -> None:
    path = self.imported('ses_a', 0)
    self.import_beside(self.stashed(path, hidden=True, keep_record=False), known=True)

  def test_session_nothing_is_known_about_is_imported_next_to_an_unreadable_file(
    self,
  ) -> None:
    other = self.tree / self.module.project_dir_name(CWD) / 'unknown.jsonl'
    other.parent.mkdir(parents=True)
    other.write_text('{"type":"session","id":"ses_x"}\n', encoding='utf-8')
    self.import_beside(other, known=False)


class RepairTestCase(ConvertTestCase):
  def setUp(self) -> None:
    super().setUp()
    self.use_agent_sessions()

  def register(self, *paths: str) -> None:
    projects = {
      'projects': [{
        'id': path,
        'name': path,
        'path': path,
      } for path in paths],
    }
    (self.data_dir / 'projects.json').write_text(json.dumps(projects), encoding='utf-8')

  def records(self, cwd: str, offset: int = 0) -> list[dict[str, Any]]:
    summary = self.slot(offset, cwd).summary
    if not summary.exists():
      return []
    data = json.loads(summary.read_text(encoding='utf-8'))
    return data['sessions']  # type: ignore[no-any-return]

  def other_project(self) -> Path:
    self.register(OTHER_CWD)
    return self.tree / self.module.project_dir_name(OTHER_CWD)

  def origin(self, cwd: str, path: Path) -> str:
    return str(self.module.default_session_dir(cwd) / path.name)

  def family(self) -> tuple[Path, Path]:
    parent = self.imported('ses_parent', 0)
    child = self.imported('ses_child', 100, 'ses_parent')
    self.same(read_entries(child)[0]['parentSession'], self.origin(CWD, parent))
    return parent, child


class RepairTest(RepairTestCase):
  # Where --repair puts a file and its record.
  def test_file_without_a_record_moved_to_another_project_gets_one(self) -> None:
    path = self.imported('ses_a', 0)
    self.slot(0).summary.unlink()
    stash = self.other_project() / 'stash'
    stash.mkdir(parents=True)
    path.replace(stash / path.name)
    code, output = self.run_repair()
    self.same(code, 0, output)
    moved = self.file_of('ses_a', 0, OTHER_CWD)
    self.same(read_entries(moved)[0]['cwd'], OTHER_CWD)
    record = self.records(OTHER_CWD)[0]
    expected = OTHER_CWD, self.origin(OTHER_CWD, moved)
    self.same((record['cwd'], record['originalPath']), expected)
    self.same(record['messageCount'], 2)

  def test_month_with_its_summary_moved_to_another_project(self) -> None:
    path = self.imported('ses_a', 0)
    mtime = path.stat().st_mtime_ns
    month = self.slot(0).month_dir
    moved = self.slot(0, OTHER_CWD).month_dir
    self.other_project()
    moved.parent.mkdir(parents=True)
    month.replace(moved)
    snapshot = {
      item: item.read_bytes()
      for item in moved.rglob('*') if item.is_file()
    }
    code, output = self.run_repair('--dry-run')
    self.same(code, 0, output)
    self.same({
      item: item.read_bytes()
      for item in moved.rglob('*') if item.is_file()
    }, snapshot)
    self.same(self.run_repair()[0], 0)
    target = self.file_of('ses_a', 0, OTHER_CWD)
    self.same(read_entries(target)[0]['cwd'], OTHER_CWD)
    self.same(target.stat().st_mtime_ns, mtime)
    record = self.records(OTHER_CWD)[0]
    expected = OTHER_CWD, self.origin(OTHER_CWD, target)
    self.same((record['cwd'], record['originalPath']), expected)

  def test_days_without_their_summary_moved_to_another_project(self) -> None:
    path = self.imported('ses_a', 0)
    archived = self.summary_of('ses_a', 0)['archivedAt']
    day = self.slot(0, OTHER_CWD).day_dir
    self.other_project()
    day.parent.mkdir(parents=True)
    path.parent.replace(day)
    code, output = self.run_repair()
    self.same(code, 0, output)
    self.same(self.records(CWD), [])
    record = self.records(OTHER_CWD)[0]
    self.same((record['cwd'], record['archivedAt']), (OTHER_CWD, archived))

  def test_file_at_any_depth_goes_back_to_its_day(self) -> None:
    path = self.imported('ses_a', 0)
    record = self.records(CWD)[0]
    deep = self.tree / self.module.project_dir_name(CWD) / 'one' / 'two'
    deep.mkdir(parents=True)
    path.replace(deep / path.name)
    self.same(self.run_repair()[0], 0)
    self.same(self.file_of('ses_a', 0), path)
    self.same(self.records(CWD), [record])

  def test_opencode_file_is_dated_by_its_file_name(self) -> None:
    path = self.imported('ses_a', 0)
    entries = read_entries(path)
    entries[0]['timestamp'] = '2001-01-01T00:00:00.000Z'
    wrong = self.tree / self.module.project_dir_name(CWD) / '2001' / '01' / '01'
    wrong.mkdir(parents=True)
    lines = ''.join(json.dumps(entry) + '\n' for entry in entries)
    (wrong / path.name).write_text(lines, encoding='utf-8')
    path.unlink()
    self.same(self.run_repair()[0], 0)
    self.same(self.file_of('ses_a', 0), path)

  def test_project_without_a_fitting_cwd_is_left_as_it_is(self) -> None:
    path = self.imported('ses_a', 0)
    stray = self.tree / 'stray--' / 'x'
    stray.mkdir(parents=True)
    path.replace(stray / path.name)
    code, output = self.run_repair()
    self.same(code, 1, output)
    self.has('stray--', output)
    self.same(list(self.tree.rglob('*_ses_a.jsonl')), [stray / path.name])
    self.same(len(self.records(CWD)), 1)

  def test_project_with_several_fitting_cwds_is_left_as_it_is(self) -> None:
    self.register('/a/b-c', '/a-b/c')
    self.imported('ses_a', 0)
    odd = self.tree / 'a-b-c--' / 'x'
    odd.mkdir(parents=True)
    path = self.file_of('ses_a', 0)
    path.replace(odd / path.name)
    code, output = self.run_repair()
    self.same(code, 1, output)
    self.has('/a-b/c', output)
    self.same(list(self.tree.rglob('*_ses_a.jsonl')), [odd / path.name])

  def test_file_without_a_header_in_a_project_is_reported(self) -> None:
    self.register(OTHER_CWD)
    empty = self.tree / self.module.project_dir_name(OTHER_CWD) / 'x' / 'empty.jsonl'
    empty.parent.mkdir(parents=True)
    empty.write_bytes(b'')
    code, output = self.run_repair()
    self.same(code, 1, output)
    self.has(f'{empty} has no session header with an id', output)
    self.same(empty.read_bytes(), b'')


class RepairHiddenAndUnreadableTest(RepairTestCase):
  def test_file_named_after_a_dot_is_placed_and_recorded(self) -> None:
    path = self.imported('ses_a', 0)
    self.slot(0).summary.unlink()
    stash = self.other_project() / 'stash'
    stash.mkdir(parents=True)
    hidden = stash / f'.{path.name}'
    path.replace(hidden)
    code, output = self.run_repair()
    self.same(code, 0, output)
    day_dir = self.slot(0, OTHER_CWD).day_dir
    self.same([item.name for item in day_dir.iterdir()], [hidden.name])
    self.same(list(stash.parent.glob('stash*')), [])
    self.same(self.records(OTHER_CWD)[0]['sessionId'], 'ses_a')

  def test_file_named_after_a_dot_is_dated_by_the_time_in_its_name(self) -> None:
    path = self.imported('ses_a', 0)
    lines = path.read_text(encoding='utf-8').splitlines()
    header = json.loads(lines[0])
    header['timestamp'] = '2025-10-20T08:00:00.000Z'
    path.write_text('\n'.join([json.dumps(header), *lines[1:]]) + '\n', encoding='utf-8')
    hidden = self.stashed(path, hidden=True, keep_record=False)
    code, output = self.run_repair()
    self.same(code, 0, output)
    self.same([item.name for item in self.slot(0).day_dir.iterdir()], [hidden.name])

  def test_leftovers_are_not_session_files(self) -> None:
    path = self.imported('ses_a', 0)
    copying = path.with_name(f'.{path.name}.0f0f.copying')
    moving = path.with_name(f'{path.name}.0f0f.moving')
    copying.write_bytes(path.read_bytes())
    moving.write_bytes(path.read_bytes())
    index = self.module.ArchiveIndex(self.tree, self.module.Summaries(dry_run=True))
    index.scan()
    self.same(list(index.files.values()), [[path]])

  def test_unreadable_file_is_reported_and_the_others_are_placed(self) -> None:
    first = self.imported('ses_a', 0)
    second = self.imported('ses_b', 100)
    stash = self.tree / self.module.project_dir_name(CWD) / 'stash'
    stash.mkdir()
    self.slot(0).summary.unlink()
    for path in (first, second):
      path.replace(stash / path.name)
    locked = stash / first.name
    locked.chmod(0)
    self.addCleanup(locked.chmod, 0o600)
    if os.access(locked, os.R_OK):
      self.skipTest('the file is readable without its permission')
    code, output = self.run_repair()
    self.same(code, 1, output)
    self.has(f'problem: cannot read {locked}', output)
    self.same([item.name for item in stash.iterdir()], [locked.name])
    self.same(read_entries(self.file_of('ses_b', 100))[0]['id'], 'ses_b')
    self.same([record['sessionId'] for record in self.records(CWD, 100)], ['ses_b'])


class RepairFailureTest(RepairTestCase):
  def test_failure_after_the_header_is_reported_and_the_others_are_placed(self) -> None:
    first = self.imported('ses_a', 0)
    second = self.imported('ses_b', 100)
    locked = self.stashed(first, keep_record=False)
    self.stashed(second)
    with broken(locked, header=True):
      code, output = self.run_repair()
    self.same(list(locked.parent.iterdir()), [locked])
    self.same(read_entries(self.file_of('ses_b', 100))[0]['id'], 'ses_b')
    self.same(self.records(CWD, 100)[0]['sessionId'], 'ses_b')
    self.has(f'problem: cannot read {locked}', output)
    self.same(code, 1, output)

  def test_failure_after_the_move_keeps_the_file_and_its_record(self) -> None:
    moved = self.stashed(self.imported('ses_a', 0))
    contents = moved.read_bytes()
    with broken(moved, header=True):
      code, output = self.run_repair()
    self.has('problem: cannot read ', output)
    self.same(self.file_of('ses_a', 0).read_bytes(), contents)
    self.same([record['sessionId'] for record in self.records(CWD)], ['ses_a'])
    self.same(code, 1, output)


class RepairLinkTest(RepairTestCase):
  # The parent links --repair keeps right.
  def test_moved_parent_takes_its_child_along(self) -> None:
    parent, child = self.family()
    day = self.slot(0, OTHER_CWD).day_dir
    self.other_project()
    day.mkdir(parents=True)
    parent.replace(day / parent.name)
    self.same(self.run_repair()[0], 0)
    origin = self.origin(OTHER_CWD, parent)
    self.same(self.records(OTHER_CWD)[0]['originalPath'], origin)
    self.same(read_entries(child)[0]['parentSession'], origin)
    self.same(read_entries(child)[0]['cwd'], CWD)
    records = self.records(CWD)
    child_record = next(item for item in records if item['sessionId'] == 'ses_child')
    self.same(child_record['parentSessionPath'], origin)

  def test_moved_child_keeps_its_parent(self) -> None:
    parent, child = self.family()
    day = self.slot(100, OTHER_CWD).day_dir
    self.other_project()
    day.mkdir(parents=True)
    child.replace(day / child.name)
    self.same(self.run_repair()[0], 0)
    moved = self.file_of('ses_child', 100, OTHER_CWD)
    self.same(read_entries(moved)[0]['parentSession'], self.origin(CWD, parent))
    self.same(read_entries(moved)[0]['cwd'], OTHER_CWD)
    self.same(self.file_of('ses_parent', 0), parent)

  def record_parent_at(self, name: str) -> str:
    slot = self.slot(0)
    summary = json.loads(slot.summary.read_text(encoding='utf-8'))
    moved = str(self.root / 'restored' / name)
    for item in summary['sessions']:
      if item['sessionId'] == 'ses_parent':
        item['originalPath'] = moved
    slot.summary.write_text(json.dumps(summary), encoding='utf-8')
    return moved

  def test_child_follows_a_parent_recorded_under_a_dot_name(self) -> None:
    parent, child = self.family()
    hidden = parent.with_name(f'.{parent.name}')
    parent.replace(hidden)
    moved = self.record_parent_at(hidden.name)
    code, output = self.run_repair()
    self.same(code, 0, output)
    self.lacks('is not found', output)
    self.same(read_entries(child)[0]['parentSession'], moved)

  def test_child_follows_a_parent_recorded_at_another_path(self) -> None:
    parent, child = self.family()
    moved = self.record_parent_at(parent.name)
    code, output = self.run_repair()
    self.same(code, 0, output)
    self.same(read_entries(child)[0]['parentSession'], moved)
    records = self.records(CWD)
    record = next(item for item in records if item['sessionId'] == 'ses_child')
    self.same(record['parentSessionPath'], moved)

  def test_parent_with_a_taken_place_stays_and_keeps_its_child(self) -> None:
    parent, child = self.family()
    stash = self.other_project() / 'stash'
    stash.mkdir(parents=True)
    parent.replace(stash / parent.name)
    taken = self.slot(0, OTHER_CWD).day_dir / parent.name
    taken.parent.mkdir(parents=True)
    taken.write_text('{"type":"session","id":"other"}\n', encoding='utf-8')
    code, output = self.run_repair()
    self.same(code, 1, output)
    self.has(f'{taken} exists', output)
    self.same(list(stash.iterdir()), [stash / parent.name])
    self.same(read_entries(child)[0]['parentSession'], self.origin(CWD, parent))

  def test_child_finds_a_parent_restored_elsewhere_despite_its_old_record(self) -> None:
    parent, child = self.family()
    restored = self.root / 'restored'
    restored.mkdir()
    other = self.imported('ses_x', 200)
    slot = self.slot(0)
    summary = json.loads(slot.summary.read_text(encoding='utf-8'))
    for item in summary['sessions']:
      if item['sessionId'] == 'ses_x':
        item['originalPath'] = str(restored / other.name)
    slot.summary.write_text(json.dumps(summary), encoding='utf-8')
    parent.replace(restored / parent.name)
    code, output = self.run_repair()
    self.same(code, 0, output)
    self.same(read_entries(child)[0]['parentSession'], str(restored / parent.name))
    ids = [item['sessionId'] for item in self.records(CWD)]
    self.lacks('ses_parent', ids)


class RepairRecoveryTest(RepairTestCase):
  # Interrupted operations, copies and --fix-mtime.
  def test_interrupted_repair_is_finished_by_the_next_run(self) -> None:
    first = self.imported('ses_a', 0)
    second = self.imported('ses_b', 100)
    stash = self.other_project() / 'stash'
    stash.mkdir(parents=True)
    for path in (first, second):
      path.replace(stash / path.name)
    real = self.module.move_file
    moves: list[Path] = []

    def stop_second(source: Path, target: Path) -> None:
      moves.append(source)
      if len(moves) > 1:
        message = 'stopped'
        raise OSError(message)
      real(source, target)

    with mock.patch.object(self.module, 'move_file', stop_second):
      self.same(self.run_repair()[0], 2)
    self.same(self.run_repair()[0], 0)
    for session_id, offset in (('ses_a', 0), ('ses_b', 100)):
      moved = self.file_of(session_id, offset, OTHER_CWD)
      self.same(read_entries(moved)[0]['cwd'], OTHER_CWD)
    self.same(len(self.records(OTHER_CWD)), 2)
    self.same(self.records(CWD), [])
    self.same(list(self.tree.rglob('.*')), [])
    self.same(self.run_repair(), (0, 'nothing to do\n'))

  def test_copies_left_by_an_interrupted_move_become_one_file(self) -> None:
    linked = self.imported('ses_a', 0)
    copied = self.imported('ses_b', 100)
    stash = self.tree / self.module.project_dir_name(CWD) / 'stash'
    stash.mkdir()
    os.link(linked, stash / linked.name)
    shutil.copy2(copied, stash / copied.name)
    code, output = self.run_repair()
    self.same(code, 0, output)
    self.same(sorted(self.tree.rglob('*.jsonl')), sorted([linked, copied]))
    self.same(len(self.records(CWD)), 2)
    self.same(self.run_repair(), (0, 'nothing to do\n'))

  def test_fix_mtime_sets_a_later_mtime_back_to_the_latest_entry(self) -> None:
    late = self.imported('ses_a', 0)
    early = self.imported('ses_b', 100)
    now = time.time_ns()
    os.utime(late, ns=(now, now))
    os.utime(early, ns=(BASE_MS * NS_PER_MS, BASE_MS * NS_PER_MS))
    self.same(self.run_repair()[0], 0)
    self.same(late.stat().st_mtime_ns, now)
    code, output = self.run_repair('--fix-mtime')
    self.same(code, 0, output)
    self.same(late.stat().st_mtime_ns, self.latest_moment_ns(late))
    self.same(early.stat().st_mtime_ns, BASE_MS * NS_PER_MS)

  def test_fix_mtime_leaves_a_file_it_cannot_date(self) -> None:
    self.imported('ses_a', 0)
    odd = self.slot(0).day_dir / 'odd.jsonl'
    header = {
      'type': 'session',
      'id': 'odd',
      'timestamp': 'bad',
      'cwd': CWD,
    }
    reply = {
      'type': 'message',
      'id': 'm1',
      'parentId': None,
      'timestamp': '2025-01-01T00:00:00.000Z',
      'message': {
        'role': 'user',
        'content': [],
      },
    }
    lines = ''.join(json.dumps(entry) + '\n' for entry in (header, reply))
    odd.write_text(lines, encoding='utf-8')
    now = time.time_ns()
    os.utime(odd, ns=(now, now))
    code, output = self.run_repair('--fix-mtime')
    self.same(code, 1, output)
    self.has(f'cannot date session file {odd}', output)
    self.same(odd.stat().st_mtime_ns, now)

  def test_fix_mtime_needs_repair(self) -> None:
    try:
      with contextlib.redirect_stderr(io.StringIO()):
        self.run_import('--fix-mtime')
    except SystemExit as error:
      self.same(error.code, 2)
    else:
      self.fail('--fix-mtime without --repair was accepted')

  def test_record_of_a_file_that_cannot_be_read_stays(self) -> None:
    path = self.imported('ses_a', 0)
    record = self.records(CWD)[0]
    path.write_bytes(b'')
    code, output = self.run_repair()
    self.same(code, 1, output)
    self.has(f'{path} has no session header with an id', output)
    self.same(self.records(CWD), [record])

  def test_record_of_a_file_named_for_it_stays(self) -> None:
    path = self.imported('ses_a', 0)
    record = self.records(CWD)[0]
    entries = read_entries(path)
    entries[0]['id'] = 'ses_new'
    lines = ''.join(json.dumps(entry) + '\n' for entry in entries)
    path.write_text(lines, encoding='utf-8')
    code, output = self.run_repair()
    self.same(code, 0, output)
    self.has(record, self.records(CWD))

  def test_record_of_a_hidden_file_named_for_it_stays(self) -> None:
    self.imported('ses_a', 0)
    slot = self.slot(0)
    hidden = slot.day_dir / '.hidden.jsonl'
    header = {
      'type': 'session',
      'id': 'ses_h',
      'timestamp': self.module.iso_ms(BASE_MS),
    }
    hidden.write_text(json.dumps(header) + '\n', encoding='utf-8')
    summary = json.loads(slot.summary.read_text(encoding='utf-8'))
    record = {
      **summary['sessions'][0],
      'sessionId': 'ses_h',
      'originalPath': f'/elsewhere/{hidden.name}',
    }
    summary['sessions'].append(record)
    slot.summary.write_text(json.dumps(summary), encoding='utf-8')
    code, output = self.run_repair()
    self.same(code, 0, output)
    self.has(record, self.records(CWD))

  def test_file_without_a_date_stays_next_to_its_original(self) -> None:
    self.imported('ses_a', 0)
    header = {
      'type': 'session',
      'id': 'odd',
      'timestamp': 'bad',
      'cwd': CWD,
    }
    slot = self.slot(0)
    odd = slot.day_dir / 'odd.jsonl'
    odd.write_text(json.dumps(header) + '\n', encoding='utf-8')
    original = self.root / 'pi' / 'odd.jsonl'
    original.parent.mkdir()
    shutil.copy2(odd, original)
    summary = json.loads(slot.summary.read_text(encoding='utf-8'))
    record = {
      **summary['sessions'][0],
      'sessionId': 'odd',
      'originalPath': str(original),
    }
    summary['sessions'].append(record)
    slot.summary.write_text(json.dumps(summary), encoding='utf-8')
    code, output = self.run_repair()
    self.same(code, 1, output)
    self.has(f'cannot date session file {odd}', output)
    self.has(odd, list(odd.parent.iterdir()))
    self.has(record, self.records(CWD))

  def test_copies_of_a_file_without_a_date_stay(self) -> None:
    self.imported('ses_a', 0)
    header = {
      'type': 'session',
      'id': 'odd',
      'timestamp': 'bad',
      'cwd': CWD,
    }
    first = self.slot(0).day_dir / 'odd.jsonl'
    first.write_text(json.dumps(header) + '\n', encoding='utf-8')
    second = first.parent.parent / 'odd.jsonl'
    shutil.copy2(first, second)
    code, output = self.run_repair()
    self.same(code, 1, output)
    self.has('cannot date session files', output)
    self.same(sorted(self.tree.rglob('odd.jsonl')), sorted([first, second]))

  def test_copies_in_two_projects_stay(self) -> None:
    path = self.imported('ses_a', 0)
    stash = self.other_project() / 'stash'
    stash.mkdir(parents=True)
    shutil.copy2(path, stash / path.name)
    code, output = self.run_repair()
    self.same(code, 1, output)
    self.has('session ses_a has several files', output)
    self.same(sorted(self.tree.rglob('*_ses_a.jsonl')), sorted([path, stash / path.name]))

  def hidden_with_record(self, *, header_id: str) -> tuple[Path, dict[str, Any]]:
    # A file named .X.jsonl in the tree, the record of X.jsonl with another session id.
    path = self.imported('ses_a', 0)
    record = self.records(CWD)[0]
    entries = read_entries(path)
    entries[0]['id'] = header_id
    hidden = path.with_name(f'.{path.name}')
    lines = ''.join(json.dumps(item) + '\n' for item in entries)
    hidden.write_text(lines, encoding='utf-8')
    path.unlink()
    return hidden, record

  def test_record_stays_for_a_readable_hidden_file_with_another_id(self) -> None:
    hidden, record = self.hidden_with_record(header_id='ses_new')
    code, output = self.run_repair()
    self.same(code, 0, output)
    self.has(f'added a record of {hidden}', output)
    self.has(record, self.records(CWD))

  def test_record_stays_for_an_unreadable_hidden_file(self) -> None:
    hidden, record = self.hidden_with_record(header_id='ses_a')
    with broken(hidden, header=False):
      code, output = self.run_repair()
    self.same(code, 1, output)
    self.has(f'problem: cannot read {hidden}', output)
    self.has(record, self.records(CWD))


class SummaryWriteFailureTest(RepairTestCase):
  # A record is in some summary at every moment, whatever write fails.
  def stored_record(self) -> dict[str, Any]:
    self.imported('ses_a', 0)
    summary = self.slot(0).summary
    data = json.loads(summary.read_text(encoding='utf-8'))
    data['sessions'][0].update({
      'archivedAt': '2020-01-01T00:00:00.000Z',
      'messageCount': 7,
    })
    summary.write_text(json.dumps(data), encoding='utf-8')
    return data['sessions'][0]  # type: ignore[no-any-return]

  def all_records(self) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for summary in sorted(self.tree.rglob(self.module.SUMMARY_NAME)):
      data = json.loads(summary.read_text(encoding='utf-8'))
      records.extend(data['sessions'])
    return records

  def repair_failing_on(self, target: Path) -> None:
    real = self.module.write_json

    def write(path: Path, data: object, *, dry_run: bool) -> None:
      if path == target:
        raise OSError(errno.ENOSPC, 'No space left on device', str(path))
      real(path, data, dry_run=dry_run)

    with mock.patch.object(self.module, 'write_json', side_effect=write):
      code, output = self.run_repair()
    self.same(code, 2, output)
    self.has('error:', output)
    self.same([item['sessionId'] for item in self.all_records()], ['ses_a'])

  def move_day_to_other_project(self) -> None:
    path = self.file_of('ses_a', 0)
    day = self.slot(0, OTHER_CWD).day_dir
    self.other_project()
    day.parent.mkdir(parents=True)
    path.parent.replace(day)

  def test_record_moved_between_projects_survives_a_failed_write(self) -> None:
    stored = self.stored_record()
    self.move_day_to_other_project()
    self.repair_failing_on(self.slot(0, OTHER_CWD).summary)
    self.same(self.run_repair()[0], 0)
    self.same(self.records(CWD), [])
    moved = self.file_of('ses_a', 0, OTHER_CWD)
    self.same(
      self.records(OTHER_CWD),
      [{
        **stored,
        'cwd': OTHER_CWD,
        'originalPath': self.origin(OTHER_CWD, moved),
      }],
    )

  def test_record_moved_between_months_survives_a_failed_write(self) -> None:
    stored = self.stored_record()
    month = self.slot(0).month_dir
    wrong = self.tree / self.module.project_dir_name(CWD) / '2001' / '01'
    wrong.parent.mkdir(parents=True)
    month.replace(wrong)
    self.repair_failing_on(self.slot(0).summary)
    self.same(self.run_repair()[0], 0)
    self.same(self.records(CWD), [stored])
    self.same(sorted(self.tree.rglob(self.module.SUMMARY_NAME)), [self.slot(0).summary])

  def test_dry_run_still_names_every_summary(self) -> None:
    self.stored_record()
    self.move_day_to_other_project()
    code, output = self.run_repair('--dry-run')
    self.same(code, 0, output)
    self.has(f'would write {self.slot(0, OTHER_CWD).summary}', output)
    self.has(f'would remove {self.slot(0).summary}', output)


class FlatReimportTest(ConvertTestCase):
  def flat_files(self) -> list[Path]:
    flat = self.data_dir / self.module.FLAT_DIR_NAME
    return sorted(flat.glob('*.jsonl'))

  def reversed_session(self) -> Path:
    self.imported('ses_a', 0)
    self.same(self.run_script('--reverse')[0], 0)
    (flat, ) = self.flat_files()
    return flat

  def index_path(self) -> Path:
    return self.data_dir / self.module.INDEX_NAME

  def import_more(self) -> str:
    self.db.message('ses_a', 'ses_a-u2', 30, user_data(), [text('more')])
    code, output = self.run_import()
    self.same(code, 0, output)
    return output

  def check_updated_in_place(self, flat: Path, count: int) -> None:
    output = self.import_more()
    self.has('updated: 1', output)
    self.lacks('imported', output)
    self.same(self.flat_files(), [flat])
    self.same(list(self.tree.rglob('*.jsonl')), [])
    self.has(len(read_entries(flat)), range(count + 1, count + 100))

  def test_renamed_flat_file_is_found_by_the_record_of_its_id(self) -> None:
    flat = self.reversed_session()
    count = len(read_entries(flat))
    renamed = flat.with_name('renamed.jsonl')
    flat.replace(renamed)
    data = json.loads(self.index_path().read_text(encoding='utf-8'))
    data['sessions'][0]['archivePath'] = str(renamed)
    self.index_path().write_text(json.dumps(data), encoding='utf-8')
    self.check_updated_in_place(renamed, count)
    record = json.loads(self.index_path().read_text(encoding='utf-8'))['sessions'][0]
    self.same((record['archivePath'], record['messageCount']), (str(renamed), 3))

  def test_flat_file_that_the_record_does_not_name_is_found_by_its_name(self) -> None:
    flat = self.reversed_session()
    count = len(read_entries(flat))
    self.index_path().write_text('{"sessions": []}', encoding='utf-8')
    self.check_updated_in_place(flat, count)

  def test_record_pointing_to_another_session_does_not_hide_the_flat_file(self) -> None:
    flat = self.reversed_session()
    count = len(read_entries(flat))
    other = flat.with_name('other.jsonl')
    other.write_text('{"type":"session","id":"ses_x","cwd":"/x"}\n', encoding='utf-8')
    data = json.loads(self.index_path().read_text(encoding='utf-8'))
    data['sessions'][0]['archivePath'] = str(other)
    self.index_path().write_text(json.dumps(data), encoding='utf-8')
    self.has('updated: 1', self.import_more())
    self.same(self.flat_files(), [flat, other])
    self.has(len(read_entries(flat)), range(count + 1, count + 100))
    self.same(list(self.tree.rglob('*.jsonl')), [])


class RepairAccessTimeTest(RepairTestCase):
  def stamped(self) -> list[tuple[Path, int, int]]:
    # The access time before and after the modification time, as a read moves it on
    # relatime; each file also gets a name its summary does not have yet.
    paths = [self.imported('ses_a', 0), self.imported('ses_b', 100)]
    stamps = []
    for path, shift in zip(paths, (-10**15, 10**15), strict=True):
      self.module.SessionFile(path).append_name(f'Name of {path.name}')
      mtime = self.latest_moment_ns(path)
      os.utime(path, ns=(mtime + shift, mtime))
      stamps.append((path, mtime + shift, mtime))
    return stamps

  def test_repair_keeps_the_access_time_of_a_moved_file_with_a_new_header(self) -> None:
    stamps = self.stamped()
    other = self.other_project() / 'stash'
    other.mkdir(parents=True)
    moved = []
    for path, atime, mtime in stamps:
      target = other / path.name
      path.replace(target)
      moved.append((target, atime, mtime))
    code, output = self.run_repair()
    self.same(code, 0, output)
    self.has('rewrote the header', output)
    self.has('took the name', output)
    for stored, atime, mtime in moved:
      path = next(self.tree.rglob(stored.name))
      status = path.stat()
      self.same((status.st_atime_ns, status.st_mtime_ns), (atime, mtime), str(path))
      self.same(read_entries(path)[0]['cwd'], OTHER_CWD)


class ManyFilesPatchTest(ToolTestCase):
  FIRST = '--- a.py\n+++ a.py\n@@ -1,1 +1,1 @@\n-x\n+y\n'
  SECOND = '--- b.md\n+++ b.md\n@@ -0,0 +1,1 @@\n+new\n'

  def part(self) -> dict[str, object]:
    files = [
      {
        'filePath': '/work/proj-a/a.py',
        'relativePath': 'a.py',
        'type': 'update',
        'patch': self.FIRST,
      },
      {
        'filePath': '/work/proj-a/b.md',
        'relativePath': 'b.md',
        'type': 'add',
        'patch': self.SECOND,
      },
    ]
    return tool(
      'apply_patch',
      {
        'patchText': TWO_FILES_PATCH,
      },
      f'{PATCH_HEAD}M a.py\nA b.md',
      metadata={
        'files': files,
      },
    )

  def test_each_patch_is_moved_to_the_result_of_its_call(self) -> None:
    part = self.part()
    (_, first), (_, second) = self.calls(part, version='1.15.13')
    self.same(first['details']['patch'], self.FIRST)
    self.same(second['details']['patch'], self.SECOND)
    entries = self.entries_of('ses_t', 0)
    results = {
      entry['message']['toolCallId']: entry['id']
      for entry in entries
      if entry['type'] == 'message' and entry['message']['role'] == 'toolResult'
    }
    customs = [
      entry['data'] for entry in entries
      if entry['type'] == 'custom' and entry['customType'] == 'opencode-import.part'
    ]
    (stored, ) = [item for item in customs if item['data']['type'] == 'tool']
    files = stored['data']['state']['metadata']['files']
    self.same([self.module.object_of(item).get('patch') for item in files], [None, None])
    moved = [item for item in stored['moved'] if item['from'][-1] == 'patch']
    patches = sorted((item['from'][-2], item['entry'], item['to']) for item in moved)
    self.same(
      patches,
      [
        (0, results['call_apply_patch-1'], ['message', 'details', 'patch']),
        (1, results['call_apply_patch-2'], ['message', 'details', 'patch']),
      ],
    )
    restored = Restored(self.module, entries)
    rebuilt = next(item for item in restored.parts if item['data']['type'] == 'tool')
    self.same(rebuilt['data'], part)


if __name__ == '__main__':
  unittest.main()
