# Herald

An Omarchy plugin that watches an IMAP mailbox and turns arriving mail into
the notification you actually wanted — the one-time code, set large and one
click from the clipboard; the amount and the merchant; the sign-in you did not
make — classified by any OpenAI-compatible endpoint.

Everything else stays in your inbox where it belongs.

![Herald's bar glyph and panel: a live one-time code set at display size over
the recent codes, transactions and alerts](preview.png)

The toast that arrives with it:

```
󰯄  Google  ·  281 940
   Sign-in verification  ·  10m left  ·  click to copy
```

Clicking that toast puts `281940` on the clipboard. So does clicking the row in
the panel, or pressing `c` with the panel open.

## How it fits together

Two halves, split on purpose.

**`bin/herald-daemon`** is a Python process holding one IMAP connection open in
`IDLE`. When mail lands it fetches the message, reduces it to plain text, asks
the model what it is, and prints one JSON object per line on stdout. It is
stdlib-only — no `pip install`, nothing to keep up to date — and it raises its
own notifications through `omarchy notification send`, so it behaves identically
whether the shell started it or you ran it in a terminal.

**`Service.qml` / `BarWidget.qml` / `Panel.qml`** are the shell half. The
service owns the daemon's lifecycle and holds the events it has reported; the
bar widget is one glyph that says whether anything is waiting; the panel is the
list, with the live code promoted to the top.

```
manifest.json          kinds: service + bar-widget
Service.qml            daemon lifecycle, event store, IPC target "herald"
BarWidget.qml          bar glyph + count, hosts the panel
Panel.qml              hero + event list + footer actions
Model.js               pure presentation logic (node-testable)
components/EventRow.qml
bin/herald-daemon      IMAP IDLE → model → notification
bin/herald-action      what clicking a notification does
bin/herald-setup       one-time interactive setup
```

The daemon is the source of truth and the panel is a view of it. If you would
rather run the watcher under systemd, set `"managed": false` in the config: the
plugin then stops spawning its own and just reads the history file.

## Requirements

Everything here except Python is already on a stock Omarchy box.

| Needs | For | Package |
|---|---|---|
| `python3` | the mail watcher | `python` |
| `secret-tool` | reading the IMAP password and API key from the login keyring | `libsecret` |
| `wl-copy` | putting a code on the clipboard | `wl-clipboard` |
| `omarchy notification send` | raising the toast through the shell's own server | ships with Omarchy |
| `jq`, `gum` | `herald-setup` only — `gum` degrades to plain prompts if absent | `jq`, `gum` |

No Python packages. The daemon is stdlib-only, so there is nothing to
`pip install` and nothing to keep patched. Python 3.13 or newer uses
`imaplib`'s native `IDLE`; older versions fall back to polling on
`scan.pollSeconds`.

**Privilege boundary:** nothing in this plugin uses `sudo`, `pkexec`, or any
other escalation, and nothing is installed outside your home directory. It
runs as you, inside `omarchy-shell`, like every Omarchy plugin.

**Network:** two outbound connections, both of which you configure — your IMAP
host, and the OpenAI-compatible endpoint in `llm.baseUrl`. There are no
analytics, no telemetry, and no calls to anything else.

## Install

```bash
omarchy plugin add https://github.com/sai80082/omarchy-herald.git
omarchy plugin enable io.github.sai80082.herald --section right
```

`omarchy plugin add` clones into `~/.config/omarchy/plugins/` under the
manifest id and leaves the plugin disabled so you can read the code first.
Later, `omarchy plugin update io.github.sai80082.herald` fast-forwards it and
shows you the diff before it touches anything.

Developing from a checkout elsewhere:

```bash
git clone https://github.com/sai80082/omarchy-herald.git
cd omarchy-herald
./scripts/dev-install     # copies into ~/.config/omarchy/plugins/ and rescans
```

> Plugins run unsandboxed inside `omarchy-shell`, with your permissions and
> your keyring. Read `bin/herald-daemon` before you enable this — it is the
> part that holds your mail and your API key.

## Set up

```bash
~/.config/omarchy/plugins/io.github.sai80082.herald/bin/herald-setup
```

It asks for the mailbox and the endpoint, writes `~/.config/omarchy/herald/config.json`,
and puts the two secrets in the login keyring via `secret-tool` — they are never
written to the config file. The panel's cog button runs the same script.

Then check it before trusting it with your inbox:

```bash
bin/herald-daemon --once 3 --dry-run | jq .
```

That classifies your three most recent messages, prints exactly what the plugin
would have shown, and raises nothing.

### Mail

Use an app password, not your account password. Most providers require one for
IMAP anyway, and it can be revoked on its own.

| Provider | Host | Port |
|---|---|---|
| Fastmail | `imap.fastmail.com` | 993 |
| Gmail | `imap.gmail.com` | 993 |
| Proton (Bridge) | `127.0.0.1` | 1143, `"ssl": false`, `"starttls": true` |
| Migadu | `imap.migadu.com` | 993 |

Outlook/Office 365 largely no longer accepts password auth for IMAP; it wants
OAuth2, which this plugin does not implement.

### Model

`llm.baseUrl` is any OpenAI-compatible `/v1`. The daemon asks for a strict
`json_schema` response and falls back to plain JSON mode when a server rejects
it, which is what makes local endpoints work:

```jsonc
// OpenAI
{ "baseUrl": "https://api.openai.com/v1", "model": "gpt-4o-mini" }
// Ollama — nothing leaves the machine, no API key needed
{ "baseUrl": "http://localhost:11434/v1", "model": "llama3.1:8b" }
// OpenRouter
{ "baseUrl": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o-mini" }
```

If the endpoint is unreachable, or the key is wrong, or the model returns
nonsense, the daemon falls back to local pattern matching rather than going
quiet. An OTP still reaches the screen when the network is having a bad day —
it just arrives with less context attached.

## Removing it

```bash
omarchy plugin remove io.github.sai80082.herald
```

That stops the watcher and takes the widget off the bar. Your account details
outlive it on purpose, so reinstalling does not mean setting it up again.
To remove those too:

```bash
rm -rf ~/.config/omarchy/herald          # host, endpoint, notification rules
rm -rf ~/.local/state/omarchy/herald     # classified events, seen marker, last UID
secret-tool clear service omarchy-herald account imap
secret-tool clear service omarchy-herald account llm
```

Nothing else on the system is touched — no files outside those paths, no
system services, and no changes to anyone else's config. Your `shell.json`
loses only the plugin's own bar entry, which `omarchy plugin remove` handles.

## What leaves your machine

With a cloud endpoint: for every new message, the sender, the subject, the
date, and the first `llm.maxBodyChars` (default 4000) characters of the body.
That includes the code itself. If that is not a trade you want to make, point
`baseUrl` at a local model — the plugin is built so that path is a one-line
change, not a different plugin.

**No stylesheet is ever sent.** A marketing email is mostly CSS by weight, and
all of it would be billed, counted against the body limit, and read by the
model as if it meant something — while being full of digits (`600px`, `#0a0`,
`max-width:4px`) that a classifier hunting for a one-time code does not need
to see. CSS is removed at two levels before the request is built:

- **Markup**, before parsing: `<style>` and `<script>` elements and HTML
  comments (Outlook conditional blocks hide whole stylesheets in them). Style
  attributes never survive extraction in the first place.
- **Text**, after extraction: at-rules and declaration blocks with their
  selectors, for senders who paste a stylesheet into the `text/plain` part —
  which never meets an HTML parser — and for anything the parser let through.

The text pass is deliberately conservative: a `{...}` block is only dropped
when its contents read as declarations, and the run in front of it only goes
with it when that run reads as a selector. `Your order {ref 8842} shipped` and
`{"amount": 1249}` come through untouched. On a typical bank alert this is an
~87% reduction in what leaves the machine.

Two knobs reduce it without switching endpoints: `scan.senderDenylist` and
`scan.subjectDenylist` are case-insensitive regexes, applied before the model
is called, so a matching message is never sent anywhere.

On disk, `~/.local/state/omarchy/herald/events.jsonl` holds the last 200
classified events — subjects, senders, and live codes among them. It is written
`0600` and trimmed automatically, and there is a test that fails if the mode
ever drifts.

## Configuration

`~/.config/omarchy/herald/config.json` — see `config.example.json` for every
key with a comment. The ones worth knowing:

| Key | Default | What it does |
|---|---|---|
| `notify.categories` | see example | Urgency per category, or `"off"` to record without a toast |
| `notify.autoCopyOtp` | `true` | Put a code on the clipboard the moment it arrives |
| `notify.minConfidence` | `0.35` | Below this, record the event but stay quiet |
| `scan.backfill` | `0` | Messages to classify on first run. `0` = only new mail |
| `scan.maxPerCycle` | `12` | Ceiling per batch, so a long offline spell cannot fire fifty toasts |
| `llm.maxBodyChars` | `4000` | How much of the body is sent |
| `managed` | `true` | `false` when you run the daemon yourself |

The bar widget's own settings live in `shell.json` (Setup → Plugins, or the
`barWidget.schema` block in `manifest.json`): badge mode, whether to hide the
glyph when nothing is waiting, and how many rows the panel lists.

## Using it

| | |
|---|---|
| Click the glyph | Open the panel |
| Right click | Reconnect the watcher |
| Middle click | Mark everything read |
| `↑` `↓` / `j` `k` | Move the cursor |
| `Enter` | Copy the selected row's code |
| `c` | Copy the code in the hero |
| `r` / `m` | Reconnect / mark read |
| `Esc` | Close |

Shell IPC:

```bash
omarchy-shell herald status          # JSON: state, account, counts
omarchy-shell herald restart         # reconnect
omarchy-shell herald copy <event-id>
omarchy-shell shell toggle io.github.sai80082.herald
```

## Theming

Herald paints nothing of its own. Colour comes from `Color.*`, spacing and type
from `Style.*`, and every control is a `qs.Ui` primitive — `KeyboardPanel`,
`CursorSurface`, `PanelActionButton`, `PanelSectionHeader`. Change your Omarchy
theme and Herald changes with it, including the notification toasts, which go
through the shell's own notification server rather than drawing themselves.

Colour is spent deliberately: accent for a live code and for money, urgent for
security alerts and for a watcher that has lost its connection, plain
foreground for everything else.

## Tests

```bash
./scripts/test
```

49 tests, none of which need a mail server, an API key, or a running shell.
`tests/fake_imap.py` is a minimal IMAP4rev1 server that speaks exactly the
commands the daemon issues, including `IDLE`, so the integration tests drive a
real socket through connect → baseline → block → wake → fetch → classify →
emit. The model endpoint is faked in-process, including a server that rejects
`json_schema` so the fallback path is exercised rather than assumed.

## Forking it

The plugin directory name must match `id` in `manifest.json`, so a fork needs
both changed together: pick your own namespaced id (`io.github.<you>.herald`),
update `author` and the `metadata` URLs, and run `./scripts/dev-install` — it
installs under whatever id the manifest declares.

`id` also appears in the QML as `moduleName`, as the bar widget's IPC target,
and in `bar.shell.serviceFor(...)`; a fork has to change all of them or the
panel will not find its own service. The service's own IPC target (`herald`)
and the paths under `~/.config/omarchy/herald/` are named for the product
rather than the id, so those stay put.

## License

MIT. See `LICENSE`.
