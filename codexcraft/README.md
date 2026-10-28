<div align="center">

# ✦ Codexcraft

**OpenAI Codex inside Minecraft** — a companion mod for [Claudecraft](../README.md).

![Minecraft 26.3](https://img.shields.io/badge/Minecraft-26.3-62B47A)
![Fabric](https://img.shields.io/badge/Fabric-0.20.1-DBD0B4)
![Codex app-server](https://img.shields.io/badge/Codex-app--server-53E0BA)

</div>

A separate client mod for Minecraft Java 26.3, Java 25, Fabric Loader 0.20.1+ and
Fabric API. It runs side by side with Claudecraft: it has its own Java packages, key,
HTTP port, settings and chats.

## Install

1. Put `claude-chat-1.1.0.jar` and `codexcraft-1.1.0.jar` into the profile's `mods` folder.
2. Install Python 3.11+ and the official Codex CLI. Check `codex --version`.
3. Start Minecraft. The Python bridge unpacks and starts automatically.
4. `/codex settings` or `/codexcraft` → **Sign in with ChatGPT** → open the sign-in
   page and enter the code **on the page**. If `codex login` was already done on the
   PC, the existing login is used.
5. **J** opens the Codexcraft window; **K** stays with Claudecraft.

The addon talks to `codex app-server` over stdio, not the Anthropic API/SDK. Its only
Python dependency is `aiohttp`; the Codex CLI is installed separately. Credentials are
kept by Codex; the mod never copies tokens from its store. For API-key auth, run
`codex login --with-api-key` in a terminal.

## Commands

| Command | What it does |
| --- | --- |
| `/gpt <text>` | Conversation through Codex in a read-only sandbox |
| `/codex <task>` | Work on code in the selected project |
| `/gpt ui`, `/codex ui` | Conversation window (J) |
| `/gpt new`, `/codex new` | New separate conversation |
| `/gpt chats`, `/codex chats` | Pick a chat |
| `/<mode> q <text>` | Queue in the current chat |
| `/<mode> f <text>` | Add to the active turn via `turn/steer`, without a restart |
| `/<mode> cancel` | `turn/interrupt`, stop and clear the queue |
| `/<mode> restart` | Stop the request; keep the thread ID to continue |
| `/codex project pick` | Pick a project folder on the PC |
| `/codex project` | Projects; also open/add/new/rename/remove |
| `/codex files`, `ls`, `cat`, `tree`, `find`, `cd` | File manager (window and chat commands) |
| `/codex git` | Git of the current project |
| `/codex sh <command>` | System shell command with output in the game |
| `/codex spawn <task>`, `tasks`, `stop <id>` | Parallel tasks |
| `/codex connectors`, `connect`, `reload` | MCP and extensions for the next request |
| `/codex model <id>`, `/codex model default` | Model / model from the CLI settings |
| `/codex tokens` | Tokens of the current chat, without a cost estimate |
| `/codex auto off\|edits\|all` | Policy for the next request |

All regular project, file and Git operations are inherited from Claudecraft. The
global `/sh`, `/git`, `/ps` shortcuts are not registered by the addon to avoid clashing
with the main mod — use them with the `/codex` prefix or inside the addon's window.

`auto off` (default): read-only sandbox, asking for permission to go beyond it.
`edits`: workspace-write, extra permissions are asked. `all`: workspace-write and
automatic approval of command/file requests; this does not disable the sandbox. In
`/gpt`, escalation is denied whatever `auto` is. Direct user commands (`git`, the file
manager, `sh`) are run by the bridge with the PC user's rights, as in Claudecraft.

If `f` arrives after a turn has finished, it runs as the next request in the same
conversation. Sessions are kept as Codex thread IDs; Claude session IDs are not
imported. Claude agents and their presets are not carried over — Codex uses its
`AGENTS.md`, the CLI settings and plain `spawn <task>` without `@name`.

## Configuration and build

Data: `config/codexcraft/`; default port **18742** (Claudecraft uses 18741). In
`bridge-config.json` you can set `codex_path` (absolute path to the CLI) and
`codex_model`. Without a model the addon keeps the choice from the Codex config. The
Python path is set in `/codex settings`. Log: `config/codexcraft/runtime/bridge.log`.

From the repository root:

```powershell
.\gradlew.bat :codexcraft:build
.\.venv\Scripts\python.exe -m unittest discover -s codexcraft/tests -v
```

Result: `codexcraft/build/libs/codexcraft-1.1.0.jar`. `generateShared` builds a private
copy of the shared UI and workspace code from the Claudecraft sources; the Codex
protocol and the sign-in screen live in this module. The main mod's sources are not
changed by generation.

`CODEXCRAFT_CLI_IT=1` enables a test against the real CLI: `initialize` and
`account/read`, without a model request. `gradlew :codexcraft:runShots` starts both
mods, takes demo screenshots and quits the game.

The checks do not replace a manual run with a live model, code sign-in and external
MCP servers. The App Server evolves: use a current CLI with the v2 methods.
Protocol: https://learn.chatgpt.com/docs/app-server
