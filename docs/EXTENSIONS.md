# Extensions, permissions, shells and Docker

## Layout

| Path | What it is |
| --- | --- |
| `src/client/` | Minecraft UI: commands, chat rendering, screens, HTTP client |
| `bridge/bridge.py` | Local bridge: queue, sessions, HTTP API, extensions |
| `bridge/backends/` | Claude Agent SDK, Messages API, projects, files, git, shell, agents, connectors |
| `<workspace>/extensions/<id>/manifest.json` | Declaration of an MCP extension |
| `<workspace>/extensions/<id>/server.py` | Example implementation (any language works) |

`workspace` is set in `config/claude-chat/bridge-config.json`. By default it is
`config/claude-chat/workspace`, not the whole Minecraft folder. In Docker it is
`MC_WORKSPACE=/projects`.

## Writing an extension

Copy `examples/extensions/system-info` into `<workspace>/extensions/`. If `python`
on your PATH is a different environment, put the absolute path of the bridge's
Python into `server.command`. After `/cc reload`, the next request gets the new tool —
Minecraft does not need a restart. `/cc tools` shows the OS, process rights,
available executables and MCP servers.

A manifest has `id`, `enabled` and `server` with `command`, `args` and optional
`env`. `${extension}` and `${workspace}` are expanded in `args`. Extensions use the
standard MCP stdio transport: log to stderr, keep stdout for the protocol. Install
an extension's dependencies into its own environment; reload never installs packages.

Claude Code can write such extensions itself and call the built-in
`minecraft.reload_extensions` tool, or you run `/cc reload`. If a manifest is
invalid, the previous configuration stays active. A running request keeps its tool
set; the new set applies from the next queued request. An `f` message goes into the
same live session and therefore still sees the old set — to apply immediately, run
`/cc cancel` and send the request again. Tools are swapped; Minecraft bytecode is not.

Any other MCP server can be added to `bridge-config.json` → `mcp_servers`, for example:

```json
{
  "browser": {
    "command": "npx",
    "args": ["-y", "@playwright/mcp@latest"]
  }
}
```

That object is the value of `mcp_servers`; keep the other config fields. Most popular
servers are also one click away in `/cc connectors` (GitHub, Playwright, Context7,
databases, Sentry, Notion, Slack, Figma and more). Pin a tested version instead of
`latest` for reproducible setups. A browser MCP runs next to the bridge — it is not
a browser inside Minecraft. In Docker a graphical browser needs a display/VNC or
headless mode.

## Administrator rights, SSH, CMD, PowerShell

Claude and the in-game shell (`/sh`, `/ps`, `/cmd`, `/bash`) run with the rights of the
account that runs the bridge. For real Windows CMD, PowerShell and Docker Desktop,
run the bridge on Windows (the mod does this by default). Claude Code's shell tool
on native Windows needs Git Bash. SSH uses the account's existing keys, ssh-agent
and known_hosts. Logins, MFA, sudo passwords and keys are never faked or bypassed.

For administrator rights on Windows, start a terminal with "Run as administrator"
and run the bridge from there. On Linux, root means running the bridge as root;
prefer giving the user access to Docker/SSH and sudo for specific commands. The mod
never elevates itself and does not disable UAC. Root in a container is not an
administrator on the host.

The SDK runs in the default permission mode. Actions the SDK asks about appear in
Minecraft as Allow / Deny / Always buttons; `/cc auto edits|all` changes that per chat.
`bypassPermissions` is never enabled.

The Docker socket gives broad control over the host through the Docker daemon, so it
is mounted only by an opt-in compose file. Do not add `privileged: true`. SSH inside
the container needs its own agent or keys; host SSH settings are not mounted.

## Docker

From `bridge/`, set absolute paths:

```sh
export MC_GAME_DIR=/path/to/minecraft-instance
export CLAUDE_WORKSPACE=/path/to/project
export BRIDGE_UID=$(id -u)
export BRIDGE_GID=$(id -g)
export ANTHROPIC_API_KEY=...   # also used by the SDK in the container
# existing folders must be accessible to this UID/GID
docker compose up --build -d
```

Linux, with access to the host Docker daemon:

```sh
export DOCKER_GID=$(stat -c %g /var/run/docker.sock)
docker compose -f compose.yaml -f compose.host-docker.yaml up --build -d
```

The port is published on the host loopback only: inside the container the bridge
listens on 0.0.0.0, outside on 127.0.0.1. The bridge token goes to `connection.json`,
never to chat or logs. On Windows, run the bridge on the host for Windows tools —
a Linux container only sees a Linux shell and the mounted folders.

The Dockerfile and compose files are prepared but the image has not been built and
tested yet.
