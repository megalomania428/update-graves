#!/usr/bin/env bash
# Regressions P36-P38, P41-P44, P46-P50; run: bash tests/scripts.sh
# cspell:ignore args cacheinfo chmod dirname gitlink gitmodules mktree NOSYSTEM repo
# cspell:ignore submodules subproject textconv noprefix symlink
set -euo pipefail
scripts="${SCRIPTS_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../scripts" && pwd)}"
tmp="$(mktemp -d /tmp/cs303-scripts.XXXXXX)"
trap 'rm -rf -- "${tmp}"' EXIT
export HOME="${tmp}/home" TMPDIR="${tmp}/tmp" LC_ALL=C.UTF-8
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_COUNT=0
export GIT_AUTHOR_NAME=Regression GIT_COMMITTER_NAME=Regression
export GIT_AUTHOR_EMAIL=regression@example.com GIT_COMMITTER_EMAIL=regression@example.com
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_CONFIG_PARAMETERS GIT_COMMON_DIR
mkdir -p -- "${HOME}" "${TMPDIR}"
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
requirements() {
  local tool
  for tool in bash git sha256sum grep find sort mktemp awk tr wc cmp; do
    command -v "${tool}" >/dev/null || fail "missing tool: ${tool}"
  done
  for tool in review-bundle.sh spec-files.sh precedents.sh; do
    [[ -x "${scripts}/${tool}" ]] || fail "missing executable: ${tool}"
  done
  printf 'Тесты\n' | LC_ALL=C.UTF-8 grep -qiF 'тесты' ||
    fail 'C.UTF-8 Cyrillic case folding unavailable'
}
repo() {
  tree="${tmp}/repo"
  mkdir -- "${tree}"
  git -C "${tree}" init -q -b master
}
bundle() {
  "${scripts}/review-bundle.sh" "${tree}" "${before}" "${after}" \
    "${tmp}/bundle" >/dev/null || fail 'review bundle returned nonzero'
  [[ -s "${tmp}/bundle" ]] || fail 'empty bundle'
  output="$(<"${tmp}/bundle")"
  stat="${output%%diff --git*}"
}
links() {
  local setting="$1" empty old new before after tree output stat
  repo
  empty="$(git -C "${tree}" mktree </dev/null)"
  old="$(printf 'old\n' | git -C "${tree}" commit-tree "${empty}")"
  new="$(printf 'new\n' | git -C "${tree}" commit-tree "${empty}" -p "${old}")"
  if [[ "${setting}" == modules || "${setting}" == submodule ]]; then
    printf '%s\n' '[submodule "dep"]' '  path = dep' >"${tree}/.gitmodules"
    if [[ "${setting}" == modules ]]; then
      printf '  ignore = all\n' >>"${tree}/.gitmodules"
    fi
    git -C "${tree}" add .gitmodules
  fi
  if [[ "${setting}" == config ]]; then
    git -C "${tree}" config diff.ignoreSubmodules all
  elif [[ "${setting}" == submodule ]]; then
    git -C "${tree}" config submodule.dep.path dep
    git -C "${tree}" config submodule.dep.ignore all
  fi
  git -C "${tree}" update-index --add --cacheinfo "160000,${old},dep"
  before="$(git -C "${tree}" write-tree)"
  git -C "${tree}" update-index --cacheinfo "160000,${new},dep"
  after="$(git -C "${tree}" write-tree)"
  bundle
  [[ "${stat}" == *dep* ]] || fail 'gitlink missing from stat'
  grep -qF -- "-Subproject commit ${old}" "${tmp}/bundle" || fail 'old gitlink missing'
  grep -qF -- "+Subproject commit ${new}" "${tmp}/bundle" || fail 'new gitlink missing'
}
textconv() {
  local before after tree output stat
  repo
  printf 'file.txt diff=constant\n' >"${tree}/.gitattributes"
  printf 'real-old-line\n' >"${tree}/file.txt"
  git -C "${tree}" add .
  before="$(git -C "${tree}" write-tree)"
  printf 'real-new-line\n' >"${tree}/file.txt"
  git -C "${tree}" add .
  after="$(git -C "${tree}" write-tree)"
  printf '%s\n' '#!/usr/bin/env bash' "printf 'textconv-constant\\n'" \
    >"${tmp}/converter"
  chmod 0755 "${tmp}/converter"
  git -C "${tree}" config diff.constant.textconv "${tmp}/converter"
  bundle
  [[ "${stat}" == *file.txt* ]] || fail 'file missing from stat'
  grep -qFx -- '-real-old-line' "${tmp}/bundle" || fail 'real removed line missing'
  grep -qFx -- '+real-new-line' "${tmp}/bundle" || fail 'real added line missing'
  if grep -qF 'textconv-constant' "${tmp}/bundle"; then
    fail 'textconv output leaked into patch'
  fi
}
files() {
  mkdir -- "${tmp}/files" "${tmp}/base"
  printf 'attachment\n' >"${tmp}/files/a\b.txt"
  printf 'ordinary\n' >"${tmp}/files/plain.txt"
  printf 'base\n' >"${tmp}/base/a\b.txt"
  printf 'carriage return\n' >"${tmp}/files/"$'carriage\r.txt'
  printf 'base carriage return\n' >"${tmp}/base/"$'carriage\r.txt'
}
manifest() {
  files
  "${scripts}/spec-files.sh" manifest "${tmp}/files" >"${tmp}/sums" ||
    fail 'manifest returned nonzero'
  [[ "$(wc -l <"${tmp}/sums")" -eq 3 ]] || fail 'manifest must list all three files'
  (cd "${tmp}/files" && sha256sum --check --strict "${tmp}/sums") ||
    fail 'manifest is not checkable'
}
base() {
  files
  "${scripts}/spec-files.sh" base "${tmp}/base" "${tmp}/files" \
    >"${tmp}/sums" || fail 'base returned nonzero'
  grep -qFx 'absent  plain.txt' "${tmp}/sums" || fail 'absent path missing'
  grep -v '^absent  ' "${tmp}/sums" >"${tmp}/hashes"
  [[ "$(wc -l <"${tmp}/hashes")" -eq 2 ]] || fail 'base must hash both existing files'
  (cd "${tmp}/base" && sha256sum --check --strict "${tmp}/hashes") ||
    fail 'base hashes are not checkable'
}
ordinary() {
  mkdir -- "${tmp}/files"
  printf 'ordinary\n' >"${tmp}/files/plain.txt"
  "${scripts}/spec-files.sh" manifest "${tmp}/files" >"${tmp}/sums"
  (cd "${tmp}/files" && sha256sum --check --strict "${tmp}/sums")
}
newline() {
  local mode="$1" rc=0
  local -a args=()
  files
  printf 'bad\n' >"${tmp}/files/"$'bad\nname'
  [[ "${mode}" != base ]] || args+=("${tmp}/base")
  "${scripts}/spec-files.sh" "${mode}" "${args[@]}" "${tmp}/files" \
    >"${tmp}/out" 2>"${tmp}/err" || rc=$?
  [[ "${rc}" -ne 0 ]] || fail 'newline name accepted'
  grep -q '^error:' "${tmp}/err" || fail 'newline rejection lacks error'
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
binary_attributes() {
  local setting="$1" before after tree output stat
  repo
  if [[ "${setting}" == driver ]]; then
    printf 'file.txt diff=opaque\n' >"${tree}/.gitattributes"
    git -C "${tree}" config diff.opaque.binary true
  elif [[ "${setting}" == external ]]; then
    printf 'file.txt -diff\n' >"${tmp}/attributes"
    git -C "${tree}" config core.attributesFile "${tmp}/attributes"
  else
    printf 'file.txt -diff\n' >"${tree}/.gitattributes"
  fi
  printf 'real-old-line\n' >"${tree}/file.txt"
  git -C "${tree}" add .
  before="$(git -C "${tree}" write-tree)"
  printf 'real-new-line\n' >"${tree}/file.txt"
  git -C "${tree}" add .
  after="$(git -C "${tree}" write-tree)"
  bundle
  [[ "${stat}" == *file.txt* ]] || fail 'file missing from stat'
  grep -qFx -- '-real-old-line' "${tmp}/bundle" || fail 'real removed line missing'
  grep -qFx -- '+real-new-line' "${tmp}/bundle" || fail 'real added line missing'
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
  [[ ! -s "${tmp}/control.out" && ! -s "${tmp}/control.err" ]] ||
    fail 'locale failure control produced unexpected output'
  rc=0
  PATH="${tmp}/bin:${PATH}" SPECS_REPO="${tmp}/locale-specs" \
    "${scripts}/precedents.sh" owner/repo - 'тесты' \
    >"${tmp}/out" 2>"${tmp}/err" || rc=$?
  [[ "${rc}" -eq 2 ]] || fail "locale failure returned ${rc}, expected 2"
  [[ ! -s "${tmp}/out" ]] || fail 'locale failure produced stdout'
  "${real_grep}" -q '^error:.*C.UTF-8' "${tmp}/err" ||
    fail 'locale failure lacks locale-specific error'
}
stable_bundle() {
  local setting="$1" before after tree output stat i
  repo
  for ((i = 1; i <= 31; i++)); do
    printf 'line-%02d\n' "${i}"
  done >"${tree}/file.txt"
  printf 'rename control\n' >"${tree}/old.txt"
  git -C "${tree}" add .
  before="$(git -C "${tree}" write-tree)"
  for ((i = 1; i <= 31; i++)); do
    if [[ "${i}" -eq 16 ]]; then
      printf 'changed-16\n'
    else
      printf 'line-%02d\n' "${i}"
    fi
  done >"${tree}/file.txt"
  mv -- "${tree}/old.txt" "${tree}/new.txt"
  git -C "${tree}" add -A
  after="$(git -C "${tree}" write-tree)"
  bundle
  grep -qFx -- '--- a/file.txt' "${tmp}/bundle" || fail 'old prefix missing'
  grep -qFx -- '+++ b/file.txt' "${tmp}/bundle" || fail 'new prefix missing'
  grep -qFx '@@ -6,21 +6,21 @@ line-05' "${tmp}/bundle" ||
    fail 'ten-line context missing'
  grep -qFx ' line-06' "${tmp}/bundle" || fail 'leading context missing'
  grep -qFx ' line-26' "${tmp}/bundle" || fail 'trailing context missing'
  mv -- "${tmp}/bundle" "${tmp}/reference"
  case "${setting}" in
  noprefix | mnemonicPrefix | relative)
    git -C "${tree}" config "diff.${setting}" true
    ;;
  renames) git -C "${tree}" config diff.renames false ;;
  context) git -C "${tree}" config diff.context 0 ;;
  info) printf '* -diff\n' >"${tree}/.git/info/attributes" ;;
  xdg)
    export XDG_CONFIG_HOME="${tmp}/xdg"
    mkdir -p -- "${XDG_CONFIG_HOME}/git"
    printf '* -diff\n' >"${XDG_CONFIG_HOME}/git/attributes"
    ;;
  opts) export GIT_DIFF_OPTS=--unified=0 ;;
  external)
    printf '%s\n' '#!/usr/bin/env bash' 'exit 99' >"${tmp}/external"
    chmod 0755 "${tmp}/external"
    export GIT_EXTERNAL_DIFF="${tmp}/external"
    ;;
  esac
  bundle
  cmp -s -- "${tmp}/reference" "${tmp}/bundle" || fail 'bundle depends on setting'
}
binary_content() {
  local before after tree
  repo
  printf 'old\0payload\n' >"${tree}/file.bin"
  git -C "${tree}" add .
  before="$(git -C "${tree}" write-tree)"
  printf 'new\0payload\n' >"${tree}/file.bin"
  git -C "${tree}" add .
  after="$(git -C "${tree}" write-tree)"
  "${scripts}/review-bundle.sh" "${tree}" "${before}" "${after}" \
    "${tmp}/bundle" >/dev/null || fail 'binary bundle returned nonzero'
  grep -qFx 'Binary files a/file.bin and b/file.bin differ' "${tmp}/bundle" ||
    fail 'binary summary missing'
  tr -d '\000' <"${tmp}/bundle" >"${tmp}/without-nul"
  cmp -s -- "${tmp}/bundle" "${tmp}/without-nul" || fail 'NUL in bundle'
  [[ "$(wc -c <"${tmp}/bundle")" -le 1024 ]] || fail 'binary bundle too large'
}
foreign_environment() {
  local before after tree output stat
  repo
  printf 'old\n' >"${tree}/file.txt"
  git -C "${tree}" add .
  before="$(git -C "${tree}" write-tree)"
  printf 'new\n' >"${tree}/file.txt"
  git -C "${tree}" add .
  after="$(git -C "${tree}" write-tree)"
  bundle
  mv -- "${tmp}/bundle" "${tmp}/reference"
  git init -q -b master "${tmp}/foreign"
  GIT_DIR="${tmp}/foreign/.git" GIT_WORK_TREE="${tmp}/foreign" \
    GIT_INDEX_FILE="${tmp}/foreign/.git/index" \
    GIT_COMMON_DIR="${tmp}/foreign/.git" \
    GIT_OBJECT_DIRECTORY="${tmp}/foreign/.git/objects" \
    GIT_ALTERNATE_OBJECT_DIRECTORIES="${tmp}/foreign/.git/objects" \
    GIT_CONFIG_PARAMETERS="'diff.noprefix'='true'" \
    GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=diff.context GIT_CONFIG_VALUE_0=0 \
    "${scripts}/review-bundle.sh" "${tree}" "${before}" "${after}" \
    "${tmp}/bundle" >/dev/null || fail 'foreign Git environment rejected trees'
  cmp -s -- "${tmp}/reference" "${tmp}/bundle" || fail 'Git environment leaked'
}
object_path() {
  local setting="$1" before after tree output stat destination
  repo
  printf 'old\n' >"${tree}/file.txt"
  git -C "${tree}" add .
  before="$(git -C "${tree}" write-tree)"
  printf 'new\n' >"${tree}/file.txt"
  git -C "${tree}" add .
  after="$(git -C "${tree}" write-tree)"
  bundle
  mv -- "${tmp}/bundle" "${tmp}/reference"
  if [[ "${setting}" == mixed ]]; then
    destination="${tmp}/"$'line\nquote"back\\ space\r\t\001\177'
  else
    destination="${tmp}/"$'"leading\nquote'
  fi
  mv -- "${tree}" "${destination}"
  tree="${destination}"
  bundle
  cmp -s -- "${tmp}/reference" "${tmp}/bundle" || fail 'object path changed bundle'
  rm -rf -- "${tree}"
}
output_cleanup() {
  local setting="$1" before tree real_git rc=0
  repo
  before="$(git -C "${tree}" mktree </dev/null)"
  real_git="$(command -v git)"
  mkdir -- "${tmp}/cleanup-bin"
  printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail' \
    "if [[ \"\${3:-}\" == rev-parse && \"\${5:-}\" == --git-common-dir ]]; then" \
    "  [[ -f \"\${P55_OUT}\" ]] || exit 98" \
    "  if [[ \"\${P55_SETTING}\" != own ]]; then" \
    "    mv -- \"\${P55_OUT}\" \"\${P55_OUT}.moved\"" \
    "    if [[ \"\${P55_SETTING}\" == symlink ]]; then" \
    "      ln -s -- \"\${P55_OUT}.moved\" \"\${P55_OUT}\"" \
    '    else' \
    "      printf 'foreign output\\n' >\"\${P55_OUT}\"" \
    '    fi' \
    '  fi' \
    '  exit 97' \
    'fi' \
    "exec \"\${P55_REAL_GIT}\" \"\$@\"" >"${tmp}/cleanup-bin/git"
  chmod 0755 "${tmp}/cleanup-bin/git"
  PATH="${tmp}/cleanup-bin:${PATH}" P55_REAL_GIT="${real_git}" \
    P55_OUT="${tmp}/bundle" P55_SETTING="${setting}" \
    "${scripts}/review-bundle.sh" "${tree}" "${before}" "${before}" \
    "${tmp}/bundle" >"${tmp}/out" 2>"${tmp}/err" || rc=$?
  [[ "${rc}" -ne 0 ]] || fail 'injected Git failure returned success'
  [[ ! -s "${tmp}/out" ]] || fail 'failed bundle produced stdout'
  grep -qF 'cannot locate shared object directory' "${tmp}/err" ||
    fail 'injected Git failure was not reached'
  if [[ "${setting}" == own ]]; then
    [[ ! -e "${tmp}/bundle" && ! -L "${tmp}/bundle" ]] ||
      fail 'own incomplete output was not removed'
  elif [[ "${setting}" == symlink ]]; then
    [[ -L "${tmp}/bundle" && "${tmp}/bundle" -ef "${tmp}/bundle.moved" ]] ||
      fail 'replacement symlink was not preserved'
  else
    printf 'foreign output\n' >"${tmp}/expected"
    cmp -s -- "${tmp}/expected" "${tmp}/bundle" ||
      fail 'foreign replacement output was not preserved'
  fi
  if [[ "${setting}" != own ]]; then
    grep -qF 'output replaced and left untouched' "${tmp}/err" ||
      fail 'replacement output lacks diagnostic'
  fi
}
run 'environment' requirements
if [[ "${failed}" -eq 0 ]]; then
  for setting in config modules submodule none; do
    run "P36 gitlink ${setting}" links "${setting}"
    rm -rf -- "${tmp}/repo" "${tmp}/bundle"
  done
  run 'P36 textconv' textconv
  rm -rf -- "${tmp}/repo" "${tmp}/bundle"
  run 'P37 manifest backslash and ordinary' manifest
  rm -rf -- "${tmp}/files" "${tmp}/base"
  run 'P37 base backslash and absent' base
  rm -rf -- "${tmp}/files" "${tmp}/base"
  run 'P37 ordinary control' ordinary
  rm -rf -- "${tmp}/files"
  for mode in manifest base; do
    run "P37 newline ${mode}" newline "${mode}"
    rm -rf -- "${tmp}/files" "${tmp}/base"
  done
  for locale in C POSIX C.UTF-8; do
    run "P38 ${locale} Cyrillic" precedents "${locale}" \
      'тесты' 'decisions.md:1:Тесты'
    run "P38 ${locale} ASCII" precedents "${locale}" 'tests' 'decisions.md:2:Tests'
  done
  for setting in tree driver external; do
    run "P41 binary attributes ${setting}" binary_attributes "${setting}"
    rm -rf -- "${tmp}/repo" "${tmp}/bundle"
  done
  for locale in C C.UTF-8; do
    run "P42 invalid UTF-8 ${locale}" invalid_utf8 "${locale}"
  done
  run 'P43 P44 locale failure' locale_failure
  settings=(noprefix mnemonicPrefix renames relative context info xdg opts external)
  for setting in "${settings[@]}"; do
    run "P46 stable bundle ${setting}" stable_bundle "${setting}"
    rm -rf -- "${tmp}/repo" "${tmp}/bundle"
  done
  run 'P46 binary content' binary_content
  rm -rf -- "${tmp}/repo" "${tmp}/bundle"
  run 'P49 foreign Git environment' foreign_environment
  rm -rf -- "${tmp}/repo" "${tmp}/bundle" "${tmp}/foreign"
  for setting in mixed leading; do
    run "P50 object path ${setting}" object_path "${setting}"
    rm -rf -- "${tmp}/repo" "${tmp}/bundle"
  done
  for setting in replacement symlink own; do
    run "P55 output cleanup ${setting}" output_cleanup "${setting}"
    rm -rf -- "${tmp}/repo" "${tmp}/bundle" "${tmp}/bundle.moved" \
      "${tmp}/cleanup-bin"
  done
fi
printf 'passed: %s, failed: %s\n' "${passed}" "${failed}"
[[ "${failed}" -eq 0 ]]
