"""Other models for /c conversations through OpenAI-compatible chat APIs.

Providers:
  ollama     — local, http://localhost:11434/v1 (no key, works offline)
  lmstudio   — local, http://localhost:1234/v1 (no key, works offline)
  openrouter — https://openrouter.ai/api/v1, any model, key in secrets.json
Base URLs can be changed in bridge-config.json → "providers": {"ollama": {"url": …}}.
Answers stream line by line like Claude's; the history (last 40 messages) lives in
the chat, as for Messages API chats. These chats are conversation only — no tools.
"""
import json

from aiohttp import ClientSession, ClientTimeout

from backends.connectors import load_secrets, store_secrets

PROVIDERS = {
    'ollama': {'url': 'http://localhost:11434/v1', 'key': None, 'default': 'llama3.2'},
    'lmstudio': {'url': 'http://localhost:1234/v1', 'key': None, 'default': ''},
    'openrouter': {'url': 'https://openrouter.ai/api/v1', 'key': 'openrouter_api_key', 'default': 'openrouter/auto'},
}


def provider(bridge, name):
    if name not in PROVIDERS:
        raise ValueError('Провайдер: ' + ', '.join(PROVIDERS))
    info = dict(PROVIDERS[name])
    override = (bridge.config.get('providers') or {}).get(name, {})
    url = override.get('url') if isinstance(override, dict) else None
    if isinstance(url, str) and url:
        local = url.startswith(('http://localhost', 'http://127.0.0.1', 'http://[::1]'))
        if not (local or url.startswith('https://')):  # a key never goes over plain http to another host
            raise ValueError(f'{name}: адрес — https:// или http://localhost')
        info['url'] = url.rstrip('/')
    if isinstance(override, dict) and override.get('default'):
        info['default'] = str(override['default'])
    info['name'] = name
    return info


def api_key(bridge, info):
    if not info.get('key'):
        return None
    key = load_secrets(bridge.directory).get(info['key'])
    if not key:
        raise ValueError(f"Нет ключа {info['name']}: /c {info['name']}-key <ключ> (openrouter.ai/keys)")
    return key


def set_key(bridge, name, key):
    info = provider(bridge, name)
    if not info.get('key'):
        raise ValueError(f'{name} работает без ключа')
    data = load_secrets(bridge.directory)
    if key.strip():
        data[info['key']] = key.strip()
    else:
        data.pop(info['key'], None)
    store_secrets(bridge.directory, data)


def headers(bridge, info):
    h = {'Content-Type': 'application/json'}
    key = api_key(bridge, info)
    if key:
        h['Authorization'] = 'Bearer ' + key
    if info['name'] == 'openrouter':
        h['X-Title'] = 'Claudecraft'
        h['HTTP-Referer'] = 'https://github.com/akijke/claudecraft'
    return h


async def models(bridge, name):
    info = provider(bridge, name)
    try:
        async with ClientSession(timeout=ClientTimeout(total=15)) as session:
            async with session.get(info['url'] + '/models', headers=headers(bridge, info)) as r:
                data = await r.json(content_type=None)
                if r.status != 200:
                    raise ValueError(f'{name}: HTTP {r.status}')
    except OSError as e:
        raise ValueError(f'{name} не отвечает ({info["url"]}) — запущен ли он?' if name != 'openrouter'
                         else f'openrouter: {e}') from None
    items = data.get('data', []) if isinstance(data, dict) else []
    return [{'id': m.get('id', ''), 'name': m.get('name') or m.get('id', ''),
             'context': m.get('context_length')} for m in items][:200]


async def run(bridge, chat, text):
    info = provider(bridge, chat.backend)
    model = chat.model or info['default']
    if not model:
        raise ValueError(f'Выбери модель: /c models {chat.backend}')
    messages = [{'role': 'system', 'content': 'Answer in the language the user writes in. Be concise; use markdown.'}]
    messages += chat.messages[-40:] + [{'role': 'user', 'content': text}]
    body = {'model': model, 'messages': messages, 'stream': True, 'stream_options': {'include_usage': True}}
    answer, buffer, first, usage = '', '', True, None

    def emit_line(line):
        nonlocal first
        bridge.emit(chat, line, kind='text_part', first=first)
        first = False

    try:
        async with ClientSession(timeout=ClientTimeout(total=600, sock_read=300)) as session:
            async with session.post(info['url'] + '/chat/completions', headers=headers(bridge, info), json=body) as r:
                if r.status != 200:
                    detail = (await r.text())[:400]
                    raise ValueError(f'{chat.backend} HTTP {r.status}: {detail}')
                async for raw in r.content:
                    line = raw.decode('utf-8', 'replace').strip()
                    if not line.startswith('data:'):
                        continue
                    payload = line[5:].strip()
                    if payload == '[DONE]':
                        break
                    try:
                        data = json.loads(payload)
                    except ValueError:
                        continue
                    if data.get('usage'):
                        usage = data['usage']
                    for choice in data.get('choices') or []:
                        piece = (choice.get('delta') or {}).get('content') or ''
                        answer += piece
                        buffer += piece
                        while '\n' in buffer:
                            out, buffer = buffer.split('\n', 1)
                            emit_line(out)
    except OSError as e:
        raise ValueError(f'{chat.backend} не отвечает ({info["url"]}): {e}') from None
    if buffer:
        emit_line(buffer)
    if not answer:
        raise ValueError(f'{chat.backend}: пустой ответ модели {model}')
    if usage:
        bridge.add_usage(chat, {'input_tokens': usage.get('prompt_tokens', 0),
                                'output_tokens': usage.get('completion_tokens', 0)},
                         float(usage.get('cost') or 0.0), model)
    chat.messages = (chat.messages + [{'role': 'user', 'content': text}, {'role': 'assistant', 'content': answer}])[-40:]
