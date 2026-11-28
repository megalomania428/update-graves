#!/usr/bin/env bash
# Behavior of the planner scripts; run: bash tests/scripts.sh [--podman]
# --podman also runs inventory.sh in a container, it needs podman and network access.
# cspell:ignore args chmod dirname functools nargs pipefail setdefault
# cspell:ignore nosection nosymbol caf getattr gpgsign hasattr hashlib hexdigest pathlib
# cspell:ignore Println PYTHONDONTWRITEBYTECODE startswith tomllib
set -euo pipefail
scripts="${SCRIPTS_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../scripts" && pwd)}"
use_podman=0
if [[ "$#" -eq 1 && "$1" == --podman ]]; then
  use_podman=1
elif [[ "$#" -ne 0 ]]; then
  echo 'usage: bash tests/scripts.sh [--podman]' >&2
  exit 2
fi
tmp="$(mktemp -d /tmp/split-plan-scripts.XXXXXX)"
trap 'rm -rf -- "${tmp}"' EXIT
real_home="${HOME:-}"
# SHA256 of src/tool.py `x = 1` committed by write_tree, the source of the test plan.
source_digest="9e2\
6bf369911c45c243c684147b23fc9e1dcfcf257d299a1c632016a6fcd33f4" # DevSkim: ignore DS173237
export HOME="${tmp}/home" TMPDIR="${tmp}/tmp" LC_ALL=C.UTF-8
export PYTHONDONTWRITEBYTECODE=1
mkdir -p -- "${HOME}" "${TMPDIR}"
passed=0
failed=0
fixture=write_plan
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
requirements() {
  local tool
  for tool in bash python3 git sha256sum grep find sort xargs mktemp diff tr wc cmp \
    sed; do
    command -v "${tool}" >/dev/null || fail "missing tool: ${tool}"
  done
  for tool in precedents.sh plan-files.sh inventory.sh inventory.py validate-plan.py; do
    [[ -x "${scripts}/${tool}" ]] || fail "missing executable: ${tool}"
  done
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' ||
    fail 'python3 3.9 or newer required'
  printf 'Тесты\n' | LC_ALL=C.UTF-8 grep -qiF 'тесты' ||
    fail 'C.UTF-8 Cyrillic case folding unavailable'
}
precedents() {
  local locale="$1" word="$2" expected="$3" output
  local rel='owner/repo/20260101-01-past'
  mkdir -p -- "${tmp}/specs/${rel}"
  printf 'Тесты\nTests\n' >"${tmp}/specs/${rel}/decisions.md"
  output="$(LC_ALL="${locale}" SPECS_REPO="${tmp}/specs" \
    "${scripts}/precedents.sh" owner/repo - "${word}")" ||
    fail 'precedents returned nonzero'
  [[ "${output}" == *"${rel}"* ]] || fail 'past run missing'
  [[ "${output}" == *"${expected}"* ]] || fail 'matching line missing'
}
invalid_utf8() {
  local locale="$1" output
  local rel='owner/repo/20260101-01-past'
  mkdir -p -- "${tmp}/specs/${rel}"
  printf 'Тесты\xff\n' >"${tmp}/specs/${rel}/decisions.md"
  output="$(LC_ALL="${locale}" SPECS_REPO="${tmp}/specs" \
    "${scripts}/precedents.sh" owner/repo - 'тесты')" ||
    fail 'precedents returned nonzero'
  [[ "${output}" == *"${rel}"* ]] || fail 'past run missing'
  [[ "${output}" == *$'decisions.md:1:Тесты\xff'* ]] || fail 'matching line missing'
}
locale_failure() {
  local rc=0 real_grep
  real_grep="$(command -v grep)"
  mkdir -p -- "${tmp}/locale-specs/owner/repo/20260101-01-past"
  printf 'Тесты\n' >"${tmp}/locale-specs/owner/repo/20260101-01-past/decisions.md"
  mkdir -- "${tmp}/bin"
  printf '%s\n' '#!/usr/bin/env bash' >"${tmp}/bin/grep"
  printf 'exec env LC_ALL=C %q "$@"\n' "${real_grep}" >>"${tmp}/bin/grep"
  chmod 0755 "${tmp}/bin/grep"
  PATH="${tmp}/bin:${PATH}" LC_ALL=C.UTF-8 grep -qiF 'тесты' \
    <<<'Тесты' >"${tmp}/control.out" 2>"${tmp}/control.err" || rc=$?
  [[ "${rc}" -eq 1 ]] || fail 'locale failure simulation ineffective'
  rc=0
  PATH="${tmp}/bin:${PATH}" SPECS_REPO="${tmp}/locale-specs" \
    "${scripts}/precedents.sh" owner/repo - 'тесты' \
    >"${tmp}/out" 2>"${tmp}/err" || rc=$?
  [[ "${rc}" -eq 2 ]] || fail "locale failure returned ${rc}, expected 2"
  [[ ! -s "${tmp}/out" ]] || fail 'locale failure produced stdout'
  "${real_grep}" -q '^error:.*C.UTF-8' "${tmp}/err" ||
    fail 'locale failure lacks locale-specific error'
}
plan_dir() {
  mkdir -p -- "$1/sub"
  printf 'overview\n' >"$1/00-overview.md"
  printf 'task\n' >"$1/sub/01-a.md"
}
manifest() {
  local sums
  plan_dir "${tmp}/files"
  sums="$("${scripts}/plan-files.sh" manifest "${tmp}/files")" ||
    fail 'manifest returned nonzero'
  [[ "$(wc -l <<<"${sums}")" -eq 2 ]] || fail 'manifest must list both files'
  (cd "${tmp}/files" && sha256sum --check --strict --quiet <<<"${sums}") ||
    fail 'manifest is not checkable'
  [[ "$(head -n 1 <<<"${sums}")" == *'  00-overview.md' ]] || fail 'manifest not sorted'
}
manifest_reject() {
  local setting="$1" rc=0
  mkdir -- "${tmp}/files"
  case "${setting}" in
  link)
    printf 'x\n' >"${tmp}/target"
    ln -s -- "${tmp}/target" "${tmp}/files/link.md"
    ;;
  backslash) printf 'x\n' >"${tmp}/files/a\b.md" ;;
  newline) printf 'x\n' >"${tmp}/files/"$'bad\nname.md' ;;
  esac
  "${scripts}/plan-files.sh" manifest "${tmp}/files" >"${tmp}/out" 2>"${tmp}/err" ||
    rc=$?
  [[ "${rc}" -eq 2 ]] || fail "${setting} returned ${rc}, expected 2"
  [[ ! -s "${tmp}/out" ]] || fail "${setting} produced stdout"
  grep -q '^error:' "${tmp}/err" || fail "${setting} lacks error"
}
digest() {
  local first second expected
  plan_dir "${tmp}/files"
  first="$("${scripts}/plan-files.sh" digest "${tmp}/files")"
  expected="$("${scripts}/plan-files.sh" manifest "${tmp}/files" | sha256sum)"
  [[ "${first}" == "${expected%% *}" ]] || fail 'digest is not the manifest hash'
  [[ "${first}" =~ ^[0-9a-f]{64}$ ]] || fail 'digest is not one hash'
  printf 'changed\n' >"${tmp}/files/sub/01-a.md"
  second="$("${scripts}/plan-files.sh" digest "${tmp}/files")"
  [[ "${first}" != "${second}" ]] || fail 'digest ignores content'
}
changes() {
  local rc=0 out
  plan_dir "${tmp}/run/plan-v01"
  plan_dir "${tmp}/run/plan-v02"
  printf 'changed\n' >"${tmp}/run/plan-v02/sub/01-a.md"
  out="$("${scripts}/plan-files.sh" changes "${tmp}/run" 01 02 "${tmp}/run/c.diff")" ||
    fail 'changes returned nonzero'
  [[ "${out}" == "${tmp}/run/c.diff" ]] || fail 'changes must print the output path'
  grep -q -- $'^+++ plan-v02/sub/01-a.md\t' "${tmp}/run/c.diff" ||
    fail 'changed file missing'
  grep -qFx -- '+changed' "${tmp}/run/c.diff" || fail 'new line missing'
  "${scripts}/plan-files.sh" changes "${tmp}/run" 01 02 "${tmp}/run/c.diff" \
    >/dev/null 2>&1 || rc=$?
  [[ "${rc}" -eq 2 ]] || fail "existing output returned ${rc}, expected 2"
  grep -qFx -- '+changed' "${tmp}/run/c.diff" || fail 'existing output was replaced'
  "${scripts}/plan-files.sh" changes "${tmp}/run" 01 01 "${tmp}/run/same.diff" \
    >/dev/null || fail 'identical versions returned nonzero'
  [[ -f "${tmp}/run/same.diff" && ! -s "${tmp}/run/same.diff" ]] ||
    fail 'identical versions must give an empty diff'
}
task_doc() {
  local file="$1" files="$2" symbols="$3" title name
  name="$(basename -- "${file}" .md)"
  {
    printf '# %s\n' "${name}"
    for title in 'Волна' 'Предпосылки' 'Результат' 'Входит' 'Не входит' 'Контракт' \
      'Исходные символы' 'Тесты' 'Приёмка' 'Типовые ответы'; do
      printf '\n## %s\n\n' "${title}"
      case "${title}" in
      'Входит') printf '%s\n' "${files}" ;;
      'Исходные символы') printf '%s\n' "${symbols}" ;;
      *) printf 'text\n' ;;
      esac
    done
  } >"${file}"
}
write_tree() {
  mkdir -p -- "${tmp}/tree/src" "${tmp}/tree/docs"
  printf 'x = 1\n' >"${tmp}/tree/src/tool.py"
  printf '# Tool\n' >"${tmp}/tree/README.md"
  printf '# Guide\n' >"${tmp}/tree/docs/guide.md"
  git -C "${tmp}/tree" -c init.defaultBranch=master init --quiet
  git -C "${tmp}/tree" add src/tool.py README.md docs/guide.md
  git -C "${tmp}/tree" -c user.name=test -c user.email=test@example.com \
    -c commit.gpgsign=false commit --quiet --message source
}
write_plan() {
  local dir="${tmp}/plan" base
  if [[ ! -d "${tmp}/tree" ]]; then
    write_tree
  fi
  base="$(git -C "${tmp}/tree" rev-parse HEAD)"
  mkdir -p -- "${dir}"
  cat >"${dir}/plan.json" <<'JSON'
{
  "version": 1,
  "mode": "split",
  "source": {
    "path": "src/tool.py",
    "language": "python",
    "sha256": "@SHA256@"
  },
  "target": {"language": "python", "root": "src/tool_parts"},
  "base": "@BASE@",
  "skip": ["legacy"],
  "tasks": [
    {
      "id": "01-skeleton",
      "wave": 0,
      "depends_on": [],
      "create": [
        "src/tool_parts/__init__.py",
        "src/tool_parts/store.py",
        "src/tool_parts/util.py"
      ],
      "modify": [],
      "delete": [],
      "symbols": ["Options", "MISSING"],
      "stubs": ["src/tool_parts/store.py", "src/tool_parts/util.py"]
    },
    {
      "id": "02-store",
      "wave": 1,
      "depends_on": ["01-skeleton"],
      "create": ["tests/test_store.py"],
      "modify": ["src/tool_parts/store.py"],
      "delete": [],
      "symbols": ["put", "Options.load"],
      "stubs": []
    },
    {
      "id": "03-util",
      "wave": 1,
      "depends_on": ["01-skeleton"],
      "create": ["tests/test_util.py"],
      "modify": ["src/tool_parts/util.py"],
      "delete": [],
      "symbols": ["helper"],
      "stubs": []
    },
    {
      "id": "04-integrate",
      "wave": 2,
      "depends_on": ["01-skeleton", "02-store", "03-util"],
      "create": [],
      "modify": ["src/tool.py"],
      "delete": [],
      "symbols": ["main", "if_statement@25"],
      "stubs": []
    }
  ]
}
JSON
  sed -i -e "s/@BASE@/${base}/" -e "s/@SHA256@/${source_digest}/" "${dir}/plan.json"
  printf '%s\n' '# Overview' '01-skeleton.md 02-store.md 03-util.md 04-integrate.md' \
    >"${dir}/00-overview.md"
  task_doc "${dir}/01-skeleton.md" \
    $'- src/tool_parts/__init__.py\n- src/tool_parts/store.py\n- src/tool_parts/util.py' \
    $'- Options src/tool.py:3-10\n- MISSING src/tool.py:1-1'
  task_doc "${dir}/02-store.md" $'- src/tool_parts/store.py\n- tests/test_store.py' \
    $'- put src/tool.py:22-23\n- Options.load src/tool.py:4-6'
  task_doc "${dir}/03-util.md" $'- src/tool_parts/util.py\n- tests/test_util.py' \
    '- helper src/tool.py:12-13'
  task_doc "${dir}/04-integrate.md" '- src/tool.py' \
    $'- main src/tool.py:18-20\n- if_statement@25 src/tool.py:25-26'
  cat >"${tmp}/inventory.json" <<'JSON'
{
  "path": "src/tool.py",
  "language": "python",
  "sha256": "@SHA256@",
  "syntax_errors": false,
  "declarations": [
    {"name": "MISSING", "parent": null, "start_line": 1, "end_line": 1},
    {"name": "Options", "parent": null, "start_line": 3, "end_line": 10},
    {"name": "Options.load", "parent": "Options", "start_line": 4, "end_line": 6},
    {"name": "Options.save", "parent": "Options", "start_line": 7, "end_line": 10},
    {"name": "helper", "parent": null, "start_line": 12, "end_line": 13},
    {"name": "legacy", "parent": null, "start_line": 15, "end_line": 16},
    {"name": "main", "parent": null, "start_line": 18, "end_line": 20},
    {"name": "put", "parent": null, "start_line": 22, "end_line": 23}
  ],
  "statements": [
    {"name": "if_statement@25", "kind": "if_statement", "start_line": 25, "end_line": 26}
  ]
}
JSON
  sed -i "s/@SHA256@/${source_digest}/" "${tmp}/inventory.json"
}
write_chain() {
  local dir="${tmp}/plan" base
  write_plan
  base="$(git -C "${tmp}/tree" rev-parse HEAD)"
  rm -- "${dir}/02-store.md" "${dir}/03-util.md" "${dir}/04-integrate.md"
  cat >"${dir}/plan.json" <<'JSON'
{
  "version": 1,
  "mode": "split",
  "source": {
    "path": "src/tool.py",
    "language": "python",
    "sha256": "@SHA256@"
  },
  "target": {"language": "python", "root": "src/tool_parts"},
  "base": "@BASE@",
  "skip": ["legacy"],
  "tasks": [
    {
      "id": "01-skeleton",
      "wave": 0,
      "depends_on": [],
      "create": [
        "src/tool_parts/__init__.py",
        "src/tool_parts/store.py",
        "src/tool_parts/util.py"
      ],
      "modify": [],
      "delete": [],
      "symbols": ["Options", "MISSING"],
      "stubs": ["src/tool_parts/store.py", "src/tool_parts/util.py"]
    },
    {
      "id": "02-fixtures",
      "wave": 0,
      "depends_on": ["01-skeleton"],
      "create": ["tests/fixtures/"],
      "modify": ["src/tool_parts/__init__.py"],
      "delete": [],
      "symbols": [],
      "stubs": []
    },
    {
      "id": "03-store",
      "wave": 1,
      "depends_on": ["01-skeleton", "02-fixtures"],
      "create": ["tests/test_store.py"],
      "modify": ["src/tool_parts/store.py"],
      "delete": [],
      "symbols": ["put", "Options.load"],
      "stubs": []
    },
    {
      "id": "04-util",
      "wave": 1,
      "depends_on": ["01-skeleton", "02-fixtures"],
      "create": ["tests/test_util.py"],
      "modify": ["src/tool_parts/util.py"],
      "delete": [],
      "symbols": ["helper"],
      "stubs": []
    },
    {
      "id": "05-integrate",
      "wave": 2,
      "depends_on": ["01-skeleton", "02-fixtures", "03-store", "04-util"],
      "create": [],
      "modify": ["src/tool.py"],
      "delete": [],
      "symbols": ["main", "if_statement@25"],
      "stubs": []
    },
    {
      "id": "06-finish",
      "wave": 2,
      "depends_on": ["01-skeleton", "02-fixtures", "03-store", "04-util", "05-integrate"],
      "create": [],
      "modify": ["tests/fixtures/calls.jsonl"],
      "delete": [],
      "symbols": [],
      "stubs": []
    }
  ]
}
JSON
  sed -i -e "s/@BASE@/${base}/" -e "s/@SHA256@/${source_digest}/" "${dir}/plan.json"
  printf '%s\n' '# Overview' '01-skeleton.md 02-fixtures.md 03-store.md 04-util.md' \
    '05-integrate.md 06-finish.md' >"${dir}/00-overview.md"
  task_doc "${dir}/02-fixtures.md" $'- tests/fixtures/\n- src/tool_parts/__init__.py' ''
  task_doc "${dir}/03-store.md" $'- src/tool_parts/store.py\n- tests/test_store.py' \
    $'- put src/tool.py:22-23\n- Options.load src/tool.py:4-6'
  task_doc "${dir}/04-util.md" $'- src/tool_parts/util.py\n- tests/test_util.py' \
    '- helper src/tool.py:12-13'
  task_doc "${dir}/05-integrate.md" '- src/tool.py' \
    $'- main src/tool.py:18-20\n- if_statement@25 src/tool.py:25-26'
  task_doc "${dir}/06-finish.md" '- tests/fixtures/calls.jsonl' ''
}
edit_plan() {
  python3 - "${tmp}/plan/plan.json" "$1" <<'PY'
import json
import sys

path, code = sys.argv[1], sys.argv[2]
with open(path, encoding='utf-8') as source:
  plan = json.load(source)
tasks = {task['id']: task for task in plan['tasks']}
exec(code)
with open(path, 'w', encoding='utf-8') as target:
  json.dump(plan, target, indent=2)
PY
}
edit_inventory() {
  python3 - "${tmp}/inventory.json" "$1" <<'PY'
import json
import sys

path, code = sys.argv[1], sys.argv[2]
with open(path, encoding='utf-8') as source:
  inventory = json.load(source)
items = {item['name']: item for item in inventory['declarations']}
exec(code)
with open(path, 'w', encoding='utf-8') as target:
  json.dump(inventory, target, indent=2)
PY
}
validate() {
  "${scripts}/validate-plan.py" "${tmp}/plan" "$@" >"${tmp}/out" 2>"${tmp}/err"
}
valid_plan() {
  write_plan
  validate || fail "valid plan rejected: $(<"${tmp}/err")"
  [[ "$(<"${tmp}/out")" == 'ok: 4 tasks (waves 1/2/1)' ]] || fail 'wrong success line'
  [[ ! -s "${tmp}/err" ]] || fail 'success wrote to stderr'
  validate --inventory "${tmp}/inventory.json" --tree "${tmp}/tree" ||
    fail "valid plan rejected with inventory: $(<"${tmp}/err")"
}
valid_chain() {
  write_chain
  validate || fail "valid chain rejected: $(<"${tmp}/err")"
  [[ "$(<"${tmp}/out")" == 'ok: 6 tasks (waves 2/2/2)' ]] || fail 'wrong success line'
  validate --inventory "${tmp}/inventory.json" --tree "${tmp}/tree" ||
    fail "valid chain rejected with inventory: $(<"${tmp}/err")"
}
directory_change() {
  write_chain
  edit_plan "tasks['06-finish']['delete'] = ['$1']"
  task_doc "${tmp}/plan/06-finish.md" "- tests/fixtures/calls.jsonl"$'\n'"- $1" ''
  validate --tree "${tmp}/tree" || fail "directory change rejected: $(<"${tmp}/err")"
}
quoted_path() {
  write_chain
  edit_plan "tasks['06-finish']['modify'] = ['$1']"
  task_doc "${tmp}/plan/06-finish.md" "- \`$1\`" ''
  validate || fail "quoted path rejected: $(<"${tmp}/err")"
}
own_mention() {
  local path='tests/fixtures/calls.jsonl'
  write_chain
  task_doc "${tmp}/plan/06-finish.md" "- ${path} «${path}», *${path}*, \`${path}:1\`" ''
  validate || fail "own path before punctuation rejected: $(<"${tmp}/err")"
}
recreated_path() {
  write_chain
  edit_plan "tasks['02-fixtures']['delete'] = ['docs']; \
tasks['05-integrate']['create'] = ['docs/new.md']; \
tasks['06-finish']['modify'].append('docs/new.md')"
  task_doc "${tmp}/plan/02-fixtures.md" \
    $'- tests/fixtures/\n- src/tool_parts/__init__.py\n- docs' ''
  task_doc "${tmp}/plan/05-integrate.md" $'- src/tool.py\n- docs/new.md' \
    $'- main src/tool.py:18-20\n- if_statement@25 src/tool.py:25-26'
  task_doc "${tmp}/plan/06-finish.md" $'- tests/fixtures/calls.jsonl\n- docs/new.md' ''
  validate --tree "${tmp}/tree" || fail "recreated path rejected: $(<"${tmp}/err")"
}
early_stub_change() {
  local parts tree=()
  if [[ -n "${3:-}" ]]; then
    rm -rf -- "${tmp}/tree"
    write_tree
    mkdir -p -- "${tmp}/tree/$1"
    printf 'legacy\n' >"${tmp}/tree/$1/legacy.txt"
    git -C "${tmp}/tree" add "$1/legacy.txt"
    git -C "${tmp}/tree" -c user.name=test -c user.email=test@example.com \
      -c commit.gpgsign=false commit --quiet --amend --no-edit
    tree=(--tree "${tmp}/tree")
  fi
  write_chain
  edit_plan "tasks['01-skeleton']['create'] = ['tests/fixtures/']; \
tasks['01-skeleton']['$2'] = ['$1']; tasks['01-skeleton']['stubs'] = []; \
tasks['02-fixtures']['create'] = ['src/tool_parts/__init__.py', \
'src/tool_parts/store.py', 'src/tool_parts/util.py']; \
tasks['02-fixtures']['modify'] = []; \
tasks['02-fixtures']['stubs'] = ['src/tool_parts/store.py', 'src/tool_parts/util.py']"
  task_doc "${tmp}/plan/01-skeleton.md" $'- tests/fixtures/\n- '"$1" \
    $'- Options src/tool.py:3-10\n- MISSING src/tool.py:1-1'
  parts='- src/tool_parts/__init__.py'$'\n''- src/tool_parts/store.py'
  task_doc "${tmp}/plan/02-fixtures.md" "${parts}"$'\n- src/tool_parts/util.py' ''
  validate ${tree[@]+"${tree[@]}"} || fail "early stub change rejected: $(<"${tmp}/err")"
}
stale_source() {
  local rc=0
  write_plan
  edit_plan "plan['source']['sha256'] = 'b' * 64"
  edit_inventory "inventory['sha256'] = 'b' * 64"
  validate --inventory "${tmp}/inventory.json" --tree "${tmp}/tree" || rc=$?
  [[ "${rc}" -eq 1 ]] || fail "returned ${rc}, expected 1"
  local expected="source.sha256 does not match src/tool.py at base: ${source_digest}"
  grep -qF -- "error: ${expected}" "${tmp}/err" ||
    fail "missing source error in: $(<"${tmp}/err")"
}
invalid_plan() {
  local code="$1" expected="$2" rc=0
  shift 2
  "${fixture}"
  edit_plan "${code}"
  validate "$@" || rc=$?
  [[ "${rc}" -eq 1 ]] || fail "returned ${rc}, expected 1"
  [[ ! -s "${tmp}/out" ]] || fail 'failure wrote to stdout'
  grep -qF -- "error: ${expected}" "${tmp}/err" ||
    fail "missing error '${expected}' in: $(<"${tmp}/err")"
}
invalid_inventory() {
  local code="$1" expected="$2" rc=0
  write_plan
  edit_inventory "${code}"
  validate --inventory "${tmp}/inventory.json" --tree "${tmp}/tree" || rc=$?
  [[ "${rc}" -eq 1 ]] || fail "returned ${rc}, expected 1"
  grep -qF -- "error: ${expected}" "${tmp}/err" ||
    fail "missing error '${expected}' in: $(<"${tmp}/err")"
}
foreign_mention() {
  local code="$1" file="$2" files="$3" symbols="$4" expected="$5" rc=0
  write_chain
  edit_plan "${code}"
  task_doc "${tmp}/plan/${file}" "${files}" "${symbols}"
  validate || rc=$?
  [[ "${rc}" -eq 1 ]] || fail "returned ${rc}, expected 1"
  grep -qF -- "error: ${file}: section Входит mentions ${expected} outside" \
    "${tmp}/err" || fail "missing mention error in: $(<"${tmp}/err")"
}
plan_files() {
  local setting="$1" expected="$2" rc=0
  write_plan
  case "${setting}" in
  missing) rm -- "${tmp}/plan/03-util.md" ;;
  extra) printf 'x\n' >"${tmp}/plan/notes.txt" ;;
  empty) printf ' \n' >"${tmp}/plan/02-store.md" ;;
  outside)
    task_doc "${tmp}/plan/02-store.md" '- src/tool_parts/store.py' \
      $'- put src/tool.py:22-23\n- Options.load src/tool.py:4-6'
    printf 'tests/test_store.py\n' >>"${tmp}/plan/02-store.md"
    ;;
  nosymbol) task_doc "${tmp}/plan/03-util.md" '- src/tool_parts/util.py' '- other' ;;
  unlisted)
    task_doc "${tmp}/plan/02-store.md" \
      $'- src/tool_parts/store.py\n- tests/test_store.py\n- src/tool_parts/extra.py' \
      $'- put src/tool.py:22-23\n- Options.load src/tool.py:4-6'
    ;;
  numbered)
    task_doc "${tmp}/plan/02-store.md" \
      $'- src/tool_parts/store.py\n1. tests/test_store.py' \
      $'- put src/tool.py:22-23\n- Options.load src/tool.py:4-6'
    ;;
  tail)
    task_doc "${tmp}/plan/02-store.md" \
      $'- `src/tool_parts/store.py`, `src/tool_parts/extra.py`\n- tests/test_store.py' \
      $'- put src/tool.py:22-23\n- Options.load src/tool.py:4-6'
    ;;
  foreign)
    task_doc "${tmp}/plan/02-store.md" \
      $'- src/tool_parts/store.py\n- tests/test_store.py\n- src/tool_parts/util.py' \
      $'- put src/tool.py:22-23\n- Options.load src/tool.py:4-6'
    ;;
  dotted)
    task_doc "${tmp}/plan/02-store.md" \
      $'- src/tool_parts/store.py see ./src/tool_parts/util.py\n- tests/test_store.py' \
      $'- put src/tool.py:22-23\n- Options.load src/tool.py:4-6'
    ;;
  nosection)
    grep -vFx -- '## Тесты' "${tmp}/plan/02-store.md" >"${tmp}/doc"
    mv -- "${tmp}/doc" "${tmp}/plan/02-store.md"
    ;;
  overview) printf '# Overview\n01-skeleton.md\n' >"${tmp}/plan/00-overview.md" ;;
  broken) printf '{' >"${tmp}/plan/plan.json" ;;
  esac
  validate || rc=$?
  [[ "${rc}" -eq 1 ]] || fail "returned ${rc}, expected 1"
  grep -qF -- "error: ${expected}" "${tmp}/err" ||
    fail "missing error '${expected}' in: $(<"${tmp}/err")"
}
validate_usage() {
  local setting="$1" rc=0
  write_plan
  case "${setting}" in
  no-args) "${scripts}/validate-plan.py" >/dev/null 2>&1 || rc=$? ;;
  no-dir) "${scripts}/validate-plan.py" "${tmp}/absent" >/dev/null 2>&1 || rc=$? ;;
  bad-inventory)
    printf '{"declarations": null}\n' >"${tmp}/inventory.json"
    validate --inventory "${tmp}/inventory.json" --tree "${tmp}/tree" || rc=$?
    ;;
  no-tree) validate --inventory "${tmp}/inventory.json" || rc=$? ;;
  bad-tree)
    validate --inventory "${tmp}/inventory.json" --tree "${tmp}/absent" || rc=$?
    ;;
  esac
  [[ "${rc}" -eq 2 ]] || fail "${setting} returned ${rc}, expected 2"
}
write_source() {
  cat >"${tmp}/source.py" <<'PY'
import functools

CACHE = {}
COUNT = 0
MISSING = object()


def bump():
  global COUNT
  COUNT += 1


@functools.cache
def lookup(key):
  CACHE[key] = helper(key)
  return CACHE[key]


def helper(value):
  return value or MISSING


class Store:
  limit = COUNT

  def put(self, key):
    self.get(key)
    CACHE.setdefault(key, 1)

  def get(self, key):
    return lookup(key)


if __name__ == '__main__':
  bump()
  globals()['helper'](1)
  from importlib import import_module as load
  load('json')
  print(COUNT)
  COUNT += 1
PY
}
inventory_requirements() {
  local rc=0 output expected=$'tree-sitter==0.26.1\ntree-sitter-python==0.25.0'
  output="$("${scripts}/inventory.py" --requirements python)" ||
    fail 'requirements returned nonzero'
  [[ "${output}" == "${expected}" ]] || fail "wrong python requirements: ${output}"
  output="$("${scripts}/inventory.py" --requirements tsx)" ||
    fail 'tsx requirements returned nonzero'
  [[ "${output}" == $'tree-sitter==0.26.1\ntree-sitter-typescript==0.23.2' ]] ||
    fail "wrong tsx requirements: ${output}"
  output="$("${scripts}/inventory.py" --requirements kotlin \
    --grammar tree-sitter-kotlin==1.1.0@language)" ||
    fail 'generic requirements returned nonzero'
  [[ "${output}" == $'tree-sitter==0.26.1\ntree-sitter-kotlin==1.1.0' ]] ||
    fail "wrong generic requirements: ${output}"
  for args in 'bash' 'go --grammar tree-sitter-go==0.25.0' \
    'kotlin --grammar kotlin==1.1.0' 'Kotlin --grammar tree-sitter-kotlin==1.1.0'; do
    rc=0
    # shellcheck disable=SC2086
    "${scripts}/inventory.py" --requirements ${args} >/dev/null 2>&1 || rc=$?
    [[ "${rc}" -eq 2 ]] || fail "requirements ${args} returned ${rc}, expected 2"
  done
}
inventory_python() {
  write_source
  "${scripts}/inventory.py" --no-tree-sitter --display src/source.py \
    "${tmp}/source.py" python >"${tmp}/inventory.json" || fail 'inventory failed'
  python3 - "${tmp}/inventory.json" "${tmp}/source.py" <<'PY' || fail 'wrong inventory'
import hashlib
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
source = Path(sys.argv[2]).read_bytes()
assert data['path'] == 'src/source.py', data['path']
assert data['sha256'] == hashlib.sha256(source).hexdigest()
assert data['lines'] == source.count(b'\n'), data['lines']
assert data['declarations'] is None and data['syntax_errors'] is None
assert data['statements'] is None
dynamic = [
  {'from': '<module>', 'line': 36, 'text': 'globals()'},
  {'from': '<module>', 'line': 38, 'text': "load('json')"},
]
assert data['python']['dynamic'] == dynamic, data['python']['dynamic']
rows = {row['name']: row for row in data['python']['globals']}
assert sorted(rows) == ['CACHE', 'COUNT', 'MISSING'], sorted(rows)
assert rows['CACHE']['mutators'] == ['Store.put', 'lookup'], rows['CACHE']
assert rows['CACHE']['readers'] == ['Store.put', 'lookup'], rows['CACHE']
assert rows['COUNT']['writers'] == ['<module>', 'bump'], rows['COUNT']
assert rows['COUNT']['readers'] == ['<module>', 'Store', 'bump'], rows['COUNT']
assert rows['MISSING']['readers'] == ['helper'], rows['MISSING']
assert data['python']['decorators'] == {'lookup': ['functools.cache']}
found = {(ref['from'], tuple(ref['to']), ref['source']) for ref in data['references']}
for expected in (
  ('Store.put', ('Store.get',), 'ast'),
  ('Store.get', ('lookup',), 'ast'),
  ('lookup', ('helper',), 'ast'),
  ('<module>', ('bump',), 'ast'),
):
  assert expected in found, (expected, sorted(found))
PY
}
inventory_scopes() {
  cat >"${tmp}/scopes.py" <<'PY'
from __future__ import annotations

import importlib as il
import re
from re import compile

COUNT = 0
CACHE = {}
LIMIT = 3
REGISTRY = []
TABLE = {}
PATTERN = re.compile('x')
lookup = getattr


def bump():
  global COUNT
  COUNT += 1

  def inner():
    COUNT = 5
    CACHE = {}
    CACHE.update({})
    return COUNT

  CACHE.update({})
  return inner


@REGISTRY.append
def fetch(timeout=LIMIT):
  return timeout


def pick(items, fallback=hasattr):
  first, second = (lambda CACHE: CACHE), (lambda: CACHE.pop('x', None))
  return lookup(items, 'x'), first, second


def order():
  TABLE['k'].append(1)
  il.import_module('json')
  return [(lambda LIMIT: 0) for _ in (lambda: LIMIT,)]


def import_module(name):
  return name


def typed(value: LIMIT) -> CACHE:
  local: TABLE = value
  return local, compile('y'), import_module('local')


def shadow(il, lookup):
  from builtins import eval
  return il.import_module('x'), lookup(None, 'y'), eval('1')


try:
  pass
except ValueError as PATTERN:
  pass

try:
  import tomllib
except ImportError:
  tomllib = None
SIZES = [(BIGGEST := size) for size in range(3)]


def load():
  return tomllib, BIGGEST
PY
  "${scripts}/inventory.py" --no-tree-sitter "${tmp}/scopes.py" python \
    >"${tmp}/inventory.json" || fail 'inventory failed'
  python3 - "${tmp}/inventory.json" <<'PY' || fail 'wrong inventory'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
rows = {row['name']: row for row in data['python']['globals']}
assert rows['COUNT']['readers'] == ['bump'], rows['COUNT']
assert rows['COUNT']['writers'] == ['bump'], rows['COUNT']
assert rows['COUNT']['mutators'] == [], rows['COUNT']
assert rows['CACHE']['readers'] == ['bump', 'pick'], rows['CACHE']
assert rows['CACHE']['writers'] == [], rows['CACHE']
assert rows['CACHE']['mutators'] == ['bump', 'pick'], rows['CACHE']
assert rows['LIMIT']['readers'] == ['fetch', 'order'], rows['LIMIT']
assert rows['TABLE']['readers'] == ['order'], rows['TABLE']
assert rows['TABLE']['mutators'] == ['order'], rows['TABLE']
assert rows['REGISTRY']['mutators'] == ['fetch'], rows['REGISTRY']
assert rows['PATTERN']['writers'] == ['<module>'], rows['PATTERN']
assert rows['tomllib']['readers'] == ['load'], rows['tomllib']
assert rows['tomllib']['writers'] == ['<module>'], rows['tomllib']
assert rows['BIGGEST']['readers'] == ['load'], rows['BIGGEST']
assert 'size' not in rows, sorted(rows)
dynamic = [(row['from'], row['text']) for row in data['python']['dynamic']]
assert ('pick', "lookup(items, 'x')") in dynamic, dynamic
assert ('pick', 'hasattr') in dynamic, dynamic
assert ('order', "il.import_module('json')") in dynamic, dynamic
assert not [text for _, text in dynamic if text.startswith('re.')], dynamic
assert not [
  text for _, text in dynamic if text.startswith(('compile(', 'import_module('))
], dynamic
assert [text for name, text in dynamic if name == 'shadow'] == ["eval('1')"], dynamic
PY
}
inventory_generics() {
  cat >"${tmp}/generics.py" <<'PY'
X = []
LIMIT = 99


def first[T](z: LIMIT) -> T:
  X.append(z)
  return X


def note():
  # A local annotation is not evaluated, so it is not a read.
  local: LIMIT = 1
  return local


class Box[T]:
  def put(self, z):
    X.append(z)


def outer():
  LIMIT = 1

  def inner[T: LIMIT]():
    return T

  return inner


type Alias[T] = list[LIMIT]

match None:
  case LIMIT:
    pass

match {'x': 1}:
  case {'x': CAPTURED}:
    pass


def use():
  return CAPTURED


schema = type('Schema', (), {'Record': str})


class Holder[T: getattr(schema, 'Record')]:
  pass
PY
  if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    # Python before 3.12 has no generic syntax: the inventory must fail, not guess.
    if "${scripts}/inventory.py" --no-tree-sitter "${tmp}/generics.py" python \
      >/dev/null 2>&1; then
      fail 'inventory accepted generics on Python before 3.12'
    fi
    return
  fi
  "${scripts}/inventory.py" --no-tree-sitter "${tmp}/generics.py" python \
    >"${tmp}/inventory.json" || fail 'inventory failed'
  python3 - "${tmp}/inventory.json" <<'PY' || fail 'wrong inventory'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
rows = {row['name']: row for row in data['python']['globals']}
assert rows['X']['readers'] == ['Box.put', 'first'], rows['X']
assert rows['X']['mutators'] == ['Box.put', 'first'], rows['X']
assert rows['LIMIT']['readers'] == ['<module>', 'first'], rows['LIMIT']
assert rows['LIMIT']['writers'] == ['<module>'], rows['LIMIT']
assert rows['CAPTURED']['readers'] == ['use'], rows['CAPTURED']
assert rows['CAPTURED']['writers'] == ['<module>'], rows['CAPTURED']
assert rows['schema']['readers'] == ['Holder'], rows['schema']
dynamic = [(row['from'], row['text']) for row in data['python']['dynamic']]
calls = [name for name, text in dynamic if text.startswith('getattr')]
assert calls == ['Holder'], dynamic
PY
}
inventory_encodings() {
  local file
  printf '\xef\xbb\xbfVALUE = 1\n' >"${tmp}/bom.py"
  printf '# -*- coding: latin-1 -*-\nNAME = "caf\xe9"\n' >"${tmp}/latin.py"
  for file in bom latin; do
    "${scripts}/inventory.py" --no-tree-sitter "${tmp}/${file}.py" python \
      >"${tmp}/${file}.json" || fail "${file}.py rejected"
  done
  python3 - "${tmp}/bom.json" "${tmp}/latin.json" <<'PY' || fail 'wrong globals'
import json
import sys
from pathlib import Path

for path, name in zip(sys.argv[1:], ('VALUE', 'NAME')):
  data = json.loads(Path(path).read_text(encoding='utf-8'))
  assert [row['name'] for row in data['python']['globals']] == [name], data
PY
}
inventory_podman() {
  write_source
  HOME="${real_home}" "${scripts}/inventory.sh" "${tmp}/source.py" python src/source.py \
    >"${tmp}/inventory.json" 2>"${tmp}/inventory.log" ||
    fail "inventory.sh failed: $(tail -n 5 "${tmp}/inventory.log")"
  python3 - "${tmp}/inventory.json" <<'PY' || fail 'wrong inventory'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
assert data['syntax_errors'] is False
items = {item['name']: item for item in data['declarations']}
expected = {
  'CACHE': ('constant', None, 3, 3),
  'COUNT': ('constant', None, 4, 4),
  'MISSING': ('constant', None, 5, 5),
  'bump': ('function', None, 8, 10),
  'lookup': ('function', None, 13, 16),
  'helper': ('function', None, 19, 20),
  'Store': ('class', None, 23, 31),
  'Store.put': ('method', 'Store', 26, 28),
  'Store.get': ('method', 'Store', 30, 31),
}
assert sorted(items) == sorted(expected), sorted(items)
for name, (kind, parent, start, end) in expected.items():
  item = items[name]
  found = (item['kind'], item['parent'], item['start_line'], item['end_line'])
  assert found == (kind, parent, start, end), (name, found)
assert [item['text'] for item in data['imports']] == ['import functools']
assert [item['name'] for item in data['statements']] == ['if_statement@34']
assert [item['kind'] for item in data['statements']] == ['if_statement']
found = {(ref['from'], tuple(ref['to']), ref['source']) for ref in data['references']}
for expected_ref in (
  ('Store.put', ('Store.get',), 'tags'),
  ('<module>', ('bump',), 'tags'),
  ('lookup', ('helper',), 'tags'),
):
  assert expected_ref in found, (expected_ref, sorted(found))
assert data['python']['decorators'] == {'lookup': ['functools.cache']}
assert data['python']['dynamic'] == [
  {'from': '<module>', 'line': 36, 'text': 'globals()'},
  {'from': '<module>', 'line': 38, 'text': "load('json')"},
], data['python']['dynamic']
PY
}
language_source() {
  case "$1" in
  c)
    cat <<'SRC'
#include <stdio.h>

struct point {
  int x;
};

int main(void) {
  return printf("ok") < 0;
}
SRC
    ;;
  cpp)
    cat <<'SRC'
#include <string>

namespace app {
class Store {
  public:
  void save();
};
}

int main() {
  return 0;
}
SRC
    ;;
  go | golang)
    cat <<'SRC'
package main

import "fmt"

type Server struct{}

func (s *Server) Run() {
  fmt.Println("run")
}

func main() {
  (&Server{}).Run()
}
SRC
    ;;
  java)
    cat <<'SRC'
import java.util.List;

public class Store {
  void save(List<String> items) {
    items.clear();
  }
}
SRC
    ;;
  javascript)
    cat <<'SRC'
import fs from 'fs';

export class Store {
  save() {
    return fs.existsSync('x');
  }
}

function main() {
  new Store().save();
}
SRC
    ;;
  lua)
    cat <<'SRC'
local json = require('json')

function helper()
  return json
end

function main()
  return helper()
end
SRC
    ;;
  php)
    cat <<'SRC'
<?php

use App\Util;

class Store
{
    public function save()
    {
        return Util::run();
    }
}

function main()
{
    (new Store())->save();
}
SRC
    ;;
  ruby)
    cat <<'SRC'
require 'json'

module App
  class Store
    def save
      JSON.dump({})
    end
  end
end

def main
  App::Store.new.save
end
SRC
    ;;
  rust)
    cat <<'SRC'
use std::fmt;

#[derive(Debug)]
pub struct Server;

impl Server {
    pub fn run(&self) {}
}

fn main() {
    Server.run();
}
SRC
    ;;
  scala)
    cat <<'SRC'
import scala.util.Try

object Main {
  def main(args: Array[String]): Unit = {
    Try(1)
  }
}
SRC
    ;;
  tsx | typescript)
    cat <<'SRC'
import { readFileSync } from 'fs';

export interface Store {
  save(): void;
}

export function main(): void {
  readFileSync('x');
}
SRC
    ;;
  esac
}
inventory_language() {
  local language="$1" expected="$2"
  language_source "${language}" >"${tmp}/source.${language}"
  HOME="${real_home}" "${scripts}/inventory.sh" "${tmp}/source.${language}" \
    "${language}" "src/source.${language}" "${@:3}" >"${tmp}/inventory.json" \
    2>"${tmp}/inventory.log" ||
    fail "inventory.sh failed: $(tail -n 5 "${tmp}/inventory.log")"
  python3 - "${tmp}/inventory.json" "${expected}" <<'PY' || fail 'wrong inventory'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
expected = json.loads(sys.argv[2])
assert data['syntax_errors'] is False, data
found = sorted(
  [item['name'], item['kind'], item['parent'], item['start_line'], item['end_line']]
  for item in data['declarations'])
assert found == sorted(expected['declarations']), found
statements = [item['name'] for item in data['statements']]
assert statements == expected['statements'], statements
assert len(data['imports']) == expected['imports'], data['imports']
PY
}
run 'environment' requirements
if [[ "${failed}" -eq 0 ]]; then
  for locale in C POSIX C.UTF-8; do
    run "precedents ${locale} Cyrillic" precedents "${locale}" \
      'тесты' 'decisions.md:1:Тесты'
    run "precedents ${locale} ASCII" precedents "${locale}" 'tests' \
      'decisions.md:2:Tests'
  done
  for locale in C C.UTF-8; do
    run "precedents invalid UTF-8 ${locale}" invalid_utf8 "${locale}"
  done
  run 'precedents locale failure' locale_failure
  rm -rf -- "${tmp:?}/specs" "${tmp:?}/locale-specs" "${tmp:?}/bin"
  run 'plan-files manifest' manifest
  rm -rf -- "${tmp}/files"
  for setting in link backslash newline empty; do
    run "plan-files manifest rejects ${setting}" manifest_reject "${setting}"
    rm -rf -- "${tmp}/files" "${tmp}/target"
  done
  run 'plan-files digest' digest
  rm -rf -- "${tmp}/files"
  run 'plan-files changes' changes
  rm -rf -- "${tmp}/run"
  run 'validate-plan valid' valid_plan
  rm -rf -- "${tmp}/plan"
  invalid=(
    "tasks['03-util']['modify'] = ['src/tool_parts/store.py']"
    'src/tool_parts/store.py: shared by wave 1 tasks 02-store, 03-util'
    "tasks['02-store']['modify'].append('src/tool.py')"
    '02-store: wave 1 must not touch src/tool.py'
    "tasks['02-store']['create'].append('src/tool.py/cfg')"
    '02-store: wave 1 must not touch src/tool.py'
    "tasks['02-store']['modify'].append('src/tool.py/cfg')"
    '02-store: wave 1 must not touch src/tool.py'
    "tasks['02-store']['delete'].append('src/tool.py/cfg')"
    '02-store: wave 1 must not touch src/tool.py'
    "tasks['03-util']['create'].append('src/tool_parts/__init__.py')"
    '03-util: creates src/tool_parts/__init__.py, which wave 0 already handles'
    "tasks['03-util']['depends_on'] = ['02-store']"
    '03-util: depends_on must list 01-skeleton'
    "tasks['04-integrate']['depends_on'] = ['01-skeleton']"
    '04-integrate: depends_on must list 01-skeleton, 02-store, 03-util'
    "tasks['02-store']['create'] = ['../outside.py']"
    "02-store: create: not a normalized relative path: '../outside.py'"
    "tasks['03-util']['symbols'].append('put')"
    'symbol put has more than one owner'
    "tasks['03-util']['symbols'] = []"
    '03-util: wave 1 needs symbols'
    "tasks['04-integrate']['modify'] = []"
    'mode split: exactly one wave 2 task must change src/tool.py, got none'
    "tasks['01-skeleton']['stubs'].append('src/tool_parts/extra.py')"
    '01-skeleton: stub src/tool_parts/extra.py is not in create'
    "tasks['03-util']['modify'] = []"
    'src/tool_parts/util.py: stub is not modified by any wave 1 task'
    "tasks['02-store']['stubs'] = ['src/tool_parts/store.py']"
    '02-store: only wave 0 may list stubs'
    "plan['tasks'][1]['wave'] = 0.0"
    '02-store: wave must be 0, 1 or 2, got 0.0'
    "plan['version'] = 1.0"
    'version: must be 1, got 1.0'
    "plan['tasks'][1]['wave'] = 2"
    'tasks: waves must not decrease along the id order'
    "plan['tasks'] = [task for task in plan['tasks'] if task['wave'] != 1]"
    'tasks: need at least one task in each wave, got 1/0/1'
    "plan['tasks'][1]['id'] = '01-store'"
    'tasks: number 01 is used more than once'
    "plan['mode'] = 'port'"
    'mode port: target.language must differ from source.language'
    "plan['extra'] = 1"
    'plan.json: unknown key extra'
    "tasks['02-store']['owner'] = 'x'"
    'tasks[1]: unknown key owner'
    "plan['base'] = 'HEAD'"
    'base: must be a full lowercase commit hash'
    "plan['mode'] = ['split']"
    "mode: must be split or port, got ['split']"
    "plan['source']['sha256'] = 'HEAD'"
    'source.sha256: must be a lowercase SHA256 hex digest'
  )
  for ((i = 0; i < ${#invalid[@]}; i += 2)); do
    run "validate-plan rejects ${invalid[i]}" invalid_plan "${invalid[i]}" \
      "${invalid[i + 1]}"
    rm -rf -- "${tmp}/plan"
  done
  port_code="plan['mode'] = 'port'; plan['target']['language'] = 'rust'"
  run 'validate-plan rejects port touching source' invalid_plan "${port_code}" \
    '04-integrate: mode port must not touch src/tool.py'
  rm -rf -- "${tmp}/plan"
  run 'validate-plan valid chain' valid_chain
  rm -rf -- "${tmp}/plan"
  fixture=write_chain
  invalid=(
    "tasks['01-skeleton']['create'].append('src/tool.py/cfg')"
    '01-skeleton: wave 0 must not touch src/tool.py'
    "tasks['01-skeleton']['modify'].append('src/tool.py/cfg')"
    '01-skeleton: wave 0 must not touch src/tool.py'
    "tasks['01-skeleton']['delete'].append('src/tool.py/cfg')"
    '01-skeleton: wave 0 must not touch src/tool.py'
    "tasks['02-fixtures']['depends_on'] = []"
    '02-fixtures: depends_on must list 01-skeleton'
    "tasks['03-store']['depends_on'] = ['01-skeleton']"
    '03-store: depends_on must list 01-skeleton, 02-fixtures'
    "tasks['02-fixtures']['create'].append('src/tool_parts/extra.py'); \
tasks['02-fixtures']['stubs'] = ['src/tool_parts/extra.py']"
    'wave 0: exactly one task must list stubs, got 01-skeleton, 02-fixtures'
    "tasks['02-fixtures']['create'].append('src/tool_parts/extra.py'); \
tasks['01-skeleton']['stubs'].append('src/tool_parts/extra.py')"
    '01-skeleton: stub src/tool_parts/extra.py is not in create'
    "tasks['02-fixtures']['modify'].append('src/tool_parts/store.py')"
    '02-fixtures: changes stub src/tool_parts/store.py of 01-skeleton'
    "tasks['02-fixtures']['modify'] = []; \
tasks['02-fixtures']['delete'] = ['src/tool_parts']"
    '02-fixtures: changes stub src/tool_parts/util.py of 01-skeleton'
    "tasks['02-fixtures']['modify'].append('src/tool_parts/store.py/child.py')"
    '02-fixtures: changes stub src/tool_parts/store.py of 01-skeleton'
    "tasks['02-fixtures']['delete'].append('src/tool_parts/store.py/child.py')"
    '02-fixtures: changes stub src/tool_parts/store.py of 01-skeleton'
    "tasks['01-skeleton']['create'] = ['src/tool_parts/__init__.py', \
'src/tool_parts/util.py', 'src/tool_parts/store.py/']"
    '01-skeleton: stub src/tool_parts/store.py is not in create'
    "tasks['06-finish']['create'] = ['tests/test_store.py']"
    "06-finish: create tests/test_store.py overlaps tests/test_store.py created by \
03-store"
    "tasks['06-finish']['create'] = ['tests/fixtures/new.json']"
    "06-finish: create tests/fixtures/new.json overlaps tests/fixtures/ created by \
02-fixtures"
    "tasks['02-fixtures']['create'].append('tests/fixtures/extra.json')"
    '02-fixtures: tests/fixtures/ and tests/fixtures/extra.json overlap in the same task'
    "tasks['03-store']['create'].append('tests/test_store.py/child.py')"
    "03-store: tests/test_store.py and tests/test_store.py/child.py overlap in the same \
task"
    "tasks['06-finish']['delete'] = ['src']"
    "mode split: exactly one wave 2 task must change src/tool.py, got 05-integrate, \
06-finish"
    "tasks['06-finish']['create'] = ['tests/test_store.py/extra.py']"
    "06-finish: create tests/test_store.py/extra.py overlaps tests/test_store.py \
created by 03-store"
    "tasks['03-store']['create'].append('tests/fixtures/store.json')"
    '03-store: creates tests/fixtures/store.json, which wave 0 already handles'
    "tasks['05-integrate']['modify'] = []"
    'mode split: exactly one wave 2 task must change src/tool.py, got none'
    "tasks['06-finish']['modify'].append('src/tool.py')"
    "mode split: exactly one wave 2 task must change src/tool.py, got 05-integrate, \
06-finish"
    "plan['mode'] = 'port'; plan['target']['language'] = 'rust'; \
tasks['05-integrate']['modify'] = []; tasks['06-finish']['modify'].append('src/tool.py')"
    '06-finish: mode port must not touch src/tool.py'
    "plan['mode'] = 'port'; plan['target']['language'] = 'rust'; \
tasks['05-integrate']['modify'] = []; \
tasks['06-finish']['create'].append('src/tool.py/cfg')"
    '06-finish: mode port must not touch src/tool.py'
    "plan['mode'] = 'port'; plan['target']['language'] = 'rust'; \
tasks['05-integrate']['modify'] = []; \
tasks['06-finish']['modify'].append('src/tool.py/cfg')"
    '06-finish: mode port must not touch src/tool.py'
    "plan['mode'] = 'port'; plan['target']['language'] = 'rust'; \
tasks['05-integrate']['modify'] = []; \
tasks['06-finish']['delete'].append('src/tool.py/cfg')"
    '06-finish: mode port must not touch src/tool.py'
    "tasks['06-finish']['modify'] = ['tests/fixtures/']"
    "06-finish: modify: not a normalized relative path: 'tests/fixtures/'"
    "tasks['05-integrate']['delete'] = ['src/tool_parts/']"
    "05-integrate: delete: not a normalized relative path: 'src/tool_parts/'"
  )
  for ((i = 0; i < ${#invalid[@]}; i += 2)); do
    run "validate-plan rejects chain ${invalid[i]}" invalid_plan "${invalid[i]}" \
      "${invalid[i + 1]}"
    rm -rf -- "${tmp}/plan"
  done
  for action in modify delete; do
    run "validate-plan rejects ${action} under stub file with tree" invalid_plan \
      "tasks['02-fixtures']['${action}'].append('src/tool_parts/store.py/child.py')" \
      '02-fixtures: changes stub src/tool_parts/store.py of 01-skeleton' \
      --tree "${tmp}/tree"
    rm -rf -- "${tmp}/plan"
  done
  for action in modify delete; do
    run "validate-plan accepts ${action} of future stub directory before owner" \
      early_stub_change src/tool_parts "${action}"
    rm -rf -- "${tmp}/plan"
    run "validate-plan accepts ${action} of future stub directory at base with tree" \
      early_stub_change src/tool_parts "${action}" tree
    rm -rf -- "${tmp}/plan" "${tmp}/tree"
  done
  run 'validate-plan rejects change absent at base' invalid_plan \
    "tasks['05-integrate']['modify'].append('src/absent.py')" \
    '05-integrate: changes src/absent.py, which is not at base or in depends_on' \
    --tree "${tmp}/tree"
  rm -rf -- "${tmp}/plan"
  run 'validate-plan rejects create at base' invalid_plan \
    "tasks['02-fixtures']['create'].append('README.md')" \
    '02-fixtures: creates README.md, which is already at base' --tree "${tmp}/tree"
  rm -rf -- "${tmp}/plan"
  run 'validate-plan rejects create under file at base' invalid_plan \
    "tasks['06-finish']['create'] = ['README.md/notes.md']" \
    '06-finish: creates README.md/notes.md under file README.md at base' \
    --tree "${tmp}/tree"
  rm -rf -- "${tmp}/plan"
  run 'validate-plan rejects change under created file' invalid_plan \
    "tasks['06-finish']['modify'].append('tests/test_store.py/child.py')" \
    "06-finish: changes tests/test_store.py/child.py, which is not at base or in \
depends_on" --tree "${tmp}/tree"
  rm -rf -- "${tmp}/plan"
  run 'validate-plan rejects change of emptied directory' invalid_plan \
    "tasks['05-integrate']['delete'] = ['docs/guide.md']; \
tasks['06-finish']['modify'].append('docs')" \
    '06-finish: changes docs, which is not at base or in depends_on' --tree "${tmp}/tree"
  rm -rf -- "${tmp}/plan"
  run 'validate-plan accepts directory change at base' directory_change docs
  rm -rf -- "${tmp}/plan"
  run 'validate-plan accepts delete of created directory' directory_change src/tool_parts
  rm -rf -- "${tmp}/plan"
  run 'validate-plan accepts path created after delete' recreated_path
  rm -rf -- "${tmp}/plan"
  run 'validate-plan accepts own path before punctuation' own_mention
  rm -rf -- "${tmp}/plan"
  run 'validate-plan accepts quoted path in foreign directory' quoted_path \
    'tests/fixtures/call list.jsonl'
  rm -rf -- "${tmp}/plan"
  run 'validate-plan accepts quoted path with foreign path after a space' quoted_path \
    'tests/fixtures/calls src/tool.py'
  rm -rf -- "${tmp}/plan"
  run 'validate-plan accepts quoted path starting with foreign path' quoted_path \
    'src/tool.py notes.md'
  rm -rf -- "${tmp}/plan"
  fixture=write_plan
  store_symbols=$'- put src/tool.py:22-23\n- Options.load src/tool.py:4-6'
  run 'validate-plan rejects foreign directory' foreign_mention 'pass' 03-store.md \
    $'- src/tool_parts/store.py\n- tests/test_store.py see tests/fixtures/store.json' \
    "${store_symbols}" tests/fixtures/
  rm -rf -- "${tmp}/plan"
  run 'validate-plan rejects quoted foreign command' foreign_mention 'pass' 03-store.md \
    $'- src/tool_parts/store.py\n- tests/test_store.py runs `cat tests/fixtures/a`' \
    "${store_symbols}" tests/fixtures/
  rm -rf -- "${tmp}/plan"
  fixtures_files=$'- tests/fixtures/ runs `tests/fixtures/gen && cat src/tool.py`\n'
  run 'validate-plan rejects foreign path after own path' foreign_mention 'pass' \
    02-fixtures.md "${fixtures_files}- src/tool_parts/__init__.py" '' src/tool.py
  rm -rf -- "${tmp}/plan"
  own_path='tests/fixtures/calls.jsonl'
  for glue in ',' ';' '(' '|'; do
    run "validate-plan rejects foreign path glued to own path by ${glue}" \
      foreign_mention 'pass' 06-finish.md \
      "- ${own_path} uses ${own_path}${glue}tests/fixtures/x" '' tests/fixtures/
    rm -rf -- "${tmp}/plan"
  done
  run 'validate-plan rejects backslash foreign path' foreign_mention 'pass' 03-store.md \
    $'- src/tool_parts/store.py\n- tests/test_store.py uses tests\\fixtures\\a.' \
    "${store_symbols}" tests/fixtures/
  rm -rf -- "${tmp}/plan"
  run 'validate-plan rejects foreign directory before a period' foreign_mention 'pass' \
    03-store.md $'- src/tool_parts/store.py\n- tests/test_store.py uses tests/fixtures.' \
    "${store_symbols}" tests/fixtures/
  rm -rf -- "${tmp}/plan"
  run 'validate-plan rejects path under foreign entry' foreign_mention \
    "tasks['06-finish']['delete'] = ['docs']" 05-integrate.md \
    '- src/tool.py see docs/guide.md' \
    $'- main src/tool.py:18-20\n- if_statement@25 src/tool.py:25-26' docs
  rm -rf -- "${tmp}/plan"
  run 'validate-plan rejects unowned declaration' invalid_plan \
    "tasks['04-integrate']['symbols'] = []" 'declaration main has no owner' \
    --inventory "${tmp}/inventory.json" --tree "${tmp}/tree"
  rm -rf -- "${tmp}/plan"
  run 'validate-plan rejects unknown symbol' invalid_plan \
    "plan['skip'].append('ghost')" \
    'symbol ghost is not a declaration or statement of the inventory' \
    --inventory "${tmp}/inventory.json" --tree "${tmp}/tree"
  rm -rf -- "${tmp}/plan"
  run 'validate-plan rejects foreign inventory' invalid_plan \
    "plan['source']['path'] = 'src/other.py'" \
    "inventory path 'src/tool.py' is not src/other.py" \
    --inventory "${tmp}/inventory.json" --tree "${tmp}/tree"
  rm -rf -- "${tmp}/plan"
  run 'validate-plan rejects source of another revision' invalid_plan \
    "plan['source']['sha256'] = 'b' * 64" \
    "inventory sha256 does not match source.sha256: '${source_digest}'" \
    --inventory "${tmp}/inventory.json" --tree "${tmp}/tree"
  rm -rf -- "${tmp}/plan"
  run 'validate-plan rejects source stale at base' stale_source
  rm -rf -- "${tmp}/plan"
  invalid=(
    "inventory['syntax_errors'] = True"
    'inventory has syntax errors or none recorded, use a manual inventory'
    "inventory['statements'].append({'name': 'expression_statement@28', \
'kind': 'expression_statement', 'start_line': 28, 'end_line': 28})"
    'declaration expression_statement@28 has no owner'
    "items['helper']['end_line'] = 14"
    '03-util.md: section Исходные символы lacks range src/tool.py:12-14'
    "inventory['declarations'].append({'name': 'helper', 'parent': None, \
'start_line': 30, 'end_line': 31})"
    '03-util.md: section Исходные символы lacks range src/tool.py:30-31'
    "inventory['declarations'].append({'name': 'Ext.run', 'parent': 'Ext', \
'start_line': 28, 'end_line': 29})"
    'declaration Ext.run has no owner'
    "items['put'].update(start_line=18, end_line=20)"
    'declarations main, put share src/tool.py:18-20 and need one owner'
  )
  for ((i = 0; i < ${#invalid[@]}; i += 2)); do
    run "validate-plan rejects inventory ${invalid[i]}" invalid_inventory \
      "${invalid[i]}" "${invalid[i + 1]}"
    rm -rf -- "${tmp}/plan"
  done
  for item in 'missing|03-util.md: missing' 'extra|notes.txt: unexpected file' \
    'empty|02-store.md: empty' \
    'outside|02-store.md: section Входит does not list tests/test_store.py' \
    'numbered|02-store.md: section Входит item is not - path: 1. tests/test_store.py' \
    'tail|02-store.md: section Входит item is not - path: - ' \
    'nosymbol|03-util.md: section Исходные символы does not mention helper' \
    'foreign|02-store.md: section Входит mentions src/tool_parts/util.py outside' \
    'dotted|02-store.md: section Входит mentions src/tool_parts/util.py outside' \
    'unlisted|02-store.md: section Входит lists src/tool_parts/extra.py outside' \
    'nosection|02-store.md: sections must be' \
    'overview|00-overview.md: does not mention 02-store.md' 'broken|plan.json:'; do
    run "validate-plan rejects files ${item%%|*}" plan_files "${item%%|*}" "${item#*|}"
    rm -rf -- "${tmp}/plan"
  done
  for setting in no-args no-dir bad-inventory no-tree bad-tree; do
    run "validate-plan usage ${setting}" validate_usage "${setting}"
    rm -rf -- "${tmp}/plan"
  done
  run 'inventory requirements' inventory_requirements
  run 'inventory python layer' inventory_python
  run 'inventory scopes' inventory_scopes
  run 'inventory generics' inventory_generics
  run 'inventory encodings' inventory_encodings
  languages=(
    c '[["point", "class", null, 3, 5], ["main", "function", null, 7, 9]]' '[]' 1
    cpp '[["Store", "class", null, 4, 7], ["Store.save", "method", "Store", 6, 6],
      ["main", "function", null, 10, 12]]' '[]' 1
    go '[["Server", "type", null, 5, 5], ["Server.Run", "method", "Server", 7, 9],
      ["main", "function", null, 11, 13]]' '[]' 2
    java '[["Store", "class", null, 3, 7],
      ["Store.save", "method", "Store", 4, 6]]' '[]' 1
    javascript '[["Store", "class", null, 3, 7], ["Store.save", "method", "Store", 4, 6],
      ["main", "function", null, 9, 11]]' '[]' 1
    lua '[["helper", "function", null, 3, 5], ["main", "function", null, 7, 9]]'
    '["variable_declaration@1"]' 0
    php '[["Store", "class", null, 5, 11], ["Store.save", "method", "Store", 7, 10],
      ["main", "function", null, 13, 16]]' '[]' 1
    ruby '[["App", "module", null, 3, 9], ["App.Store", "class", "App", 4, 8],
      ["main", "method", null, 11, 13]]' '["call@1"]' 0
    rust '[["Server", "class", null, 3, 4], ["Server.run", "method", "Server", 7, 7],
      ["main", "function", null, 10, 12]]' '[]' 1
    scala '[["Main", "object", null, 3, 7], ["Main.main", "method", "Main", 4, 6]]' '[]' 1
    tsx '[["Store", "interface", null, 3, 5], ["Store.save", "method", "Store", 4, 4]]'
    '["export_statement@7"]' 1
    typescript '[["Store", "interface", null, 3, 5],
      ["Store.save", "method", "Store", 4, 4]]' '["export_statement@7"]' 1
  )
  if [[ "${use_podman}" -eq 1 ]]; then
    run 'inventory.sh in podman' inventory_podman
    for ((i = 0; i < ${#languages[@]}; i += 4)); do
      expected="$(printf '{"declarations": %s, "statements": %s, "imports": %s}' \
        "${languages[i + 1]}" "${languages[i + 2]}" "${languages[i + 3]}")"
      run "inventory.sh in podman for ${languages[i]}" inventory_language \
        "${languages[i]}" "${expected}"
    done
    run 'inventory.sh in podman with --grammar' inventory_language golang \
      '{"declarations": [["Server", "type", null, 5, 5], ["Run", "method", null, 7, 9],
      ["main", "function", null, 11, 13]],
      "statements": ["package_clause@1", "import_declaration@3"], "imports": 0}' \
      tree-sitter-go==0.25.0
  else
    printf 'skipped: inventory.sh in podman (run with --podman)\n'
  fi
fi
printf 'passed: %s, failed: %s\n' "${passed}" "${failed}"
[[ "${failed}" -eq 0 ]]
