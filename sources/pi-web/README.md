# `@raven428/pi-web`

<!-- markdownlint-disable MD013 -->
<!-- cspell:ignore enum jmfederico patchset subkey subsession toolArgs toolContent subsessions Σtotal -->

Patched build of [pi-web](https://github.com/jmfederico/pi-web), distributed as the `@raven428/pi-web` npm tarball in [GitHub Releases](https://github.com/megalomania428/update-graves/releases). The upstream ref and the patch number live in `build.yaml`: `ref: v1.202609.0` with `patch: 7` sets the package version to `1.202609.0-p7`. Pull requests and manual builds use `1.202609.0-p7.dev.<run>.<attempt>` and the shared `v999` draft.

Installation: `npm i -g <asset .tgz URL>`. For example:

```bash
npm i -g 'https://github.com/megalomania428/update-graves/releases/download/pi-web-v1.202609.0p7/pi-web-1_202609_0-p7.tgz'
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
- `004-panel-collapse-persistence` – persist navigation/workspace panel collapsed state, navigation section (`machines`/`projects`/`sessions`) collapsed state, and the archived sessions section's expanded state across tab reloads
- `005-function-key-shortcuts` – allow lone function keys (`F1`-`F24`) as shortcut activators, not just Ctrl/Cmd/Alt chords; skip the two Docker dev-mode tests in `src/server/dockerControlAssets.test.ts` that refuse to run as root, because the graves builder runs the test suite as root
- `006-spawn-thinking-level` – add a `thinkingLevel` enum parameter to `spawn_subsession`/`spawn_session`, plus a `provider/model-id:level` suffix on their `model` parameter (the suffix wins over `thinkingLevel` when both are set); omitting both inherits the spawning session's level, while an explicit `model` without a level lets pi apply its own default for that model; an unsupported level is clamped to the nearest one the target model supports with a note in the result, and the actual level the child runs with is always reported back; `spawnSessions: false` now turns off only `spawn_session`, while the tracked-subsession tools keep working independently under `subsessions`
- `007-tool-input-cards` – add highlighted JSON `Arguments` cards for tools except `edit`/`write`/`bash`/`read` and `Written content` cards for `write` with path-based highlighting and line numbers; cap expanded Details/Result/diff/Arguments/Written content and standalone result cards at half the window height with scrolling; pin all five scrollable card areas during streaming, show full Arguments/Written content while live then restore the 180-line limit, and highlight live text up to 64 KB without caching snapshots; open completed cards, including standalone results, at the bottom; keep the feed scrolling unless the user intentionally scrolls up, ignore gestures inside nested scroll areas, and follow content growth with ResizeObserver
- `008-ask-user-markdown` – render question text, question details, and custom ask_user answers as Markdown in forms and transcript records; double-clicking inline code (single backticks, not code blocks) in ask_user cards and in chat Markdown selects its whole text, ready to copy, without copying it automatically; text typed into the Custom answer survives choosing another option (or unchecking Custom) and comes back when Custom is selected again, kept in the ask draft but never submitted and shown, marked as not selected, in the record of a superseded ask; ask drafts in localStorage expire 7 days after their last change and are swept on client start
- `009-chat-scroll-buttons` – add buttons to scroll the chat transcript to the beginning or latest message; remove the workspaces section from the navigation panel: selecting a project always opens its default (main) workspace, and the workspace trust toggle and details move to the project ⋯ menu
- `010-events-group-limit` – add `ui.eventsGroupLimit` (0…10000, default 111) and split event groups into deterministic parts; zero disables splitting
- `011-running-work-snapshot` – restore tool progress, pending tool finals, and running or deferred user `!`/`!!` commands when joining a session; capture a browser-safe history tail together with the stream watermark, replay events arriving during every refresh, and keep each shell command's last 1,000,000 characters until its result is recorded without duplicating identical commands
- `012-card-wrap-toggle` – add a `wrap` button to every content section of tool cards: at the right of the section header in `Details` and `Result`, shown while the section is open, and left of the copy button in the toolbar of `Arguments`, `Written content` and the preview/applied diff; `Details` and `Result` now start unwrapped with horizontal scrolling (`Details` used to wrap), each button independently switches its section to wrapping long lines anywhere and back without toggling the section, wrapped `Written content` lines keep the line number on the first visual line only, each button keeps a bottom-pinned area at the bottom and otherwise keeps the first visible line in place, moving a partly hidden first line with at least half a text line visible to the top of the area, resets when the card switches to another call, and is not persisted; the half-window height caps stay the same; `Details` and `Result` also get a `copy` button right of their `wrap` button, shown while the section is open, which copies the shown text without the `File`/`Command`/`Input` label and does not toggle the section, shows `copied` for 1.2 s after a successful copy, and its `copied` state resets when the card switches to another call
- `013-card-metadata` – put a right-aligned header with a tooltip on every piece on every chat card: `<tokens> · <durations> · <date> · <model:level>` on messages and `<tokens> · <duration> · <date>` on events groups, tool cards, `ask_user` cards and extension dialogs, every part optional; tokens as `↑input ↓output ⤴cache write ⤵cache read ∴reasoning Σtotal` (`1.23k`, `63.49k`; `∴∅` when the provider reports no reasoning breakdown) for finished answers, summaries and tool results with their own usage, summed in events group headers and, for the whole session file, in the status bar instead of the lone `↑`/`↓`; the time spent on finished work as `45s`, `1m5s` or `1h2m3s`, with the answer generation time and, on the final answer of a turn with more than one answer or any tools, `Σ` with the whole turn since the user message; a local `2026-10-22 14:05:31` date instead of the browser-locale one, with the zone in its tooltip; the model keeps only the part after its last `/`; history passes the session entry time as the answer end and gives summaries their time and usage; a model answer made only of tool calls shows its header above its first tool card, and live answers keep their metadata on their pending tool cards as history does; live tool, shell and streaming events carry server times that the join snapshot keeps; closed extension dialogs keep their close time; running work shows only its start, and a reloaded tool call shows its duration only when it was the only call of its answer, otherwise only its end; the client history cache key moves to v3 so pages cached by an older build are not reused
