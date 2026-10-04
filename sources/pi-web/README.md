# `@raven428/pi-web`

<!-- markdownlint-disable MD013 -->
<!-- cspell:ignore enum jmfederico patchset subkey subsession toolArgs toolContent -->

Patched build of [pi-web](https://github.com/jmfederico/pi-web), distributed as the `@raven428/pi-web` npm tarball in [GitHub Releases](https://github.com/megalomania428/update-graves/releases). The upstream ref and the patch number live in `build.yaml`: `ref: v1.202609.0` with `patch: 1` sets the package version to `1.202609.0-p1`. Pull requests and manual builds use `1.202609.0-p1.dev.<run>.<attempt>` and the shared `v999` draft.

Installation: `npm i -g <asset .tgz URL>`. For example:

```bash
npm i -g 'https://github.com/megalomania428/update-graves/releases/download/pi-web-v1.202609.0p1/pi-web-1_202609_0-p1.tgz'
```

The package requires Node.js >=22.19.0 and is not a standalone executable. Native dependencies such as `node-pty` can require build tools on the installation machine.

## List of patches

- `001-prompt-send-chord` – new "Ctrl+Enter sends message" Enter-key preference: Enter/Shift+Enter always insert a line break, Ctrl+Enter (⌘+Enter on macOS) sends
- `002-chat-card-disclosure` – configurable disclosure (`none`/`live`/`last`/`all`) for thinking, skill, and tool-result/details/diff/arguments/written-content transcript cards, plus the eighth `eventsGroup` subkey for the summarizing events group card; remove the duplicate red error block while keeping error text in forced-open `Result`, and show failed live `edit` previews there until the final result replaces them
  - `ui.disclosure.thinking` – `none` (default) | `live` | `last` | `all`
  - `ui.disclosure.skillInvocation` – `none` (default) | `live` | `last` | `all`
  - `ui.disclosure.toolResult` – `none` (default) | `live` | `last` | `all`; `live`/`last` target the last call or standalone result that renders this card
  - `ui.disclosure.toolDetails` – `none` (default) | `live` | `last` | `all`; `live`/`last` target the last call that renders this card
  - `ui.disclosure.toolDiff` – `none` | `live` | `last` | `all` (default); `live`/`last` target the last call that renders this card
  - `ui.disclosure.toolArgs` – `none` (default) | `live` | `last` | `all`; `live`/`last` target the last call that renders this card
  - `ui.disclosure.toolContent` – `none` | `live` | `last` | `all` (default); `live`/`last` target the last call that renders this card
  - `ui.disclosure.eventsGroup` – `live` (default) | `last` | `all` (`none` is not accepted)

```json
{
  "ui": {
    "disclosure": {
      "thinking": "all",
      "skillInvocation": "none",
      "toolResult": "last",
      "toolDetails": "none",
      "toolDiff": "all",
      "toolArgs": "none",
      "toolContent": "all",
      "eventsGroup": "last"
    }
  }
}
```

- `003-agent-session-title` – disable PI WEB's extra model request for automatic session naming and forward pi.dev extension-driven title changes live to the browser
- `004-panel-collapse-persistence` – persist navigation/workspace panel collapsed state, navigation section (`machines`/`projects`/`workspaces`/`sessions`) collapsed state, and the archived sessions section's expanded state across tab reloads
- `005-function-key-shortcuts` – allow lone function keys (`F1`-`F24`) as shortcut activators, not just Ctrl/Cmd/Alt chords
- `006-spawn-thinking-level` – add a `thinkingLevel` enum parameter to `spawn_subsession`/`spawn_session`, plus a `provider/model-id:level` suffix on their `model` parameter (the suffix wins over `thinkingLevel` when both are set); omitting both inherits the spawning session's level, while an explicit `model` without a level lets pi apply its own default for that model; an unsupported level is clamped to the nearest one the target model supports with a note in the result, and the actual level the child runs with is always reported back
- `007-tool-input-cards` – add highlighted JSON `Arguments` cards for tools except `edit`/`write`/`bash`/`read` and `Written content` cards for `write` with path-based highlighting and line numbers; cap expanded Details/Result/diff/Arguments/Written content and standalone result cards at half the window height with scrolling; pin all five scrollable card areas during streaming, show full Arguments/Written content while live then restore the 180-line limit, and highlight live text up to 64 KB without caching snapshots; open completed cards, including standalone results, at the bottom; keep the feed scrolling unless the user intentionally scrolls up, ignore gestures inside nested scroll areas, and follow content growth with ResizeObserver
- `008-ask-user-markdown` – render question text, question details, and custom ask_user answers as Markdown in forms and transcript records
- `009-chat-scroll-buttons` – add buttons to scroll the chat transcript to the beginning or latest message
- `010-events-group-limit` – add `ui.eventsGroupLimit` (0…10000, default 111) and split event groups into deterministic parts; zero disables splitting
