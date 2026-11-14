# Инвентаризация на хосте прогонов

<!-- cspell:ignore inventory mktemp pipefail скаут скаута rsyncto sshto лэптопа лэптопе скилла -->
<!-- markdownlint-disable-file MD013 -->

Действует на этапе 1. Правила для роли `скаут`, которой поручена инвентаризация; оркестратор передаёт этот файл абсолютным путём литералом и подставляет в задание значения переменных ниже.

- Хост прогонов – `coder@coding`. На лэптопе инвентаризацию не запускать: там разрешено только точечное и быстрое. Файловые системы лэптопа и хоста прогонов не общие, поэтому файлы доставляются `rsync` во временный каталог хоста без `.git` и забираются обратно.
- Переменные задания: `TREE`, `BASE`, `SRC` – путь монолита относительно `TREE`, `LANG_KEY` – язык, `GRAMMAR` – значение `--grammar` для языка вне таблицы или пустая строка, `SCRIPTS` – абсолютный путь каталога `scripts/` скилла, `OUT_JSON` и `OUT_LOG` – абсолютные пути `inventory-<N>.json` и `inventory-<N>.log` в каталоге прогона.
- Выполнить одним вызовом bash с таймаутом инструмента не меньше 40 минут, задав в начале значения переменных задания строками `TREE=…` и так далее. Монолит берётся на `BASE`, а не из рабочего дерева, которое может отличаться. Запуск идёт в отдельной сессии `tmux`, чтобы обрыв SSH не прерывал работу, а очистка выполняется всегда, в том числе после ошибки или таймаута:

  ```bash
  set -euo pipefail
  SECONDS=0
  H=coder@coding
  # Hard limits: one ssh call 60 s + 5 s kill grace = 65 s, one rsync 300 + 5 = 305 s.
  # Worst case, from the block start: the last poll iteration begins before 1200 s and
  # takes 65 + sleep 10 = 75, so the wait ends at 1275 s; then test 65, cat 65, two rsync
  # 610 and cleanup 3 x (65 + sleep 20) = 255: 1275 + 65 + 65 + 610 + 255 = 2270 s,
  # below the 2400 s tool timeout. Steps before the wait count in SECONDS, so they only
  # shorten it.
  TS=60
  TR=300
  KS=5
  O=(-o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15
    -o ServerAliveCountMax=4)
  sshto() { timeout -k "${KS}" "${TS}" ssh "${O[@]}" "${H}" "$@"; }
  rsyncto() {
    timeout -k "${KS}" "${TR}" rsync -a --timeout=60 -e "ssh ${O[*]}" -- "$@"
  }
  sshto true
  D=''
  S=''
  cleanup() {
    local remote
    if [[ -n "${D}" ]]; then
      # S is set right after D; one command kills the session and removes the directory.
      remote="$(printf 'tmux kill-session -t %q 2>/dev/null; rm -rf -- %q' "${S}" "${D}")"
      for _ in 1 2 3; do
        if sshto "${remote}"; then
          remote=''
          break
        fi
        sleep 20
      done
      if [[ -n "${remote}" ]]; then
        printf 'leftover: ssh %s %q\n' "${H}" "${remote}" >&2
      fi
    fi
    rm -rf -- "${L}"
  }
  L="$(mktemp -d /var/tmp/split-plan-XXXXXXXX)"
  trap cleanup EXIT
  # The remote path repeats the random local one, so cleanup knows it before SSH answers.
  D="${L}"
  S="split-plan-${L##*-}"
  sshto mkdir -m 700 -- "${D}"
  printf 'local %s, remote %s, session %s\n' "${L}" "${D}" "${S}" >&2
  git -C "${TREE}" show "${BASE}:${SRC}" >"${L}/input"
  sshto mkdir -- "${D}/scripts"
  rsyncto \
    "${SCRIPTS}/inventory.sh" "${SCRIPTS}/inventory.py" "${H}:${D}/scripts/"
  rsyncto "${L}/input" "${H}:${D}/input"
  cmd="$(printf 'bash %q %q %q %q' \
    "${D}/scripts/inventory.sh" "${D}/input" "${LANG_KEY}" "${SRC}")"
  if [[ -n "${GRAMMAR}" ]]; then
    cmd+=" $(printf '%q' "${GRAMMAR}")"
  fi
  cmd+="$(printf ' >%q 2>%q; echo $? >%q' "${D}/out.json" "${D}/err.log" "${D}/rc")"
  sshto "tmux new-session -d -s ${S} $(printf '%q' "${cmd}")"
  while ((SECONDS < 1200)); do
    if sshto test -f "${D}/rc"; then
      break
    fi
    sleep 10
  done
  sshto test -f "${D}/rc" || {
    echo 'error: inventory timeout' >&2
    exit 1
  }
  rc="$(sshto cat -- "${D}/rc")"
  rsyncto "${H}:${D}/err.log" "${OUT_LOG}"
  [[ "${rc}" == 0 ]] || {
    echo "error: inventory.sh exited ${rc}" >&2
    exit 1
  }
  rsyncto "${H}:${D}/out.json" "${OUT_JSON}"
  python3 -m json.tool "${OUT_JSON}" >/dev/null
  ```

- Если в stderr блока есть строка `leftover: …`, хост не ответил и очистке: записать её в отчёт как невыполненную очистку; оркестратор записывает её в журнал, передаёт дословно следующему заданию на хосте прогонов для выполнения первой командой после проверки `ssh`, а невыполненные к концу прогона перечисляет в итоговом ответе.
- Если первая команда `ssh` завершилась ошибкой, дальше ничего не делать: записать в отчёт код и stderr и вернуть статус «хост прогонов недоступен». При любой другой ошибке `OUT_JSON` результатом не считать: записать в отчёт код, сообщение и последние строки `OUT_LOG`, если он есть.
- Скачанные образы podman на хосте не удалять – это кэш следующих прогонов. Кроме своего временного каталога и сессии `tmux` на хосте прогонов ничего не создавать и не изменять; чужие каталоги в `/var/tmp` не трогать.
- В отчёт записать вывод stderr блока, образ из `OUT_LOG` или переменной `INVENTORY_IMAGE`, число деклараций, импортов и операторов верхнего уровня, значение `syntax_errors` и отклонения от порядка выше.
