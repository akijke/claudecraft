# Pair programming (#69) — what is ready and what is left

## Ready (bridge side, tested)

- `/cc guest new <name> [view|chat] [hours]` — a guest token for **the current chat only**,
  12 h by default (1–72). The token is shown once in the chat with a copy button; the bridge
  keeps only its SHA-256 in `config/claude-chat/guests.json`.
- `/cc guest list`, `/cc guest revoke <name>`.
- Guest endpoints (everything else answers 401 to a guest token):
  - `GET /guest/events?after=<seq>` — events of that chat, without permission prompts,
    questions, account/URL events and the token event itself.
  - `POST /guest/say {"text": …}` — only with `chat` scope: queued as
    `[guest <name>]: …` and shown to the owner as a separate line.
- Guests can never answer permission prompts, use `f`, files, git, terminal, settings or MCP.
  Tests: `bridge/test_bridge.py` → `GuestTests`.

## Left — needs the owner's decisions

1. **How the guest reaches the bridge.** It listens on 127.0.0.1 only, on purpose. Options:
   - SSH tunnel to the owner's PC (guest needs an SSH account — usually not wanted);
   - Tailscale / ZeroTier between the two PCs, bridge bound to the tailnet address
     (`--listen` would need a third choice);
   - a small relay server (the owner's VPS) that forwards only `/guest/*` — best for strangers.
2. **Guest client.** Either a second Minecraft with this mod in a "guest" mode (reads
   `/guest/events` and shows the usual chat cards) or a tiny web page. Not written yet:
   the choice depends on (1).
3. **Prompt-injection policy.** A `chat` guest's text goes to Claude. Tools still need the
   owner's approval (auto mode is not bypassed), but with `/cc auto all` a guest message can
   make Claude run commands. Proposal: guest messages always run with auto `off`.
4. Whether guests should also see the files Claude reads/edits (diffs) — currently they see
   the same tool cards the owner sees, including edit diffs.
