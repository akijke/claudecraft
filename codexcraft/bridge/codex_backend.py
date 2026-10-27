"""Map Codex threads, live steering and approval requests onto the shared UI."""
import asyncio
import json
from codex_rpc import AppServer, RpcError
from backends.sdk import TurnClosed


class CodexTurn:
    def __init__(self, bridge, chat):
        self.bridge, self.chat = bridge, chat
        self.rpc = AppServer(bridge.config, self.notify, self.request)
        self.ready = asyncio.Event()
        self.finished = asyncio.Event()
        self.accepting = True
        self.turn_id = None
        self.error = None
        self.stream = {}
        self.streamed = set()
        self.first = set()
        self.answers = []
        self.steer_lock = asyncio.Lock()
        self.usage = None
        self.items = {}

    def flush(self, item_id, final=False):
        text = self.stream.get(item_id, '')
        while '\n' in text:
            line, text = text.split('\n', 1)
            self.bridge.emit(self.chat, line, kind='text_part', first=item_id not in self.first)
            self.first.add(item_id)
        if final and text:
            self.bridge.emit(self.chat, text, kind='text_part', first=item_id not in self.first)
            self.first.add(item_id)
            text = ''
        self.stream[item_id] = text

    def notify(self, method, data):
        if data.get('threadId') and self.chat.sdk_id and data['threadId'] != self.chat.sdk_id:
            return
        if method == 'turn/started':
            self.turn_id = data['turn']['id']
            self.ready.set()
        elif method == 'item/agentMessage/delta':
            key = data['itemId']
            self.streamed.add(key)
            self.stream[key] = self.stream.get(key, '') + data.get('delta', '')
            self.flush(key)
        elif method == 'item/started':
            item = data.get('item', {})
            if item.get('id'):
                self.items[item['id']] = item
            kind = item.get('type')
            if kind == 'commandExecution':
                self.bridge.emit(self.chat, item.get('command', ''), kind='tool', tool='Bash', icon='$')
            elif kind == 'fileChange':
                paths = ', '.join(c.get('path', '') for c in item.get('changes', []))
                self.bridge.emit(self.chat, paths or 'Изменение файлов', kind='tool', tool='Edit', icon='✎')
            elif kind == 'mcpToolCall':
                self.bridge.emit(self.chat, f"{item.get('server', '')}: {item.get('tool', '')}", kind='tool', icon='◆')
        elif method == 'item/completed':
            item = data.get('item', {})
            if item.get('type') == 'agentMessage':
                key = item['id']
                if key in self.streamed:
                    self.flush(key, final=True)
                else:
                    self.bridge.emit(self.chat, item.get('text', ''), kind='text')
                self.answers.append(item.get('text', ''))
            elif item.get('type') in ('commandExecution', 'fileChange', 'mcpToolCall'):
                if item.get('status') in ('failed', 'declined'):
                    self.bridge.emit(self.chat, str(item.get('aggregatedOutput') or item.get('error') or
                                                    item['status'])[:3000], kind='tool_error')
        elif method == 'turn/plan/updated':
            self.bridge.emit(self.chat, data.get('explanation') or 'План', kind='todo',
                rows=[{'text': s['step'], 'status': s['status']} for s in data.get('plan', [])])
        elif method == 'thread/tokenUsage/updated':
            self.usage = data.get('tokenUsage', {}).get('last')
        elif method == 'error' and not data.get('willRetry'):
            self.error = data.get('error', {}).get('message', 'Codex error')
        elif method in ('turn/completed', 'codexcraft/disconnected'):
            self.accepting = False
            self.ready.set()
            if method == 'codexcraft/disconnected':
                self.error = data.get('message', 'Codex disconnected')
            else:
                turn = data.get('turn', {})
                if turn.get('status') == 'failed':
                    self.error = (turn.get('error') or {}).get('message', 'Codex turn failed')
            self.finished.set()

    async def inject(self, text):
        await self.ready.wait()
        async with self.steer_lock:
            if not self.accepting or not self.turn_id:
                raise TurnClosed()
            try:
                await self.rpc.call('turn/steer', {'threadId': self.chat.sdk_id,
                    'expectedTurnId': self.turn_id, 'input': [{'type': 'text', 'text': text}]})
            except RpcError as exc:
                # A completion that wins this race is queued by Bridge.deliver.
                if not self.accepting or 'no active turn' in str(exc).lower() or 'not active' in str(exc).lower():
                    raise TurnClosed() from exc
                raise

    async def request(self, method, params):
        if params.get('threadId') != self.chat.sdk_id or not self.accepting:
            raise RpcError('Request for an inactive thread')
        if method in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval'):
            # GPT mode never escalates to writes or command execution.
            if self.chat.mode == 'c':
                return {'decision': 'decline'}
            if self.chat.auto == 'all' or (self.chat.auto == 'edits' and method.startswith('item/fileChange/')):
                return {'decision': 'accept'}
            item = self.items.get(params.get('itemId'), {})
            detail = params.get('command') or params.get('reason') or params.get('grantRoot') or 'Изменение файлов'
            if params.get('cwd'):
                detail += '\ncwd: ' + params['cwd']
            if item.get('changes'):
                detail += '\n' + json.dumps(item['changes'], ensure_ascii=False, indent=2)
            try:
                answer = await self.bridge.ask_user(self.chat, 'permission', str(detail),
                    choices=['Разрешить', 'Отклонить'], tool='Codex',
                    input=json.dumps(params, ensure_ascii=False))
            except TimeoutError:
                answer = '2'
            return {'decision': 'accept' if answer == '1' else 'decline'}
        if method in ('item/tool/requestUserInput', 'tool/requestUserInput'):
            answers = {}
            for question in params.get('questions', []):
                options = [o['label'] for o in question.get('options', [])]
                answer = await self.bridge.ask_user(self.chat, 'question', question['question'], choices=options)
                answers[question['id']] = {'answers': [answer]}
            return {'answers': answers}
        # Permissions, dynamic tools and elicitation must never be silently approved.
        if method == 'item/permissions/requestApproval':
            return {'permissions': {}}
        raise RpcError('Unsupported Codex request: ' + method)

    async def drive(self, text):
        chat, bridge = self.chat, self.bridge
        folder = bridge.chat_cwd(chat)
        try:
            await self.rpc.start()
            params = {'cwd': folder, 'approvalPolicy': 'on-request' if chat.mode == 'cc' else 'never',
                'sandbox': 'workspace-write' if chat.mode == 'cc' and chat.auto != 'off' else 'read-only',
                'developerInstructions': ('You are Codex inside Minecraft. Reply in the user language. '
                    + ('Help develop the current project. ' if chat.mode == 'cc' else
                       'Conversation mode: answer questions only. Do not use tools or change files. '))}
            if bridge.config.get('codex_model'):
                params['model'] = bridge.config['codex_model']
            if chat.mode == 'cc' and bridge.extensions:
                servers = {}
                for name, server in bridge.extensions.items():
                    if server.get('type') in ('http', 'sse'):
                        servers[name] = {'url': server['url'], 'http_headers': server.get('headers', {})}
                    else:
                        servers[name] = {k: v for k, v in server.items() if k != 'type'}
                params['config'] = {'mcp_servers': servers}
            method = 'thread/resume' if chat.sdk_id else 'thread/start'
            if chat.sdk_id:
                params['threadId'] = chat.sdk_id
            result = await self.rpc.call(method, params)
            chat.sdk_id = result['thread']['id']
            chat.session_cwd = folder
            bridge.persist()
            result = await self.rpc.call('turn/start', {'threadId': chat.sdk_id,
                'input': [{'type': 'text', 'text': text}]})
            self.turn_id = result['turn']['id']
            self.ready.set()
            await self.finished.wait()
            # Serialize against any accepted steer still returning its acknowledgement.
            async with self.steer_lock:
                pass
            for key in self.stream:
                self.flush(key, final=True)
            if self.error:
                raise ValueError(self.error)
            if self.usage:
                bridge.add_usage(chat, {'input_tokens': self.usage.get('inputTokens', 0),
                                        'output_tokens': self.usage.get('outputTokens', 0)})
            chat.messages = (chat.messages + [{'role': 'user', 'content': text},
                {'role': 'assistant', 'content': '\n'.join(self.answers)}])[-40:]
        except asyncio.CancelledError:
            self.accepting = False
            if self.turn_id and self.rpc.process and self.rpc.process.returncode is None:
                try:
                    await self.rpc.call('turn/interrupt', {'threadId': chat.sdk_id,
                                                         'turnId': self.turn_id}, timeout=5)
                except (ValueError, OSError, TimeoutError):
                    pass
            raise
        finally:
            self.accepting = False
            self.ready.set()
            await self.rpc.close()


async def run(bridge, chat, text):
    turn = CodexTurn(bridge, chat)
    chat.live = turn
    try:
        await turn.drive(text)
    finally:
        if chat.live is turn:
            chat.live = None
        bridge.persist()
