"""A tiny ACP agent standing in for `gemini --experimental-acp` in tests.

Prompt text decides what it does:
  "write <path> <text>"  → asks permission (edit), then fs/write_text_file
  "read <path>"          → fs/read_text_file, answers with the content
  "plan"                 → sends a plan update
  anything else          → echoes "Gemini says: <text>" in two chunks
Sessions are remembered in a file next to this script, so session/load works across runs.
"""
import json
import sys
import uuid
from pathlib import Path

STORE = Path(__file__).with_name('fake_sessions.json')
pending = {}
counter = [100]


def send(m):
    sys.stdout.write(json.dumps(m) + '\n')
    sys.stdout.flush()


def ask(method, params):
    counter[0] += 1
    send({'jsonrpc': '2.0', 'id': counter[0], 'method': method, 'params': params})
    while True:
        m = json.loads(sys.stdin.readline())
        if m.get('id') == counter[0] and 'method' not in m:
            return m.get('result'), m.get('error')


def update(sid, u):
    send({'jsonrpc': '2.0', 'method': 'session/update', 'params': {'sessionId': sid, 'update': u}})


def sessions():
    try:
        return json.loads(STORE.read_text())
    except (OSError, ValueError):
        return {}


for raw in sys.stdin:
    if not raw.strip():
        continue
    m = json.loads(raw)
    method, p = m.get('method'), m.get('params') or {}
    if method == 'initialize':
        send({'jsonrpc': '2.0', 'id': m['id'], 'result': {'protocolVersion': 1, 'agentCapabilities': {'loadSession': True},
                                                         'authMethods': [{'id': 'oauth-personal', 'name': 'Google'}]}})
    elif method == 'authenticate':
        send({'jsonrpc': '2.0', 'id': m['id'], 'result': None})
    elif method == 'session/new':
        sid = 'sess-' + uuid.uuid4().hex[:6]
        data = sessions(); data[sid] = []; STORE.write_text(json.dumps(data))
        send({'jsonrpc': '2.0', 'id': m['id'], 'result': {'sessionId': sid}})
    elif method == 'session/load':
        if p['sessionId'] in sessions():
            send({'jsonrpc': '2.0', 'id': m['id'], 'result': None})
        else:
            send({'jsonrpc': '2.0', 'id': m['id'], 'error': {'code': -32602, 'message': 'unknown session'}})
    elif method == 'session/prompt':
        sid = p['sessionId']
        text = ' '.join(b.get('text', '') for b in p['prompt'] if b.get('type') == 'text')
        data = sessions(); data.setdefault(sid, []).append(text); STORE.write_text(json.dumps(data))
        if text.startswith('write '):
            _, path, content = text.split(' ', 2)
            update(sid, {'sessionUpdate': 'tool_call', 'toolCallId': 't1', 'title': f'Write {path}', 'kind': 'edit',
                         'status': 'pending'})
            result, _ = ask('session/request_permission', {'sessionId': sid, 'toolCall': {'toolCallId': 't1', 'title': f'Write {path}', 'kind': 'edit'},
                            'options': [{'optionId': 'yes', 'name': 'Allow', 'kind': 'allow_once'},
                                        {'optionId': 'no', 'name': 'Reject', 'kind': 'reject_once'}]})
            if result['outcome'].get('optionId') == 'yes':
                ask('fs/write_text_file', {'sessionId': sid, 'path': path, 'content': content})
                update(sid, {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': 'written\n'}})
            else:
                update(sid, {'sessionUpdate': 'tool_call_update', 'toolCallId': 't1', 'status': 'failed',
                             'content': [{'type': 'content', 'content': {'type': 'text', 'text': 'rejected by user'}}]})
        elif text.startswith('read '):
            result, _ = ask('fs/read_text_file', {'sessionId': sid, 'path': text[5:].strip()})
            update(sid, {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': 'content: ' + result['content'] + '\n'}})
        elif text == 'plan':
            update(sid, {'sessionUpdate': 'plan', 'entries': [{'content': 'step one', 'status': 'completed', 'priority': 'high'},
                                                              {'content': 'step two', 'status': 'pending', 'priority': 'low'}]})
        else:
            history = len(sessions()[sid])
            update(sid, {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': 'Gemini says: '}})
            update(sid, {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': f'{text} (turn {history})\n'}})
        send({'jsonrpc': '2.0', 'id': m['id'], 'result': {'stopReason': 'end_turn'}})
    elif 'id' in m:
        send({'jsonrpc': '2.0', 'id': m['id'], 'result': None})
