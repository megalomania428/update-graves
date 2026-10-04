# Opencode with patches

<!-- cspell:ignore unarchiving lydell -->
<!-- markdownlint-disable MD013 -->

[Opencode](https://github.com/anomalyco/opencode) is an AI coding agent with a CLI, WebUI and Electron desktop application. This recipe builds patched Linux x64 and Windows x64 releases from the upstream ref in `build.yaml`.

The build scripts name and archive everything themselves: `opencode-<version>-linux-x64.txz` and `opencode-<version>-windows-x64.7z` for the CLI, plus `opencode-desktop-<version>-linux-x86_64.AppImage`, `opencode-desktop-<version>-linux-amd64.deb`, `opencode-desktop-<version>-linux-x86_64.rpm` and `opencode-desktop-<version>-windows-x64.exe` for the desktop application, where `<version>` is `GRAVES_FILE_VERSION`.

## Patches

### `patches/common`

- `001-ctrl-enter` – submit WebUI and Desktop messages by `Ctrl-Enter` instead of `Enter`
- `003-markdown-code-scrollbar` – show horizontal scrollbar in code blocks instead of hiding it
- `004-retry-overload-cap` – cap overload retry delay to 11s and treat certificate errors as retryable
- `007-unarchive-sessions` – add support for unarchiving sessions
- `008-user-message-markdown` – render user messages as markdown instead of plain pre-wrap text
- `009-timestamp-24h` – show message timestamps in 24h format with ISO-like date
- `010-infinite-retry-with-context` – auto recover session after timeout error
- `011-mcp-auto-reconnect` – auto reconnect disconnected mcp servers
- `012-mcp-status-display` – backport of MCP servers showing fix

`patches/linux` keeps only the Linux x64 build.

`patches/windows` keeps only the Windows x64 build and replaces the native `node-pty` binding with `@lydell/node-pty-win32-x64`.
