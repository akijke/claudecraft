"""Overlaycraft tools for Claude: an MCP stdio server with no dependencies.

Talks to the running game on 127.0.0.1 with the token from ~/.overlaycraft/connection.json.
Claudecraft installs it as <workspace>/extensions/overlaycraft; for Claude Code on the PC:
    claude mcp add overlaycraft -- python <path>/server.py
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

CONNECTION = Path.home() / '.overlaycraft' / 'connection.json'
WIDGETS = ['music', 'food', 'tabs']

TOOLS = [
    {'name': 'overlay_status',
     'description': 'What the Minecraft overlay (Overlaycraft) shows now: media sessions playing on the PC '
                    '(YouTube, SoundCloud, VK, huevo.su, Telegram, Spotify...), the Yandex Eda order, open browser '
                    'tabs and the overlay layout.',
     'inputSchema': {'type': 'object', 'properties': {}}},
    {'name': 'overlay_media',
     'description': 'Control music/video playing on the PC through Windows media sessions. Without "session" it '
                    'acts on the session the overlay shows; "session" is an id from overlay_status. command "show" '
                    'only switches which session the overlay card shows.',
     'inputSchema': {'type': 'object', 'properties': {
         'command': {'type': 'string', 'enum': ['toggle', 'play', 'pause', 'next', 'prev', 'stop', 'show']},
         'session': {'type': 'string'}}, 'required': ['command']}},
    {'name': 'overlay_browser',
     'description': 'Bring a browser tab to the front (tab id from overlay_status), or open an http(s) page '
                    '(an already open tab with the same address is reused).',
     'inputSchema': {'type': 'object', 'properties': {
         'tab': {'type': 'integer'}, 'url': {'type': 'string'}}}},
    {'name': 'overlay_food',
     'description': 'Set the food order card by hand (when the browser extension cannot see it), or clear it. '
                    'step: 0 accepted, 1 cooking, 2 on the way, 3 delivered, -1 cancelled.',
     'inputSchema': {'type': 'object', 'properties': {
         'status': {'type': 'string'}, 'place': {'type': 'string'}, 'service': {'type': 'string'},
         'minutes': {'type': 'number', 'description': 'minutes until arrival'},
         'step': {'type': 'integer', 'minimum': -1, 'maximum': 3},
         'clear': {'type': 'boolean'}}}},
    {'name': 'overlay_layout',
     'description': 'Arrange the overlay. widget + visible/compact/x/y (x, y: 0..100 percent of the free room, '
                    '0 = left/top). Overlay-wide: enabled, screens ("hide" = not drawn in inventory and menus, '
                    '"behind" = left under their blur), opacity 0..100 (card background), reset (default places).',
     'inputSchema': {'type': 'object', 'properties': {
         'widget': {'type': 'string', 'enum': WIDGETS},
         'visible': {'type': 'boolean'}, 'compact': {'type': 'boolean'},
         'x': {'type': 'number', 'minimum': 0, 'maximum': 100}, 'y': {'type': 'number', 'minimum': 0, 'maximum': 100},
         'enabled': {'type': 'boolean'}, 'screens': {'type': 'string', 'enum': ['hide', 'behind']},
         'opacity': {'type': 'integer', 'minimum': 0, 'maximum': 100}, 'reset': {'type': 'boolean'}}}},
]


def game(path, body=None):
    try:
        conn = json.loads(CONNECTION.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'error': 'Overlaycraft has not run yet: start Minecraft with the mod'}
    request = urllib.request.Request(
        f"http://127.0.0.1:{conn.get('port', 18745)}{path}",
        data=None if body is None else json.dumps(body).encode(),
        headers={'Authorization': 'Bearer ' + conn.get('token', ''), 'Content-Type': 'application/json'},
        method='GET' if body is None else 'POST')
    try:
        with urllib.request.urlopen(request, timeout=10) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode('utf-8'))
        except ValueError:
            return {'error': f'HTTP {e.code}'}
    except OSError:
        return {'error': 'Minecraft with Overlaycraft is not running'}


def call(name, args):
    if name == 'overlay_status':
        return game('/api/state')
    if name == 'overlay_media':
        if args.get('command') == 'show':
            return game('/api/control', {'action': 'pick', 'session': args.get('session', '')})
        return game('/api/control', {'action': 'media', 'command': args.get('command', ''),
                                     'session': args.get('session', '')})
    if name == 'overlay_browser':
        if 'tab' in args:
            return game('/api/control', {'action': 'focus', 'tab': args['tab']})
        if args.get('url'):
            return game('/api/control', {'action': 'open', 'url': args['url']})
        return {'error': 'give tab or url'}
    if name == 'overlay_food':
        if args.get('clear'):
            return game('/api/control', {'action': 'food_clear'})
        return game('/api/control', {'action': 'food', **{k: v for k, v in args.items() if k != 'clear'}})
    if name == 'overlay_layout':
        result = None
        overlay = {k: args[k] for k in ('enabled', 'screens', 'opacity', 'reset') if k in args}
        if overlay:
            result = game('/api/control', {'action': 'overlay', **overlay})
        if args.get('widget'):
            widget = {k: args[k] for k in ('visible', 'compact', 'x', 'y') if k in args}
            result = game('/api/control', {'action': 'widget', 'name': args['widget'], **widget})
        return result or {'error': 'nothing to change'}
    return {'error': f'unknown tool {name}'}


def reply(msg_id, result=None, error=None):
    out = {'jsonrpc': '2.0', 'id': msg_id}
    if error is not None:
        out['error'] = error
    else:
        out['result'] = result
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + '\n')
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, msg_id = msg.get('method'), msg.get('id')
        if msg_id is None:
            continue  # notifications
        if method == 'initialize':
            version = (msg.get('params') or {}).get('protocolVersion', '2025-06-18')
            reply(msg_id, {'protocolVersion': version, 'capabilities': {'tools': {}},
                           'serverInfo': {'name': 'overlaycraft', 'version': '0.3.0'}})
        elif method == 'tools/list':
            reply(msg_id, {'tools': TOOLS})
        elif method == 'tools/call':
            params = msg.get('params') or {}
            result = call(params.get('name'), params.get('arguments') or {})
            failed = isinstance(result, dict) and 'error' in result and not result.get('ok')
            reply(msg_id, {'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False, indent=1)}],
                           'isError': failed})
        elif method == 'ping':
            reply(msg_id, {})
        else:
            reply(msg_id, error={'code': -32601, 'message': f'unknown method {method}'})


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stdin.reconfigure(encoding='utf-8')
    main()
