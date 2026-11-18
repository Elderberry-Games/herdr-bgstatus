# herdr-bgstatus

A [Herdr](https://herdr.dev) plugin. It shows a count and a spinner in front of the agent icon when an agent still has background work: shells, terminals, tasks, or sub-agents.

```
 agents          priority
 ⠀ ⠀ · ✓ 🐝 apiary  👑 lead     <- done, nothing in the background
   claude
 2 ⠹ · ✓ 🍋 citrus  42+77      <- "done", but 2 shells still run
   claude · 2 shells
 1 ⠹ · ◐ 🦊 fox  12 docs        <- working, 1 shell
   opencode · 1 shell
```

- Herdr keeps its own colored state icon and bold workspace name. The plugin only adds a plain-text slot in front.
- Works with Claude Code, Codex, and OpenCode.
- No inference and no network. A small Python script (standard library only) reads the visible pane text through the Herdr CLI.
- Rows stay aligned: idle rows get blank characters of the same width (`U+2800`), so no text moves when a spinner shows or goes away.
- All values have a TTL (15 s). If the script stops, the slot goes away on all rows.

## What it detects

It reads the last 8 non-empty lines of each agent pane:

| Text in the footer | Counted as |
|---|---|
| `2 shells`, `↓ 1 shell`, `2 shells still running` | shell |
| `3 background terminals running` | terminal |
| `2 agents running`, `1 local agent`, `2 background agents` | agent |
| `4 background tasks`, `1 task running` | task |

It ignores the agent-switcher hints `← 5 agents` and `← for agents`.

## Install

Needs Herdr 0.9.1 or newer and `python3` 3.9 or newer.

```bash
herdr plugin install Elderberry-Games/herdr-bgstatus
```

Then add the `$bg` token in front of `state_icon` in `~/.config/herdr/config.toml`. Back up the file first:

```toml
[ui.sidebar.agents]
rows = [["$bg", "state_icon", "machine", "workspace", "tab"], ["agent"]]
```

If you have your own `rows`, add `"$bg"` as the first item of the first row. Herdr puts ` · ` after a custom token; that is the space between the slot and the icon, and no setting removes it.

Then reload:

```bash
herdr server reload-config
```

The plugin starts when the Herdr server starts. To start it now, without a restart:

```bash
herdr plugin action invoke restart --plugin elderberry.bgstatus
```

## Control

| Command | Effect |
|---|---|
| `herdr plugin action invoke stop --plugin elderberry.bgstatus` | stop; all slots go away at once |
| `herdr plugin action invoke restart --plugin elderberry.bgstatus` | restart |
| `python3 bgstatus.py status` (in the plugin directory) | show if it runs |
| `python3 bgstatus.py run --only w1:p2` | run in the foreground on one pane (for tests; uses its own lock) |

Log and pidfile: `~/.local/state/herdr/plugins/elderberry.bgstatus/`.

## Uninstall

```bash
herdr plugin uninstall elderberry.bgstatus
```

Remove `"$bg"` from your sidebar rows and run `herdr server reload-config`.

## How it works

- Every 1 s: `herdr agent list`. Slots that changed, and slots with a spinner, are sent with `herdr pane report-metadata --token bg=... --ttl-ms 15000`.
- Every 2 s (and at once for a new agent): `herdr pane read <pane> --source visible`, to count background work.
- Unchanged slots are refreshed every 5 s, so they do not expire while the plugin runs.
- One daemon per Herdr server: a `flock` on a pidfile keyed by the socket path. It stops when the server socket goes away, after 10 failed ticks in sequence, or on the stop action. Stop clears all slots in one pass.

## Limits

- It reads screen text. If an agent changes its footer text, the patterns need an update.
- Text such as "2 shells" or "4 background tasks" in the last 8 lines counts as work, also when it is part of a message or the input box.
- Herdr always puts ` · ` after a custom token.
- A new agent row can move once, for up to 1 s, before its first slot arrives.

## Development

```bash
python3 -m unittest discover -s tests
```

## License

MIT. See [LICENSE](LICENSE).
