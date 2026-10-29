"""One queue item = one ACP prompt turn of Gemini CLI, shown with the shared UI.

Sessions: session/new the first time, session/load later when the agent supports
it (the id is kept in chat.sdk_id). Streaming answer → text_part lines, tool calls →
tool lines, plans → todo cards. Permission requests use the same cards as Claude;
file writes go through this client (fs/write_text_file), so /gemini undo works.
`f` messages cannot be added to a running ACP turn: they run next (TurnClosed).
"""
import asyncio
import base64
import json
from pathlib import Path

from gemini_acp import Agent, AcpError
from backends.sdk import TurnClosed
from backends import images
from backends.connectors import load_secrets


class GeminiTurn:
    def __init__(self, bridge, chat):
        self.bridge, self.chat = bridge, chat
        self.session = None
        self.stream = ''
        self.first = True
        self.accepting = True
        self.client = None
        self.tools = {}

    async def inject(self, text):
        raise TurnClosed()  # ACP has no "add to the running turn": the message runs next

    def emit_line(self, line):
        self.bridge.emit(self.chat, line, kind='text_part', first=self.first)
        self.first = False

    def flush(self, final=False):
        while '\n' in self.stream:
            line, self.stream = self.stream.split('\n', 1)
            self.emit_line(line)
        if final and self.stream:
            self.emit_line(self.stream)
            self.stream = ''

    def notify(self, method, params):
        if method == 'geminicraft/disconnected':
            return
        if method != 'session/update' or (self.session and params.get('sessionId') not in (None, self.session)):
            return
        update = params.get('update') or {}
        kind = update.get('sessionUpdate')
        if kind == 'agent_message_chunk':
            content = update.get('content') or {}
            if content.get('type') == 'text':
                self.stream += content.get('text', '')
                self.flush()
        elif kind == 'tool_call':
            self.flush(final=True)
            self.first = True
            tool_id = update.get('toolCallId', '')
            self.tools[tool_id] = update
            icon = {'read': '📄', 'edit': '✎', 'delete': '✖', 'search': '🔍', 'execute': '$', 'fetch': '◎'}.get(
                update.get('kind'), '◆')
            self.bridge.emit(self.chat, update.get('title') or update.get('kind') or 'tool', kind='tool', icon=icon,
                             tool=update.get('kind') or '')
        elif kind == 'tool_call_update' and update.get('status') == 'failed':
            text = ''
            for c in update.get('content') or []:
                inner = c.get('content') or {}
                text += inner.get('text', '') if isinstance(inner, dict) else ''
            self.bridge.emit(self.chat, (text or 'tool failed')[:1500], kind='tool_error')
        elif kind == 'plan':
            rows = [{'text': e.get('content', ''), 'status': {'in_progress': 'in_progress', 'completed': 'completed'}
                     .get(e.get('status'), 'pending')} for e in update.get('entries') or []]
            self.bridge.emit(self.chat, 'План', kind='todo', rows=rows)

    async def request(self, method, params):
        if method == 'session/request_permission':
            return await self.permission(params)
        if method == 'fs/read_text_file':
            path = Path(params.get('path', ''))
            text = path.read_text(encoding='utf-8', errors='replace')
            if params.get('line') or params.get('limit'):
                lines = text.split('\n')
                start = max(0, int(params.get('line') or 1) - 1)
                end = start + int(params['limit']) if params.get('limit') else None
                text = '\n'.join(lines[start:end])
            return {'content': text}
        if method == 'fs/write_text_file':
            path = Path(params.get('path', ''))
            if self.chat.mode != 'cc':
                raise AcpError('Writing files is not allowed in a /gem chat')
            self.bridge.checkpoints.remember(self.chat.id, path)   # /gemini undo
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(params.get('content', ''), encoding='utf-8')
            return None
        raise AcpError(f'Unsupported request {method}')

    async def permission(self, params):
        options = params.get('options') or []
        by_kind = {o.get('kind'): o.get('optionId') for o in options}
        call = params.get('toolCall') or {}
        tool_kind = call.get('kind') or (self.tools.get(call.get('toolCallId')) or {}).get('kind') or ''
        def choose(kind):
            option = by_kind.get(kind) or by_kind.get(kind.replace('_once', '_always')) or (options[0]['optionId'] if options else '')
            return {'outcome': {'outcome': 'selected', 'optionId': option}}
        if self.chat.mode == 'c':
            return choose('reject_once')
        if self.chat.auto == 'all' or (self.chat.auto == 'edits' and tool_kind in ('edit', 'read', 'search')):
            return choose('allow_once')
        title = call.get('title') or tool_kind or 'Gemini'
        details = json.dumps(call.get('rawInput') or call.get('content') or {}, ensure_ascii=False)[:4000]
        try:
            answer = await self.bridge.ask_user(self.chat, 'permission', f'{title}\n{details}',
                                                ['Разрешить', 'Отклонить', 'Всегда разрешать'], summary=title, tool=tool_kind)
        except TimeoutError:
            return choose('reject_once')
        return choose({'1': 'allow_once', '2': 'reject_once', '3': 'allow_always'}.get(answer, 'reject_once'))


def gemini_env(bridge):
    key = load_secrets(bridge.directory).get('gemini_api_key')
    return {'GEMINI_API_KEY': key} if key else {}


async def run(bridge, chat, text):
    turn = GeminiTurn(bridge, chat)
    folder = bridge.chat_cwd(chat)
    config = dict(bridge.config)
    if config.get('gemini_model'):
        config['gemini_args'] = list(config.get('gemini_args') or ['--experimental-acp']) + ['-m', config['gemini_model']]
    agent = Agent(config, turn.notify, turn.request, env=gemini_env(bridge), cwd=folder)
    turn.client = agent
    chat.live = turn
    bridge.checkpoints.begin(chat.id, text)
    try:
        await agent.start()
        if chat.sdk_id and agent.capabilities.get('loadSession'):
            try:
                await agent.call('session/load', {'sessionId': chat.sdk_id, 'cwd': folder, 'mcpServers': []}, timeout=120)
                turn.session = chat.sdk_id
            except AcpError:
                turn.session = None  # unknown or expired: start fresh
        if not turn.session:
            try:
                result = await agent.call('session/new', {'cwd': folder, 'mcpServers': []}, timeout=120)
            except AcpError as e:
                if 'auth' in str(e).lower():
                    raise ValueError('Нужен вход в Gemini: /geminicraft → «Войти через Google» или API-ключ') from e
                raise
            turn.session = chat.sdk_id = result['sessionId']
            bridge.persist()
        plain, paths = images.parse(text)
        prompt = [{'type': 'text', 'text': plain or text}]
        if paths and (agent.capabilities.get('promptCapabilities') or {}).get('image'):
            for p in paths:
                data = base64.b64encode(Path(p).read_bytes()).decode('ascii')
                prompt.insert(0, {'type': 'image', 'mimeType': images.TYPES.get(Path(p).suffix.lower(), 'image/png'),
                                  'data': data})
        try:
            result = await agent.call('session/prompt', {'sessionId': turn.session, 'prompt': prompt})
        except asyncio.CancelledError:
            try:
                await agent.notify('session/cancel', {'sessionId': turn.session})
            except Exception:
                pass
            raise
        turn.flush(final=True)
        if (result or {}).get('stopReason') == 'refusal':
            bridge.emit(chat, 'Gemini отказался выполнять запрос', kind='error')
    finally:
        turn.accepting = False
        if chat.live is turn:
            chat.live = None
        bridge.checkpoints.finish(chat.id)
        await agent.close()
        bridge.persist()
