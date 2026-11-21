#!/usr/bin/env bash
# Regression tests of the split plan scripts; run: bash tests/scripts.sh
# cspell:ignore monkeypatch setattr NOSYSTEM PYTHONPYCACHEPREFIX pathlib tcli
set -euo pipefail
scripts="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../scripts" && pwd)"
tmp="$(mktemp -d /var/tmp/split-plan-tests.XXXXXX)"
trap 'chmod -R u+w -- "${tmp}"; rm -rf -- "${tmp}"' EXIT
export HOME="${tmp}/home" PYTHONPYCACHEPREFIX="${tmp}/pyc" LC_ALL=C.UTF-8
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=Regression GIT_COMMITTER_NAME=Regression
export GIT_AUTHOR_EMAIL=regression@example.com GIT_COMMITTER_EMAIL=regression@example.com
mkdir -p -- "${HOME}"
passed=0
failed=0
fail() {
  echo "$1" >&2
  exit 1
}
run() {
  local name="$1" rc=0
  shift
  set +e
  (
    set -e
    "$@"
  ) >"${tmp}/case.log" 2>&1
  rc=$?
  set -e
  if [[ "${rc}" -eq 0 ]]; then
    printf 'ok: %s\n' "${name}"
    passed=$((passed + 1))
  else
    printf 'FAIL: %s: ' "${name}"
    tr '\n' ' ' <"${tmp}/case.log"
    printf ' (exit %s)\n' "${rc}"
    failed=$((failed + 1))
  fi
}
deps() {
  python3 "${scripts}/py-deps.py" "$@"
}
has() {
  grep -qF -- "$2" <<<"$1" || fail "no '$2' in: $1"
}
lacks() {
  if grep -qF -- "$2" <<<"$1"; then
    fail "unexpected '$2' in: $1"
  fi
}
cat >"${tmp}/mono.py" <<'EOF'
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from pathlib import Path

LIMIT = 3
SEEN: list[str] = []
COUNT = 0


def helper(path: str) -> str:
  return os.path.basename(path)


def shadow(helper: str) -> str:
  return helper * LIMIT


def remember(name: str) -> None:
  global COUNT
  COUNT += 1
  SEEN.append(helper(name))


class Worker:

  def run(self, where: Path) -> str:
    return self.step(str(where))

  def step(self, name: str) -> str:
    remember(name)
    return helper(name)


if __name__ == '__main__':
  print(Worker().run(LIMIT))
EOF
inventory() {
  local out
  out="$(deps inventory "${tmp}/mono.py")"
  has "${out}" $'helper\tdef\t12\t13\t2\t-\tos\t-'
  has "${out}" $'shadow\tdef\t16\t17\t2\tLIMIT\t-\t-'
  has "${out}" $'remember\tdef\t20\t23\t4\tSEEN,helper\t-\twrites:COUNT;mutates:SEEN'
  has "${out}" $'Path\ttyping'
  has "${out}" $'__main__\tmain'
}
check_move() {
  local out rc=0
  {
    printf '0\tbase\t%s\n' LIMIT SEEN COUNT
    printf '1\tone\t%s\n' helper shadow
    printf '1\ttwo\t%s\n' remember Worker
    printf '2\tcli\t__main__\n'
  } >"${tmp}/part.tsv"
  out="$(deps check "${tmp}/mono.py" "${tmp}/part.tsv")" || rc=$?
  [[ "${rc}" -eq 1 ]] || fail "rc ${rc}"
  has "${out}" 'error edge: two:remember (wave 1) uses one:helper (wave 1)'
  has "${out}" 'error state: two:remember changes base:COUNT by rebinding'
  has "${out}" 'note state: two:remember changes base:SEEN in place'
  has "${out}" $'part\t1\tone\t2\t4\tos'
}
check_port() {
  local out
  printf '1\tthree\tWorker.step\n' >>"${tmp}/part.tsv"
  out="$(deps check --mode port "${tmp}/mono.py" "${tmp}/part.tsv")"
  has "${out}" 'note contract: two:remember (wave 1) uses one:helper (wave 1)'
  has "${out}" 'note contract: two:Worker (wave 1) uses three:Worker.step (wave 1)'
  has "${out}" 'note contract: three:Worker.step (wave 1) uses two:remember (wave 1)'
  lacks "${out}" 'error'
}
check_errors() {
  local out rc=0
  printf '0\tother\tmissing_name\n1\tone\tWorker.nope\n1\tone\tLIMIT\n' \
    >>"${tmp}/part.tsv"
  sed -i '/\tcli\t/d' "${tmp}/part.tsv"
  out="$(deps check "${tmp}/mono.py" "${tmp}/part.tsv")" || rc=$?
  [[ "${rc}" -eq 1 ]] || fail "rc ${rc}"
  has "${out}" 'wave 0 has several parts: base, other'
  has "${out}" 'LIMIT is assigned twice'
  has "${out}" 'unknown member: Worker.nope'
  has "${out}" 'unknown: missing_name'
  has "${out}" 'missing: __main__'
}
same() {
  local out rc=0
  sed 's/return helper \* LIMIT/return helper * 2/' "${tmp}/mono.py" >"${tmp}/new.py"
  out="$(deps same "${tmp}/mono.py" "${tmp}/new.py" helper shadow absent)" || rc=$?
  [[ "${rc}" -eq 1 ]] || fail "rc ${rc}"
  has "${out}" $'same\thelper'
  has "${out}" $'differs\tshadow'
  has "${out}" $'missing-in-new\tabsent'
}
patches() {
  local out
  cat >"${tmp}/test_mono.py" <<'EOF'
from unittest import mock


def test(monkeypatch, module):
  with mock.patch.object(module, 'helper'):
    pass
  monkeypatch.setattr(module, 'remember', None)
  monkeypatch.setattr('pkg.mono.LIMIT', 1)
  mock.patch('pkg.mono.shadow')
  module.state.COUNT = 2
  mock.patch.object(dict, 'get')
EOF
  printf '1\tone\t%s\n' helper shadow remember LIMIT COUNT >"${tmp}/plain.tsv"
  out="$(deps patches --partition "${tmp}/plain.tsv" "${tmp}/mono.py" \
    "${tmp}/test_mono.py")"
  has "${out}" $'helper\tdef\tpatch.object\tone'
  has "${out}" $'remember\tdef\tsetattr\tone'
  has "${out}" $'LIMIT\tconst\tsetattr\tone'
  has "${out}" $'shadow\tdef\tpatch\tone'
  has "${out}" $'COUNT\tconst\tassign\tone'
  lacks "${out}" 'get'
}
manifest() {
  local out rc=0
  printf 'w0\t0\tpkg/__init__.py\nw0\t0\tpkg/base.py\n' >"${tmp}/own.tsv"
  printf 'one\t1\tpkg/one.py\ntwo\t1\tpkg/two/\nw2\t2\tpkg/one.py\n' >>"${tmp}/own.tsv"
  "${scripts}/scope.sh" manifest "${tmp}/own.tsv"
  printf 'four\t1\tpkg/base.py\n' >>"${tmp}/own.tsv"
  out="$("${scripts}/scope.sh" manifest "${tmp}/own.tsv")"
  has "${out}" $'handover\tw0:pkg/base.py\tfour'
  printf 'three\t1\tpkg/two/x.py\nfive\t1\tpkg/\n' >>"${tmp}/own.tsv"
  out="$("${scripts}/scope.sh" manifest "${tmp}/own.tsv")" || rc=$?
  [[ "${rc}" -eq 1 ]] || fail "rc ${rc}"
  has "${out}" $'overlap\ttwo:pkg/two/\tthree:pkg/two/x.py\twave 1'
  has "${out}" $'overlap\tfive:pkg/\tw0:pkg/__init__.py\twave 0'
  printf 'bad\t1\t../x\n' >"${tmp}/bad.tsv"
  rc=0
  "${scripts}/scope.sh" manifest "${tmp}/bad.tsv" 2>/dev/null || rc=$?
  [[ "${rc}" -eq 2 ]] || fail "rc ${rc} for a bad path"
}
branch() {
  local repo="${tmp}/repo" out rc=0
  git init -q -b master "${repo}"
  git -C "${repo}" commit -q --allow-empty -m base
  git -C "${repo}" switch -q -c part-one
  mkdir -p "${repo}/pkg/two"
  echo one >"${repo}/pkg/one.py"
  git -C "${repo}" add -A
  git -C "${repo}" commit -q -m one
  "${scripts}/scope.sh" branch "${repo}" master part-one "${tmp}/own.tsv" one
  echo x >"${repo}/pkg/two/x.py"
  git -C "${repo}" add -A
  git -C "${repo}" commit -q -m two
  out="$("${scripts}/scope.sh" branch "${repo}" master part-one "${tmp}/own.tsv" one)" ||
    rc=$?
  [[ "${rc}" -eq 1 ]] || fail "rc ${rc}"
  has "${out}" $'foreign\tpkg/two/x.py'
  rc=0
  out="$("${scripts}/scope.sh" merged "${repo}" master part-one)" || rc=$?
  [[ "${rc}" -eq 1 ]] || fail "rc ${rc} for an unmerged branch"
  has "${out}" $'unmerged\tpart-one'
  git -C "${repo}" switch -q master
  git -C "${repo}" merge -q --ff-only part-one
  "${scripts}/scope.sh" merged "${repo}" master part-one
}
snapshot() {
  local out rc=0
  out="$("${scripts}/src-snapshot.sh" "${tmp}/repo" master "${tmp}/src")"
  has "${out}" "SRC_DIR=${tmp}/src"
  [[ -f "${tmp}/src/pkg/one.py" && ! -w "${tmp}/src/pkg/one.py" ]] ||
    fail 'snapshot is not a read-only copy'
  "${scripts}/src-snapshot.sh" "${tmp}/repo" master "${tmp}/src" 2>/dev/null || rc=$?
  [[ "${rc}" -eq 2 ]] || fail "rc ${rc} for an existing output"
}
run 'inventory lists references, state and kinds' inventory
run 'check in move mode rejects edges between wave 1 parts' check_move
run 'check in port mode turns them into contracts' check_port
run 'check reports partition errors' check_errors
run 'same compares syntax trees' same
run 'patches lists patched attributes' patches
run 'manifest allows handovers and rejects overlaps' manifest
run 'branch and merged check the scope of a part' branch
run 'snapshot exports a read-only copy' snapshot
printf 'passed: %s, failed: %s\n' "${passed}" "${failed}"
[[ "${failed}" -eq 0 ]]
