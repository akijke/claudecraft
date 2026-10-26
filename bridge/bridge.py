"""Authenticated loopback bridge. Run with --game-dir pointing at the launcher instance."""
import argparse
import asyncio
from collections import deque
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import secrets
import shutil
import platform
import re
import time
import uuid
from aiohttp import web
from backends.account import Account


def uid():
    return uuid.uuid4().hex


def save_json(path, data):
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as f:
        os.chmod(temp, 0o600)
        json.dump(data, f, ensure_ascii=False, indent=2)
    temp.replace(path)


@dataclass
class Chat:
    id: str
    mode: str
    backend: str
    title: str
    sdk_id: str | None = None
    messages: list = field(default_factory=list)
    cwd: str | None = None           # `cd` folder; None = workspace
    session_cwd: str | None = None   # folder the SDK session was created in
    auto: str = 'off'                # auto-approve: off | edits | all
    allowed: list = field(default_factory=list)  # tools approved with "always"
    usage: dict = field(default_factory=dict)    # token/cost totals for /c tokens
    run_usage: dict = field(default_factory=dict)  # current queue item (not saved)
    queue: deque = field(default_factory=deque)
    task: asyncio.Task | None = None
    # Live backend session of the running item (SdkTurn); receives `f` messages.
    live: object = None
    deliveries: set = field(default_factory=set)


class Bridge:
    def __init__(self, directory, runner=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.workspace = self.directory / 'workspace'
        self.workspace.mkdir(exist_ok=True)
        config_path = self.directory / 'bridge-config.json'
        if not config_path.exists():
            save_json(config_path, {'workspace': str(self.workspace.resolve()),
                                   'api_model': 'claude-sonnet-4-6', 'mcp_servers': {}})
        self.config = json.loads(config_path.read_text(encoding='utf-8'))
        self.workspace = Path(os.environ.get('MC_WORKSPACE', self.config['workspace'])).expanduser().resolve()
        if not self.workspace.is_dir():
            raise ValueError('workspace must be an existing directory')
        self.extensions = {}
        self.reload_extensions()
        self.chats = {}
        self.selected = {}
        self.pending = {}
        self.events = deque(maxlen=2000)
        self.sequence = 0
        self.boot = uid()  # lets clients notice a bridge restart (event numbering resets)
        self.lock = asyncio.Lock()
        self.runner = runner or self.run_backend
        self.account = Account(self)
        state = self.directory / 'chats.json'
        if state.exists():
            data = json.loads(state.read_text(encoding='utf-8'))
            for row in data['chats']:
                chat = Chat(**row)
                self.chats[chat.id] = chat
            self.selected = data['selected']
        for mode in ('c', 'cc'):
            if self.selected.get(mode) not in self.chats:
                self.new_chat(mode, 'local')

    def reload_extensions(self):
        """Validate a full snapshot; active turns keep their original MCP processes."""
        config = json.loads((self.directory / 'bridge-config.json').read_text(encoding='utf-8'))
        workspace = Path(os.environ.get('MC_WORKSPACE', config['workspace'])).expanduser().resolve()
        if workspace != self.workspace:
            raise ValueError('Changing workspace requires a bridge restart')
        extension_dir = self.workspace / 'extensions'
        extension_dir.mkdir(exist_ok=True)
        loaded = dict(config.get('mcp_servers', {}))
        if 'minecraft' in loaded:
            raise ValueError('MCP name minecraft is reserved')
        for manifest in sorted(extension_dir.glob('*/manifest.json')):
            data = json.loads(manifest.read_text(encoding='utf-8'))
            if not data.get('enabled', True):
                continue
            name = data.get('id', '')
            if not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}', name):
                raise ValueError(f'Invalid extension id in {manifest}')
            if name in loaded or name == 'minecraft':
                raise ValueError(f'Duplicate or reserved MCP name: {name}')
            server = dict(data['server'])
            if not isinstance(server.get('command'), str) or not server['command']:
                raise ValueError(f'{name}: command is required')
            if not isinstance(server.get('args', []), list) or not all(
                    isinstance(arg, str) for arg in server.get('args', [])):
                raise ValueError(f'{name}: args must be a string list')
            server['args'] = [arg.replace('${extension}', str(manifest.parent.resolve()))
                              .replace('${workspace}', str(self.workspace)) for arg in server.get('args', [])]
            loaded[name] = server
        self.extensions = loaded
        self.config = config
        return sorted(loaded)

    def capabilities(self):
        admin = False
        if os.name == 'nt':
            import ctypes
            admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        elif hasattr(os, 'geteuid'):
            admin = os.geteuid() == 0
        return {'platform': platform.system(), 'admin': admin,
                'workspace': str(self.workspace),
                'executables': {name: bool(shutil.which(name)) for name in
                                ('docker', 'ssh', 'bash', 'cmd.exe', 'powershell.exe', 'pwsh')},
                'extensions': sorted(self.extensions)}

    def persist(self):
        rows = [{k: getattr(c, k) for k in ('id', 'mode', 'backend', 'title', 'sdk_id', 'messages',
                                            'cwd', 'session_cwd', 'auto', 'allowed', 'usage')}
                for c in self.chats.values()]
        save_json(self.directory / 'chats.json', {'chats': rows, 'selected': self.selected})

    def emit(self, chat, text='', **extra):
        self.sequence += 1
        event = {'seq': self.sequence, 'mode': chat.mode, 'chat': chat.id, 'title': chat.title[:32],
                 'text': text[:24000], **extra}
        self.events.append(event)
        return event

    def new_chat(self, mode, backend):
        if mode not in ('c', 'cc') or backend not in ('local', 'api'):
            raise ValueError('Unknown mode/backend')
        if mode == 'cc' and backend == 'api':
            raise ValueError('/cc uses Agent SDK; API chat belongs to /c')
        chat = Chat(uid(), mode, backend, 'Новый чат')
        self.chats[chat.id] = chat
        self.selected[mode] = chat.id
        self.persist()
        return chat

    async def stop(self, chat, clear=True):
        if clear:
            chat.queue.clear()
        for delivery in list(chat.deliveries):
            delivery.cancel()
        chat.deliveries.clear()
        for key, p in list(self.pending.items()):
            if p['chat'] == chat.id:
                if not p['future'].done():
                    p['future'].cancel()
                self.pending.pop(key, None)
        if chat.task and not chat.task.done():
            chat.task.cancel()
            # The SDK is connected, consumed and disconnected in its owning task.
            try:
                await chat.task
            except asyncio.CancelledError:
                pass
        chat.task = None

    async def pump(self, chat):
        try:
            while chat.queue:
                question = chat.queue.popleft()
                self.emit(chat, 'Думаю…', kind='thinking', queued=len(chat.queue))
                chat.run_usage = {}
                started = time.monotonic()
                try:
                    await self.runner(chat, question)
                    self.persist()
                    u = chat.run_usage
                    self.emit(chat, 'Готово', kind='done', seconds=round(time.monotonic() - started, 1),
                              input=u.get('input_tokens', 0) + u.get('cache_read_input_tokens', 0)
                              + u.get('cache_creation_input_tokens', 0),
                              output=u.get('output_tokens', 0), cost=round(u.get('cost_usd', 0.0), 4))
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    message = 'Ошибка: ' + str(e)[:1500]
                    if re.search(r'auth|login|credential|401|api key', str(e), re.I):
                        message += '\nПроверь вход в аккаунт Claude: /c settings'
                    self.emit(chat, message, kind='error')
        except asyncio.CancelledError:
            self.emit(chat, 'Текущий запрос прерван.', kind='status')
        finally:
            chat.task = None

    def add_usage(self, chat, usage, cost=0.0):
        """Add one turn's tokens (and cost delta) to the chat totals and the running item."""
        for bucket in (chat.usage, chat.run_usage):
            for key in USAGE_KEYS:
                bucket[key] = bucket.get(key, 0) + int((usage or {}).get(key) or 0)
            bucket['cost_usd'] = bucket.get('cost_usd', 0.0) + float(cost or 0.0)
        chat.usage['turns'] = chat.usage.get('turns', 0) + 1

    def tokens_report(self, chat):
        def row(label, u):
            total_in = sum(u.get(k, 0) for k in ('input_tokens', 'cache_read_input_tokens',
                                                  'cache_creation_input_tokens'))
            return {'label': label, 'input': total_in, 'output': u.get('output_tokens', 0),
                    'cache': u.get('cache_read_input_tokens', 0), 'cost': round(u.get('cost_usd', 0.0), 4),
                    'turns': u.get('turns', 0)}
        everything = {}
        for c in self.chats.values():
            for key, value in c.usage.items():
                everything[key] = everything.get(key, 0) + value
        rows = [row('Этот чат: ' + chat.title[:40], chat.usage), row('Все чаты', everything)]
        note = ('Стоимость — оценка Claude Code по ценам API; на подписке списывается лимит, '
                'а не деньги.' if chat.backend == 'local' else 'Токены из ответов Messages API.')
        self.emit(chat, 'Токены', kind='tokens', rows=rows, note=note)

    def chat_cwd(self, chat):
        folder = Path(chat.cwd) if chat.cwd else self.workspace
        return str(folder if folder.is_dir() else self.workspace)

    def resolve(self, chat, arg):
        """Path for ls/cd/cat: relative to the chat folder; ~ is the workspace."""
        arg = (arg or '').strip().strip('"')
        base = Path(self.chat_cwd(chat))
        if not arg:
            return base
        if arg == '~' or arg.startswith(('~/', '~\\')):
            return (self.workspace / arg[2:]).resolve()
        path = Path(arg)
        return (path if path.is_absolute() else base / path).resolve()

    def ls(self, chat, arg):
        folder = self.resolve(chat, arg)
        if not folder.is_dir():
            raise ValueError(f'Нет такой папки: {folder}')
        rows = []
        try:
            entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            raise ValueError(f'Нет доступа: {folder}') from None
        for entry in entries[:200]:
            try:
                is_dir = entry.is_dir()
                size = 0 if is_dir else entry.stat().st_size
            except OSError:
                continue
            rows.append({'name': entry.name, 'dir': is_dir, 'size': size})
        self.emit(chat, str(folder), kind='listing', path=str(folder), rows=rows,
                  more=max(0, len(entries) - 200), parent=str(folder.parent) if folder.parent != folder else None)

    def cd(self, chat, arg):
        folder = self.resolve(chat, arg or '~')
        if not folder.is_dir():
            raise ValueError(f'Нет такой папки: {folder}')
        chat.cwd = None if folder == self.workspace else str(folder)
        self.persist()
        note = ''
        if chat.sdk_id and chat.session_cwd and Path(chat.session_cwd) != folder:
            note = 'Сессия Claude остаётся в своей папке; эта папка будет передана ему как рабочая.'
        self.emit(chat, str(folder), kind='cwd', note=note)

    def cat(self, chat, arg):
        if not (arg or '').strip():
            raise ValueError('Формат: cat <файл>')
        path = self.resolve(chat, arg)
        if not path.is_file():
            raise ValueError(f'Нет такого файла: {path}')
        with path.open('rb') as f:
            raw = f.read(40000)
        if b'\0' in raw[:4000]:
            raise ValueError('Это двоичный файл — не показываю')
        text = raw.decode('utf-8', 'replace')
        lines = text.splitlines()
        truncated = len(lines) > 150 or path.stat().st_size > len(raw)
        self.emit(chat, '\n'.join(lines[:150]), kind='file', path=str(path), truncated=truncated)

    @staticmethod
    def check_text(text):
        if not isinstance(text, str) or not text.strip() or len(text) > 8000:
            raise ValueError('Question must contain 1..8000 characters')

    def start(self, chat, text, first=False):
        if len(chat.queue) >= 20:
            raise ValueError('Queue is full (20)')
        if first:
            chat.queue.appendleft(text)
        else:
            chat.queue.append(text)
        if chat.title == 'Новый чат':
            chat.title = text[:60]
            self.persist()
        if chat.task is None:
            chat.task = asyncio.create_task(self.pump(chat))
            return True
        return False

    async def enqueue(self, chat, text):
        """q: run after everything already running/queued in this chat."""
        self.check_text(text)
        if not self.start(chat, text):
            self.emit(chat, f'В очереди: {len(chat.queue)}', kind='status')

    async def force(self, chat, text):
        """f: add a message to the work in progress. Never cancels or restarts it."""
        self.check_text(text)
        if chat.task is None:
            self.start(chat, text)
            return 'started'
        if chat.backend == 'api':
            raise ValueError('Messages API: уже отправленный HTTP-запрос нельзя дополнить сообщением. '
                             'Текущий ответ НЕ отменён и продолжается. Используй q (очередь) '
                             'или cancel, затем новый запрос. Для f выбери локальный чат: /c new local.')
        live = chat.live
        if live is None:
            # Between two queue items: nothing to inject into, run it right next.
            self.start(chat, text, first=True)
            self.emit(chat, 'Сейчас нет живой сессии для f: сообщение выполнится следующим, '
                      'без отмены текущей работы.', kind='status')
            return 'next'
        # Delivery may wait for the session to start; do not hold the bridge lock meanwhile.
        delivery = asyncio.create_task(self.deliver(chat, live, text))
        chat.deliveries.add(delivery)
        delivery.add_done_callback(chat.deliveries.discard)
        return 'sending'

    async def deliver(self, chat, live, text):
        from backends.sdk import TurnClosed
        try:
            await live.inject(text)
            self.emit(chat, 'Сообщение передано Claude, работа продолжается.', kind='status')
        except TurnClosed:
            # Lost the race with ResultMessage: the answer was already final.
            try:
                self.start(chat, text, first=True)
                self.emit(chat, 'Claude уже закончил ответ до получения f: сообщение '
                          'выполнится следующим запросом в этой же сессии.', kind='status')
            except ValueError as e:
                self.emit(chat, 'f не доставлено: ' + str(e), kind='error')
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.emit(chat, 'f не доставлено: ' + str(e)[:1500], kind='error')

    async def ask_user(self, chat, kind, text, choices=None, multiple=False, **extra):
        request = uid()
        future = asyncio.get_running_loop().create_future()
        options = choices or ['Разрешить', 'Отклонить']
        self.pending[request] = {'chat': chat.id, 'future': future, 'choices': options,
                                 'multiple': multiple, 'kind': kind}
        event = self.emit(chat, text, kind=kind, request=request,
                          choices=options, multiple=multiple, **extra)
        self.pending[request]['event'] = event
        try:
            return await asyncio.wait_for(future, 600)
        finally:
            self.pending.pop(request, None)

    def answer(self, chat, request, answer):
        pending = self.pending.get(request)
        if not pending or pending['chat'] != chat.id or pending['future'].done():
            raise ValueError('This question has expired or belongs to another chat')
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 8000:
            raise ValueError('Empty or oversized answer')
        if pending['kind'] == 'permission' and answer not in [str(i + 1) for i in range(len(pending['choices']))]:
            raise ValueError('Permission requires a button number (1 allow, 2 deny, 3 always)')
        value = answer
        if pending['kind'] == 'question':
            parts = answer.split(',')
            if all(p.strip().isdigit() for p in parts):
                indexes = [int(p.strip()) - 1 for p in parts]
                if any(i < 0 or i >= len(pending['choices']) for i in indexes):
                    raise ValueError('Invalid option number')
                if len(indexes) > 1 and not pending['multiple']:
                    raise ValueError('Select one option')
                value = ', '.join(pending['choices'][i] for i in indexes)
        pending['future'].set_result(value)

    async def run_backend(self, chat, text):
        if chat.backend == 'api':
            await self.run_api(chat, text)
        else:
            await self.run_sdk(chat, text)

    async def run_api(self, chat, text):
        from backends.api import run
        await run(self, chat, text)

    async def run_sdk(self, chat, text):
        from backends.sdk import run
        await run(self, chat, text)

    def chat_menu(self, mode):
        chat = self.chats[self.selected[mode]]
        rows = [{'id': c.id, 'label': f'{c.backend}: {c.title}', 'selected': c.id == chat.id}
                for c in self.chats.values() if c.mode == mode]
        self.emit(chat, 'Выбери чат:', kind='chats', rows=rows)

    async def command(self, data):
        mode = data.get('mode')
        if mode not in ('c', 'cc'):
            raise ValueError('Invalid mode')
        action = data.get('action')
        chat = self.chats[self.selected[mode]]
        action = ALIASES.get(action, action)
        if action == 'q':
            await self.enqueue(chat, data.get('text', ''))
        elif action == 'f':
            await self.force(chat, data.get('text', ''))
        elif action in ('cancel', 'restart'):
            await self.stop(chat)
            self.emit(chat, 'Остановлено; очередь очищена. История сохранена.', kind='status')
        elif action == 'disconnect':
            for item in list(self.chats.values()):
                await self.stop(item)
        elif action == 'reload':
            names = self.reload_extensions()
            self.emit(chat, 'Расширения перезагружены для следующих запросов: ' + ', '.join(names), kind='status')
        elif action in ('tools', 'capabilities'):
            self.emit(chat, json.dumps(self.capabilities(), ensure_ascii=False, indent=2), kind='status')
        elif action == 'tokens':
            self.tokens_report(chat)
        elif action == 'auto':
            value = (data.get('text') or '').strip().lower()
            if value:
                if value not in AUTO_MODES:
                    raise ValueError('auto: off | edits | all')
                chat.auto = value
                if value == 'off':
                    chat.allowed.clear()
                self.persist()
            self.emit(chat, AUTO_MODES[chat.auto], kind='auto', mode_value=chat.auto,
                      allowed=list(chat.allowed))
        elif action == 'ls':
            self.ls(chat, data.get('text', ''))
        elif action == 'cd':
            self.cd(chat, data.get('text', ''))
        elif action == 'pwd':
            self.emit(chat, self.chat_cwd(chat), kind='cwd', note='')
        elif action == 'cat':
            self.cat(chat, data.get('text', ''))
        elif action == 'clearqueue':
            chat.queue.clear()
            self.emit(chat, 'Очередь очищена.', kind='status')
        elif action == 'status':
            state = 'ожидание'
            if chat.task:
                state = 'работает' + (', f доступно' if chat.live is not None else '')
            self.emit(chat, f'{chat.title} | {chat.backend} | {state} | очередь {len(chat.queue)} | '
                      f'auto {chat.auto} | {self.chat_cwd(chat)}', kind='status')
        elif action == 'new':
            chat = self.new_chat(mode, data.get('text') or 'local')
            self.emit(chat, 'Новый чат создан.', kind='status')
            self.chat_menu(mode)
        elif action == 'select':
            target = self.chats.get(data.get('text'))
            if not target or target.mode != mode:
                raise ValueError('Unknown chat')
            self.selected[mode] = target.id
            self.persist()
            self.chat_menu(mode)
        elif action == 'chat':
            self.chat_menu(mode)
        elif action == 'rename':
            name = data.get('text', '').strip()
            if not name or len(name) > 80:
                raise ValueError('Name must contain 1..80 characters')
            chat.title = name
            self.persist()
        elif action == 'reply':
            # Request IDs determine chat ownership, so old buttons never approve another tool.
            request = data.get('request')
            p = self.pending.get(request)
            if not p or self.chats[p['chat']].mode != mode:
                raise ValueError('Expired question')
            self.answer(self.chats[p['chat']], request, data.get('text', ''))
        elif action == 'pending':
            for p in self.pending.values():
                if self.chats[p['chat']].mode == mode:
                    event = dict(p['event'])
                    event.pop('seq', None)
                    event.pop('text', None)
                    event.pop('chat', None)
                    event.pop('mode', None)
                    self.emit(self.chats[p['chat']], p['event']['text'], **event)
        elif action == 'login':
            await self.account.login(console=data.get('text') == 'console')
        elif action == 'login_code':
            await self.account.submit_code(data.get('text', ''))
        elif action == 'login_cancel':
            await self.account.cancel_login()
        elif action == 'logout':
            await self.account.logout()
        elif action == 'apikey':
            self.account.set_api_key(data.get('text', ''))
        elif action == 'local':
            if mode != 'cc':
                raise ValueError('Use /cc local to import CLI coding sessions')
            from claude_agent_sdk import list_sessions
            sessions = await asyncio.to_thread(list_sessions, directory=str(self.workspace), limit=30)
            known = {c.sdk_id for c in self.chats.values()}
            for session in sessions:
                if session.session_id not in known:
                    imported = Chat(uid(), 'cc', 'local', session.summary or session.session_id[:8], session.session_id,
                                    session_cwd=str(self.workspace))
                    self.chats[imported.id] = imported
                    known.add(session.session_id)
            self.persist()
            self.chat_menu(mode)
        else:
            raise ValueError('Unknown action')
        return {'ok': True}

    async def close(self):
        await self.account.cancel_login()
        for chat in self.chats.values():
            await self.stop(chat)
        self.persist()


# Short and legacy command names → canonical actions (full names keep working).
ALIASES = {'queue': 'q', 'force': 'f', 'c': 'chat', 'chats': 'chat', 'dir': 'ls', 'type': 'cat',
           'usage': 'tokens'}
USAGE_KEYS = ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')
AUTO_MODES = {
    'off': 'Авторазрешение выключено: каждое действие спрашивает подтверждение.',
    'edits': 'Авторазрешение: чтение, поиск и правка файлов без вопросов; команды (Bash) и интернет спрашивают.',
    'all': 'Авторазрешение ВСЕГО: Claude выполняет любые команды без вопросов, с правами моста. Будь осторожен.',
}


def create_app(bridge, token):
    @web.middleware
    async def authenticate(request, handler):
        if not secrets.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + token):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        if request.headers.get('Origin'):
            return web.json_response({'error': 'Browser origins are not accepted'}, status=403)
        try:
            return await handler(request)
        except (ValueError, KeyError, TypeError) as e:
            return web.json_response({'error': str(e)}, status=400)
        except Exception as e:
            return web.json_response({'error': str(e)[:1000]}, status=500)
    app = web.Application(middlewares=[authenticate], client_max_size=32768)
    async def command(request):
        async with bridge.lock:
            return web.json_response(await bridge.command(await request.json()))
    async def events(request):
        boot = request.query.get('boot', '')
        if not boot:
            # Fresh client: start at "now" instead of replaying old history (use pending for prompts).
            return web.json_response({'events': [], 'cursor': bridge.sequence, 'boot': bridge.boot})
        # A restarted bridge numbers events from 1 again: the old cursor would hide them all.
        cursor = int(request.query.get('after', '0')) if boot == bridge.boot else 0
        batch = [e for e in bridge.events if e['seq'] > cursor][:50]
        return web.json_response({'events': batch, 'boot': bridge.boot,
                                 'cursor': batch[-1]['seq'] if batch else max(cursor, 0)})
    app.router.add_post('/command', command)
    app.router.add_get('/events', events)
    async def account(request):
        return web.json_response(await bridge.account.status())
    app.router.add_get('/account', account)
    async def cleanup(app):
        await bridge.close()
    app.on_cleanup.append(cleanup)
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--game-dir', required=True, type=Path)
    parser.add_argument('--port', default=18741, type=int)
    parser.add_argument('--listen', default='127.0.0.1', choices=['127.0.0.1', '0.0.0.0'])
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('port must be 1024..65535')
    bridge = Bridge(args.game_dir / 'config' / 'claude-chat')
    connection = bridge.directory / 'connection.json'
    # Persist the random token across restarts so Minecraft can reconnect.
    token_path = bridge.directory / 'bridge-token'
    if token_path.exists():
        token = token_path.read_text().strip()
    else:
        token = secrets.token_urlsafe(32)
        token_path.write_text(token)
    os.chmod(token_path, 0o600)
    save_json(connection, {'port': args.port, 'token': token})
    print('Claude bridge listening on ' + args.listen + ':' + str(args.port))
    web.run_app(create_app(bridge, token), host=args.listen, port=args.port, print=None)


if __name__ == '__main__':
    main()
