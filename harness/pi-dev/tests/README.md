<!-- cspell:ignore верхнеуровневый неизменённой немокированный рефакторинга эмитит -->

# OAuth fingerprint для compact и tree

## Область действия

Проверенный baseline – установленный `pi-coding-agent` 0.85.1 с его вложенными `pi-ai` и `pi-agent-core` 0.85.1. Верхнеуровневый `pi-agent-core` 1.1.0 не используется: `StreamFn` выводится из публичной сигнатуры `compact`. Наличие модели Opus 5.5 в живом каталоге не предполагается: offline adapter fixture построена из найденной записи Opus 5 с заменённым id.
Существующий entrypoint `../extensions/aoauth-fingerprint-2_1_281.ts` регистрирует `session_before_compact` для manual/threshold/overflow и `session_before_tree` для запрошенного summary непустой ветки. Только model calls Anthropic OAuth проходят изолированную обёртку effective provider: актуальные registry auth/baseUrl/nullable headers/env, общий fingerprint и тот же factory session header. Штатные `compact`/`generateBranchSummary` сохраняют summarizer prompts, split history/prefix, instructions, budgets, routing IDs, retry и usage/cost. Ordinary fingerprint и `move-reminders-2_1_281.ts` не изменены по результатам.
Summary не эмитит остальные provider extension hooks, `context` или `before_agent_start`; `move-reminders` не участвует при любом порядке загрузки. Входящие stream callbacks сохраняются; payload callback исполняется перед summary-only structural guard и fingerprint, response callback не дублируется. Проверка terminal response отвергает `aborted` и успешный stop без непустого текста до добавления штатных файловых XML sections. Для Opus 5/5.5 сохранена прежняя нормализация fingerprint: служебный пустой effort tail и message-level output_config удаляются из final body; это не отдельное исправление effort controls.

## Ошибки и ограничения

No-stream sentinel `No content to summarize` принимается только для фактически metadata-only ветки: подготовка нормализованных entries без лимита должна вернуть ноль сообщений и пустые Sets `read`/`written`/`edited`. Budget-empty с реальным содержимым отменяется с категорией `summary`, без checkpoint/fallback; штатные бюджеты и non-OAuth pipeline не меняются.
Любая ошибка внутри целевого hook возвращает cancel и безопасное уведомление с категорией `auth`, `settings`, `provider`, `summary`, `metadata` или `aborted`, без raw diagnostics/auth/history. Ошибка самого уведомления не разрешает fallback. Ядро представляет неудачу как обычную отмену. Отмена overflow не запускает compaction-triggered retry, но queued messages могут продолжить ход; threshold cancellation также не останавливает весь agent run. Auth failure, произошедший в ядре до hook, расширению недоступен.
Обязателен exclusive-owner режим: расширение должно быть единственным владельцем результата обоих session-before hooks. Runner не является middleware: поздний truthy instruction/label result способен стереть summary и разрешить default path, ранние return-based modifiers не видны; ранний/поздний cancel соблюдается, но поздний не возвращает потраченные токены. Автоматического устранения конфликтов с неизвестными расширениями нет.
Settings читаются одним доверенным snapshot на операцию через `SettingsManager.create` с project trust и `drainErrors`, без setters/flush; чтение может использовать кратковременный lock. Private ephemeral SDK settings и штатные retry UI events публичному context недоступны. Abort прекращает ожидание auth и предотвращает поздний provider dispatch, но физически отменить OAuth refresh публичным registry API невозможно.
Own marker `aoauthFingerprintSummary: 1` признаётся только с двумя полностью валидными массивами строк. Гарантируются compact→compact, branch→branch и own branch→compact в потребляемом диапазоне, включая OAuth→non-OAuth→OAuth. На non-OAuth действует только metadata bridge: aliased read/edited Sets и элементы исходного tree array, без auth/settings/provider/fingerprint. Native результаты не усыновляются marker; original entry objects не меняются. Штатный metadata gap compact→tree намеренно сохранён на обоих типах providers: tree не наследует compaction.details. Чужие fromHook schemas не поддерживаются.
Установка, reload, изменение настроек, ядра self/web и сервисов не выполнялись. Существующей регистрации entrypoint достаточно; новые factory handlers станут доступны после самостоятельного reload пользователем. Текущая активная сессия и историческая ошибка с версией `2.1.151` живым запросом не проверялись.

## Адресный offline запуск

Из корня `opencode-ctrl`, без запуска Pi или сервисов:

```bash
unshare -Urn env PI_OFFLINE=1 PI_TELEMETRY=0 node patches/_pi-dev/tests/run-aoauth-fingerprint-2_1_281.mjs
```

`unshare -Urn` обязателен; при его недоступности запуск завершается ошибкой, без сетевого fallback. `PI_OFFLINE` сам по себе не является сетевой изоляцией. Loader использует уже установленный Node 24 и jiti 2.7, отключает `fsCache`/`moduleCache`, проверяет реальное разрешение nested agent-core 0.85.1 из coding-agent, изолирует HOME/agent dir в `/tmp`, очищает окружение и запрещает немокированный fetch. Credentials, реальная история и settings не читаются. Fake settings – `SettingsManager.inMemory`; production factory проверяется через scoped mock `SettingsManager.create`. Настоящий move-reminders загружается из неизменённой временной копии с fake prompt config, без реальных config/инструкций.
Префикс runtime задаётся `AOAUTH_RUNTIME_NODE_MODULES`, по умолчанию `/home/coder/.local/pi-dev/node_modules`. При другом префиксе одновременно требуется согласованная замена абсолютных paths/typeRoots адресного tsconfig, в том числе на nested agent-core, а не root 1.1.0. Генераторы штатные; временный SDK facade только для адресной инъекции некорректных generator results/подсчёта StreamFn. Native preparation для chain-тестов читается из installed dist, production использует только публичные root exports. Временные файлы удаляются, в репозитории cache/logs не создаются.
Frozen fixture `fixtures/ordinary-281.json` снята с исходного entrypoint до рефакторинга: known hash `b83`, headers, billing, betas и Opus transformations. Session UUID нормализован, OS/arch зависят от платформы. Один test-файл проверяет ordinary regression, factory integration, реальные fake-fetch adapter bodies, retries/callback counts, abort/auth, metadata bridges, sentinel/error semantics и ограничения runner. Live LLM requests, полные suites и сборки не выполняются.
Адресная типизация на установленном согласованном графе:

```bash
node /home/coder/.local/share/opencode/bin/node_modules/typescript/bin/tsc --noEmit -p patches/_pi-dev/tests/tsconfig.aoauth-fingerprint-2_1_281.json
```

Линт выполняется только для изменённого entrypoint, helper, test и loader, без `--fix` и без форматирования сторонних файлов.
