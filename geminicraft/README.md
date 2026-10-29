# Geminicraft

Gemini CLI inside Minecraft, as an addon for **Claudecraft** — the same windows, chat
cards, file manager, editor, terminal and git tools, but the assistant is Google's
Gemini CLI.

| | Claudecraft | Geminicraft |
|---|---|---|
| Conversation | `/c` | `/gem` |
| Coding agent | `/cc` | `/gemini` |
| Window key | K | G |
| Settings | `/claude` | `/geminicraft` |
| Bridge port | 18741 | 18743 |

## How it works

The Geminicraft bridge starts `gemini --experimental-acp` and talks the
[Agent Client Protocol](https://agentclientprotocol.com): sessions (`session/new`,
`session/load` — the session id is kept per chat), streamed answers, tool calls,
plans, and permission requests, which appear as the usual Allow / Deny / Always
cards (or follow `/gemini auto off|edits|all`). Gemini writes files through the
bridge (`fs/write_text_file`), so `/gemini undo` restores them. `/gem` chats never
get write access.

Messages sent with `f` while Gemini works run right after the current turn (ACP has
no way to add to a running turn).

## Setup

1. Install Claudecraft (`claude-chat-1.1.0.jar`) into a Fabric 26.3 profile: Geminicraft ships
   inside it (jar-in-jar). Don't add a separate `geminicraft-*.jar` (a duplicate mod id stops Fabric).
2. `/geminicraft` → **Install Gemini CLI** (runs `npm install -g @google/gemini-cli`; needs Node.js).
3. **Sign in with Google** — Gemini CLI opens the browser on this PC — or paste an
   API key from aistudio.google.com/apikey.
4. Optional: `/gemini model gemini-2.5-pro`, `gemini_path` / `gemini_args` in
   `config/geminicraft/bridge-config.json` for a custom CLI.

Commands that belong to Claude Code itself (plan mode, rewind/fork, skills, hooks,
CLAUDE.md memory, output styles, session search) are not available here; project
instructions for Gemini go into `GEMINI.md`.

## Tests

`gradlew :geminicraft:build`, then `python -m unittest discover -s geminicraft/tests`
— runs the shipped payload against `tests/fake_acp_agent.py`, a small agent speaking
real ACP (no Google account needed).
