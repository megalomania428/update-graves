# Regression tests of the opencode import of convert-session-archive.py.
# ruff: file-ignore[implicit-namespace-package]
# cspell:ignore Csvg executescript opencode QUJD QUJDRA

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

if TYPE_CHECKING:
  from collections.abc import Callable
  from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'convert-session-archive.py'
CWD = '/work/proj-a'
BASE_MS = 1_760_000_000_000
ENV_KEYS = (
  'PI_WEB_AGENT_DIR',
  'PI_CODING_AGENT_DIR',
  'PI_WEB_AGENT_SESSION_DIR',
  'PI_CODING_AGENT_SESSION_DIR',
  'PI_WEB_CONFIG',
  'PI_WEB_DATA_DIR',
  'PI_WEB_PROJECTS_FILE',
)


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
  # A synthetic opencode database with the tables the script reads.
  def __init__(self, path: Path) -> None:
    self.path = path
    self.parts = 0
    with contextlib.closing(sqlite3.connect(path)) as connection:
      connection.executescript(
        'CREATE TABLE session (id TEXT, parent_id TEXT, directory TEXT, title TEXT,'
        ' time_created INTEGER, time_updated INTEGER, time_archived INTEGER);'
        'CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER,'
        ' data TEXT);'
        'CREATE TABLE part (id TEXT, message_id TEXT, data TEXT);',
      )

  def _run(self, sql: str, values: tuple[object, ...]) -> None:
    with contextlib.closing(sqlite3.connect(self.path)) as connection:
      connection.execute('PRAGMA synchronous=OFF')
      connection.execute(sql, values)
      connection.commit()

  def session(self, session_id: str, offset: int, parent: str | None = None) -> None:
    moment = BASE_MS + offset
    self._run(
      'INSERT INTO session VALUES (?, ?, ?, ?, ?, ?, NULL)',
      (session_id, parent, CWD, 'A real title', moment, moment + 1),
    )

  def message(
    self,
    session_id: str,
    message_id: str,
    offset: int,
    data: dict[str, object],
    parts: list[dict[str, object]] | None = None,
  ) -> None:
    self._run(
      'INSERT INTO message VALUES (?, ?, ?, ?)',
      (message_id, session_id, BASE_MS + offset, json.dumps(data)),
    )
    for part in parts or []:
      self.parts += 1
      self._run(
        'INSERT INTO part VALUES (?, ?, ?)',
        (f'prt_{self.parts:05d}', message_id, json.dumps(part)),
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


class ConvertTestCase(unittest.TestCase):
  def setUp(self) -> None:
    self.module = load_script()
    self.module.PROGRESS_SECONDS = 1e9  # type: ignore[attr-defined]
    holder = tempfile.TemporaryDirectory()
    self.addCleanup(holder.cleanup)
    self.root = Path(holder.name)
    self.data_dir = self.root / 'data'
    self.data_dir.mkdir()
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
    environment = dict.fromkeys(ENV_KEYS, '')
    environment['PI_WEB_AGENT_DIR'] = str(self.agent)
    environment['PI_WEB_AGENT_SESSION_DIR'] = str(self.sessions)
    environment['PI_WEB_CONFIG'] = str(self.root / 'config.json')
    patcher = mock.patch.dict(os.environ, environment)
    patcher.start()
    self.addCleanup(patcher.stop)
    self.module.agent_dir.cache_clear()
    self.module.default_session_dir.cache_clear()

  def run_import(self, *extra: str) -> tuple[int, str]:
    output = io.StringIO()
    argv = ['--opencode', str(self.db.path), '--data-dir', str(self.data_dir), *extra]
    with contextlib.redirect_stdout(output):
      code = self.module.main(argv)
    return code, output.getvalue()

  def slot(self, offset: int) -> object:
    tree_root = self.data_dir / self.module.TREE_DIR_NAME
    return self.module.TreeSlot.at(tree_root, CWD, BASE_MS + offset)

  def file_of(self, session_id: str, offset: int) -> Path:
    slot = self.slot(offset)
    return next(slot.day_dir.glob(f'*_{session_id}.jsonl'))  # type: ignore[attr-defined]

  def entries_of(self, session_id: str, offset: int) -> list[dict[str, object]]:
    path = self.file_of(session_id, offset)
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]

  def summary_of(self, session_id: str, offset: int) -> dict[str, object]:
    slot = self.slot(offset)
    summary = slot.summary  # type: ignore[attr-defined]
    sessions = json.loads(summary.read_text(encoding='utf-8'))['sessions']
    return next(item for item in sessions if item['sessionId'] == session_id)


class ParentLinkTest(ConvertTestCase):
  def test_parent_file_conflict_leaves_child_without_link(self) -> None:
    self.db.session('ses_parent', 0)
    self.db.exchange('ses_parent', 10)
    self.db.session('ses_child', 100, 'ses_parent')
    self.db.exchange('ses_child', 110)
    slot = self.slot(0)
    squatter = slot.day_dir / self.module.OpencodeSession(  # type: ignore[attr-defined]
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
  def messages_of(self, session_id: str, offset: int) -> list[dict[str, object]]:
    entries = self.entries_of(session_id, offset)
    messages = [entry['message'] for entry in entries if entry['type'] == 'message']
    return messages  # type: ignore[return-value]  # noqa: RET504

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
    counts = first['input'], first['output'], first['reasoning']  # type: ignore[index]
    self.same(counts, (30, 14, 2))
    self.same(first['totalTokens'], 44)  # type: ignore[index]
    self.same(first['cost']['total'], 0.75)  # type: ignore[index]
    self.same((second['input'], second['output']), (3, 4))  # type: ignore[index]
    self.same(second['cost']['total'], 1)  # type: ignore[index]
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
    counts = reply['usage']['input'], reply['usage']['output']  # type: ignore[index]
    self.same(counts, (5, 6))
    self.same(reply['usage']['cost']['total'], 0.5)  # type: ignore[index]

  def kept_usage_only(self, data: dict[str, object]) -> dict[str, object]:
    self.db.session('ses_a', 0)
    self.db.message('ses_a', 'm1', 10, user_data(), [text('hi')])
    self.db.message('ses_a', 'm2', 11, assistant_data(**data))
    self.same(self.run_import()[0], 0)
    messages = self.messages_of('ses_a', 0)
    self.same([item['role'] for item in messages], ['user', 'assistant'])
    self.same(messages[1]['content'], [])
    self.same(self.summary_of('ses_a', 0)['messageCount'], len(messages))
    return messages[1]['usage']  # type: ignore[return-value]

  def test_assistant_with_only_cost_in_data_and_no_parts_is_kept(self) -> None:
    usage = self.kept_usage_only({
      'tokens': {
        'input': 0,
        'output': 0,
      },
      'cost': 0.5,
    })
    self.same(usage['cost']['total'], 0.5)  # type: ignore[index]
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
    self.same(usage['cost']['total'], 0)  # type: ignore[index]

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

  def compaction_entry(self) -> dict[str, object]:
    entries = self.entries_of('ses_a', 0)
    return next(entry for entry in entries if entry['type'] == 'compaction')

  def rows(self, third: dict[str, object], parts: list[dict[str, object]]) -> None:
    self.db.session('ses_a', 0)
    self.db.message('ses_a', 'm1', 10, user_data(), [text('first')])
    self.db.message('ses_a', 'm2', 11, assistant_data(), [text('answer')])
    self.db.message('ses_a', 'm3', 12, third, parts)

  def kept(self, tail: str, wanted: Callable[[dict[str, object]], bool]) -> None:
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

    def empty_reply(e: dict[str, object]) -> bool:
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


class RecordedOriginTest(ConvertTestCase):
  def restored_parent(self) -> tuple[Path, dict[str, object]]:
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
    tree_root = self.data_dir / self.module.TREE_DIR_NAME
    return list(tree_root.rglob('*_ses_parent.jsonl'))

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
    copy = self.module.default_session_dir(CWD) / original.name
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_bytes(original.read_bytes())
    code, output = self.run_import()
    self.same(code, 0, output)
    self.has('present: 1', output)
    self.has('imported: 1', output)
    self.same(self.entries_of('ses_child', 100)[0]['parentSession'], str(original))
    self.same(self.summary_of('ses_child', 100)['parentSessionPath'], str(original))

  def test_copy_in_the_session_dir_without_a_record_file_is_the_parent(self) -> None:
    original, _ = self.restored_parent()
    copy = self.module.default_session_dir(CWD) / original.name
    copy.parent.mkdir(parents=True, exist_ok=True)
    original.replace(copy)
    code, output = self.run_import()
    self.same(code, 0, output)
    self.has('present: 1', output)
    self.same(self.entries_of('ses_child', 100)[0]['parentSession'], str(copy))
    self.same(self.summary_of('ses_child', 100)['parentSessionPath'], str(copy))


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
  def blocks(self, *urls: str) -> list[dict[str, object]]:
    self.db.session('ses_a', 0)
    parts = [{
      'type': 'file',
      'mime': 'image/x',
      'url': url,
    } for url in urls]
    self.db.message('ses_a', 'm-u', 10, user_data(), parts)  # type: ignore[arg-type]
    self.same(self.run_import()[0], 0)
    entry = next(
      item for item in self.entries_of('ses_a', 0) if item['type'] == 'message'
    )
    return entry['message']['content']  # type: ignore[index]

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


if __name__ == '__main__':
  unittest.main()
