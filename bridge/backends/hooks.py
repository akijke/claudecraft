"""Claude Code hooks from settings.json: list, add, remove, and a log of what ran.

Hooks live in "hooks" of ~/.claude/settings.json, <project>/.claude/settings.json and
.claude/settings.local.json:
  {"hooks": {"PostToolUse": [{"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "npm run lint"}]}]}}
The CLI runs them itself (the bridge loads these settings, see memory.py); with
include_hook_events the CLI reports each run, which the bridge keeps in a short log
(/cc hooks log) and shows failures in the chat. Adding/removing edits the project file.
"""
import json
import os
from collections import deque
from pathlib import Path

from backends.util import save_json

EVENTS = ('PreToolUse', 'PostToolUse', 'UserPromptSubmit', 'Stop', 'SubagentStop', 'Notification',
          'PreCompact', 'SessionStart', 'SessionEnd')
LOG = deque(maxlen=100)


def sources(project):
    home = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')
    return [('user', home / 'settings.json'), ('project', Path(project) / '.claude' / 'settings.json'),
            ('local', Path(project) / '.claude' / 'settings.local.json')]


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def listing(project):
    rows = []
    for source, path in sources(project):
        hooks = _load(path).get('hooks') or {}
        for event, groups in hooks.items():
            for gi, group in enumerate(groups if isinstance(groups, list) else []):
                for hi, hook in enumerate(group.get('hooks') or []):
                    rows.append({'source': source, 'file': str(path), 'event': event,
                                 'matcher': group.get('matcher', ''), 'type': hook.get('type', 'command'),
                                 'command': str(hook.get('command') or hook.get('prompt') or '')[:300],
                                 'id': f'{event}:{gi}:{hi}'})
    return rows


def add(project, event, matcher, command):
    if event not in EVENTS:
        raise ValueError('Событие хука: ' + ', '.join(EVENTS))
    if not command.strip():
        raise ValueError('hooks add <Событие> [matcher] -- <команда>')
    path = sources(project)[1][1]
    data = _load(path)
    groups = data.setdefault('hooks', {}).setdefault(event, [])
    group = next((g for g in groups if g.get('matcher', '') == matcher), None)
    if group is None:
        group = {'matcher': matcher, 'hooks': []} if matcher else {'hooks': []}
        groups.append(group)
    group['hooks'].append({'type': 'command', 'command': command.strip()})
    path.parent.mkdir(parents=True, exist_ok=True)
    save_json(path, data)


def remove(project, hook_id):
    path = sources(project)[1][1]
    data = _load(path)
    try:
        event, gi, hi = hook_id.split(':')
        groups = data['hooks'][event]
        del groups[int(gi)]['hooks'][int(hi)]
        if not groups[int(gi)]['hooks']:
            del groups[int(gi)]
        if not groups:
            del data['hooks'][event]
    except (KeyError, IndexError, ValueError):
        raise ValueError('Нет такого хука в настройках проекта (свои ~/.claude хуки правь в файле)') from None
    save_json(path, data)


def record(message):
    """A HookEventMessage from the stream → log row (None for the 'started' half)."""
    data = message.data or {}
    if message.subtype != 'hook_response':
        return None
    row = {'event': message.hook_event_name or data.get('hook_event', ''), 'name': data.get('hook_name', ''),
           'exit': data.get('exit_code'), 'outcome': data.get('outcome', ''),
           'output': str(data.get('output') or data.get('stdout') or data.get('stderr') or '')[:500]}
    LOG.append(row)
    return row
