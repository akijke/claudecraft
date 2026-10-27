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
from backends.agents import Agents
from backends.connectors import Connectors
from backends.files import FileManager
from backends.gitops import Git
from backends.shell import Shell
from backends.github import GitHub
from backends.projects import DEFAULT as DEFAULT_PROJECT, Projects
from backends.util import save_json  # noqa: F401  (re-exported for older imports)
from backends.i18n import language, to_english, translate_event


def uid():
    return uuid.uuid4().hex


@dataclass
class Chat:
    id: str
    mode: str
    backend: str
    title: str
    sdk_id: str | None = None
    messages: list = field(default_factory=list)
    project: str | None = None       # /cc: project whose folder Claude works in
    session_cwd: str | None = None   # folder the SDK session was created in
    auto: str = 'off'                # auto-approve: off | edits | all
    allowed: list = field(default_factory=list)  # tools approved with "always"
    usage: dict = field(default_factory=dict)    # token/cost totals for /c tokens
    updated: float = 0.0
    origin: str = ''                 # 'pc' = session found in Claude Code on the PC
    cwd: str | None = None           # legacy field (old `cd`), ignored
    run_usage: dict = field(default_factory=dict)  # current queue item (not saved)
    step: str = ''                   # last progress line, for the task manager
    started: float = 0.0
    queue: deque = field(default_factory=deque)
    task: asyncio.Task | None = None
    # Live backend session of the running item (SdkTurn); receives `f` messages.
    live: object = None
    deliveries: set = field(default_factory=set)


SAVED = ('id', 'mode', 'backend', 'title', 'sdk_id', 'messages', 'project', 'session_cwd', 'auto',
         'allowed', 'usage', 'updated', 'origin')


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
        # config/claude-chat → the Minecraft instance folder (for latest.log / crash reports).
        self.game_dir = self.directory.parent.parent
        self.projects = Projects(self)
        self.connectors = Connectors(self)
        self.agents = Agents(self)
        self.files = FileManager(self)
        self.git = Git(self)
        self.shell = Shell(self)
        self.github = GitHub(self)
        self.extensions = {}
        self.reload_extensions()
        self.chats = {}
        self.selected = {}
        self.pending = {}
        self.events = deque(maxlen=3000)
        self.sequence = 0
        self.boot = uid()  # lets clients notice a bridge restart (event numbering resets)
        self.lock = asyncio.Lock()
        self.runner = runner or self.run_backend
        self.account = Account(self)
        self.rate_limits = {}
        state = self.directory / 'chats.json'
        if state.exists():
            data = json.loads(state.read_text(encoding='utf-8'))
            for row in data['chats']:
                chat = Chat(**{k: v for k, v in row.items() if k in Chat.__dataclass_fields__})
                if chat.mode == 'cc' and not chat.project:
                    chat.project = (self.projects.find_by_path(chat.session_cwd)
                                    if chat.session_cwd else None) or DEFAULT_PROJECT
                if chat.mode == 'cc' and chat.project not in self.projects.items:
                    chat.project = DEFAULT_PROJECT
                self.chats[chat.id] = chat
            self.selected = data['selected']
            self.files.cwd = data.get('files', {})
        if self.selected.get('c') not in self.chats:
            self.new_chat('c', 'local')
        selected = self.chats.get(self.selected.get('cc'))
        if not selected or selected.project != self.projects.current:
            self.open_project(self.projects.current)

    # ---- extensions & connectors ----------------------------------------
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
        for name, server in self.connectors.servers().items():
            loaded.setdefault(name, server)  # a hand-written extension with the same name wins
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
                'workspace': str(self.workspace), 'project': self.projects.path_of_current(),
                'executables': {name: bool(shutil.which(name)) for name in
                                ('git', 'gh', 'docker', 'ssh', 'node', 'npx', 'bash', 'cmd.exe',
                                 'powershell.exe', 'pwsh')},
                'extensions': sorted(self.extensions)}

    # ---- state ------------------------------------------------------------
    def persist(self):
        rows = [{k: getattr(c, k) for k in SAVED} for c in self.chats.values()]
        save_json(self.directory / 'chats.json', {'chats': rows, 'selected': self.selected,
                                                  'files': self.files.cwd})

    def emit(self, chat, text='', **extra):
        self.sequence += 1
        event = {'seq': self.sequence, 'mode': chat.mode, 'chat': chat.id, 'title': chat.title[:32],
                 'project': chat.project, 'text': text[:24000], **extra}
        if extra.get('kind') == 'tool':
            chat.step = text[:120]
        # The one place where bridge text gets the user's language (English by default).
        event = translate_event(event, self.language())
        self.events.append(event)
        return event

    def language(self):
        return language(self.directory)

    def prompt(self, text):
        """A canned instruction for Claude plus the language to answer in."""
        return text + (' Answer in Russian.' if self.language() == 'ru' else ' Answer in English.')

    def tr(self, text):
        """Bridge-written text in the user's language (errors, titles outside events)."""
        return to_english(text) if self.language() == 'en' else text

    def current_chat(self, mode):
        return self.chats[self.selected[mode]]

    def new_chat(self, mode, backend, project=None, select=True):
        if mode not in ('c', 'cc') or backend not in ('local', 'api'):
            raise ValueError('Unknown mode/backend')
        if mode == 'cc' and backend == 'api':
            raise ValueError('/cc uses Agent SDK; API chat belongs to /c')
        chat = Chat(uid(), mode, backend, 'Новый чат', updated=time.time(),
                    project=(project or self.projects.current) if mode == 'cc' else None)
        self.chats[chat.id] = chat
        if select:
            self.selected[mode] = chat.id
        self.persist()
        return chat

    def open_project(self, name):
        """Claude's working folder = the project's folder; its chats come with it."""
        if name not in self.projects.items:
            raise ValueError(f'Нет проекта {name}. Список: /cc project')
        self.projects.current = name
        self.projects.touch(name)
        own = [c for c in self.chats.values() if c.mode == 'cc' and c.project == name]
        if own:
            self.selected['cc'] = max(own, key=lambda c: c.updated).id
            self.persist()
        else:
            self.new_chat('cc', 'local', project=name)
        self.files.cwd['cc'] = self.projects.path_of(name)
        self.projects.save()
        return self.current_chat('cc')

    async def sync_sessions(self, name=None):
        """Import Claude Code sessions of the project folder from the PC (shared ~/.claude store).

        Sessions started in a terminal / the Claude app show up as chats; chats started here
        are already the same sessions there. Returns how many sessions were new."""
        name = name or self.projects.current
        folder = self.projects.path_of(name)
        try:
            from claude_agent_sdk import list_sessions
            sessions = await asyncio.to_thread(list_sessions, directory=folder, limit=50, include_worktrees=False)
        except Exception:  # missing SDK or unreadable store: syncing is best effort
            return 0
        from backends.terminal import session_title
        by_sdk = {c.sdk_id: c for c in self.chats.values() if c.sdk_id}
        new = 0
        for session in sessions:
            when = (session.last_modified or 0) / 1000
            chat = by_sdk.get(session.session_id)
            if chat:
                chat.updated = max(chat.updated, when)  # continued on the PC → moves up the list
                continue
            imported = Chat(uid(), 'cc', 'local', session_title(session), session.session_id,
                            project=name, session_cwd=session.cwd or folder, updated=when, origin='pc')
            self.chats[imported.id] = imported
            new += 1
        if new or sessions:
            self.persist()
        return new

    def safe_project_name(self, folder):
        base = re.sub(r'[^\w.\-]+', '-', folder.name, flags=re.UNICODE).strip('-.')[:36] or 'project'
        name, n = base, 2
        while name in self.projects.items:
            name, n = f'{base}-{n}', n + 1
        return name

    def chat_cwd(self, chat):
        """Folder Claude works in for this chat."""
        if chat.mode != 'cc':
            return str(self.workspace)
        folder = Path(self.projects.path_of(chat.project))
        return str(folder if folder.is_dir() else self.workspace)

    # ---- queue ------------------------------------------------------------
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
                chat.started = started = time.monotonic()
                chat.step = 'думает…'
                try:
                    await self.runner(chat, question)
                    chat.updated = time.time()
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
            chat.step = ''

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
        everything, project = {}, {}
        for c in self.chats.values():
            for key, value in c.usage.items():
                everything[key] = everything.get(key, 0) + value
                if c.mode == 'cc' and c.project == self.projects.current:
                    project[key] = project.get(key, 0) + value
        rows = [row('Этот чат: ' + chat.title[:40], chat.usage),
                row('Проект ' + self.projects.current, project), row('Все чаты', everything)]
        note = ('Стоимость — оценка Claude Code по ценам API; на подписке списывается лимит, '
                'а не деньги.' if chat.backend == 'local' else 'Токены из ответов Messages API.')
        limits = [{'type': k, **v} for k, v in sorted(self.rate_limits.items())]
        self.emit(chat, 'Токены', kind='tokens', rows=rows, note=note, limits=limits)

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

    # ---- menus ------------------------------------------------------------
    def chat_menu(self, mode):
        chat = self.current_chat(mode)
        rows = [{'id': c.id, 'label': ('PC · ' if c.origin == 'pc' else '') + c.title, 'selected': c.id == chat.id,
                 'running': bool(c.task), 'queue': len(c.queue), 'origin': c.origin, 'session': bool(c.sdk_id)}
                for c in sorted(self.chats.values(), key=lambda c: -c.updated)
                if c.mode == mode and (mode == 'c' or c.project == self.projects.current)]
        self.emit(chat, 'Выбери чат:', kind='chats', rows=rows,
                  project_name=self.projects.current if mode == 'cc' else None,
                  project_path=self.projects.path_of_current() if mode == 'cc' else None)

    def projects_menu(self, mode):
        self.emit(self.current_chat(mode), 'Проекты', kind='projects', rows=self.projects.rows(self.chats),
                  browsing=str(self.files.here(mode)))

    async def project_command(self, mode, arg):
        sub, _, rest = (arg or '').strip().partition(' ')
        sub, rest = sub.lower(), rest.strip()
        if not sub or sub == 'list':
            return self.projects_menu(mode)
        if sub == 'open':
            if rest not in self.projects.items:
                raise ValueError(f'Нет проекта {rest}. Список: /cc project')
            # Pull PC sessions first, so opening picks the newest of them instead of a blank chat.
            new = await self.sync_sessions(rest)
            chat = self.open_project(rest)
            self.emit(chat, f'Проект «{rest}» открыт. Claude работает в {self.projects.path_of(rest)}',
                      kind='project_opened', path=self.projects.path_of(rest), name=rest, synced=new)
            return self.chat_menu('cc')
        if sub == 'pick':
            # Folder chosen in the system dialog of the game (any folder on the PC).
            folder = Path(rest.strip().strip('"')).expanduser()
            if not folder.is_dir():
                raise ValueError(f'Нет такой папки: {folder}')
            name = self.projects.find_by_path(folder) or self.projects.add(self.safe_project_name(folder), folder)
            return await self.project_command(mode, f'open {name}')
        if sub in ('add', 'here'):
            # add <name> [path]  |  here [name]  (path = the file manager's folder)
            name, _, path = rest.partition(' ')
            folder = Path(path.strip().strip('"')) if path.strip() else self.files.here(mode)
            name = name or folder.name
            self.projects.add(name, folder)
            new = await self.sync_sessions(name)
            self.open_project(name)
            self.emit(self.current_chat('cc'), f'Проект «{name}» создан и открыт: {folder}',
                      kind='project_opened', path=str(folder), name=name, synced=new)
            return self.projects_menu(mode)
        if sub == 'new':
            name = self.projects.check_name(rest)
            folder = self.workspace / name
            folder.mkdir(exist_ok=False)
            self.projects.add(name, folder)
            self.open_project(name)
            self.emit(self.current_chat('cc'), f'Новый проект «{name}»: {folder}', kind='project_opened',
                      path=str(folder), name=name)
            return
        if sub in ('remove', 'rm', 'forget'):
            if any(c.task for c in self.chats.values() if c.project == rest):
                raise ValueError('В проекте идёт работа — сначала cancel')
            self.projects.remove(rest)
            for cid in [c.id for c in self.chats.values() if c.project == rest]:
                del self.chats[cid]
            if self.chats.get(self.selected.get('cc')) is None or self.current_chat('cc').project == rest:
                self.open_project(self.projects.current)
            self.persist()
            self.emit(self.current_chat(mode), f'Проект «{rest}» убран из списка (файлы на диске не тронуты).',
                      kind='status')
            return self.projects_menu(mode)
        if sub == 'rename':
            old, _, new = rest.partition(' ')
            self.projects.rename(old, new.strip())
            for c in self.chats.values():
                if c.project == old:
                    c.project = new.strip()
            self.persist()
            return self.projects_menu(mode)
        raise ValueError('project: list | open <имя> | here [имя] | add <имя> [путь] | new <имя> | '
                         'rename <старое> <новое> | remove <имя>')

    def tasks_menu(self, mode):
        rows = []
        now = time.monotonic()
        for c in self.chats.values():
            waiting = any(p['chat'] == c.id for p in self.pending.values())
            if not (c.task or c.queue or waiting):
                continue
            rows.append({'id': c.id, 'mode': c.mode, 'title': c.title, 'project': c.project,
                         'state': 'ждёт ответа' if waiting else ('работает' if c.task else 'в очереди'),
                         'code': 'waiting' if waiting else ('running' if c.task else 'queued'),
                         'step': c.step, 'queue': len(c.queue),
                         'seconds': int(now - c.started) if c.task and c.started else 0,
                         'selected': self.selected.get(c.mode) == c.id})
        self.emit(self.current_chat(mode), 'Задачи', kind='tasks', rows=rows)

    async def spawn(self, mode, text):
        """Run a task in a NEW chat of the current project, in parallel, without switching to it."""
        agent = None
        if text.startswith('@'):
            agent, _, text = text[1:].partition(' ')
            if agent not in self.agents.enabled():
                raise ValueError(f'Нет включённого агента {agent}: /cc agents')
        self.check_text(text)
        chat = self.new_chat('cc', 'local', select=False)
        chat.title = ((agent + ': ') if agent else '') + text[:50]
        prompt = f'Use the {agent} subagent to do this: {text}' if agent else text
        self.start(chat, prompt)
        self.emit(self.current_chat(mode), f'Запущено параллельно: {chat.title}', kind='spawned',
                  target=chat.id, target_title=chat.title)

    def connectors_menu(self, mode):
        self.emit(self.current_chat(mode), 'Коннекторы', kind='connectors', rows=self.connectors.rows())

    def agents_menu(self, mode):
        self.emit(self.current_chat(mode), 'Агенты', kind='agents', rows=self.agents.rows())

    # ---- command dispatcher ------------------------------------------------
    async def command(self, data):
        mode = data.get('mode')
        if mode not in ('c', 'cc'):
            raise ValueError('Invalid mode')
        action = data.get('action')
        chat = self.current_chat(mode)
        action = ALIASES.get(action, action)
        text = data.get('text', '') or ''
        if action == 'q':
            await self.enqueue(chat, text)
        elif action == 'f':
            await self.force(chat, text)
        elif action in ('cancel', 'restart'):
            await self.stop(chat)
            self.emit(chat, 'Остановлено; очередь очищена. История сохранена.', kind='status')
        elif action == 'disconnect' and text.strip():
            # `/cc disconnect github` = switch a connector off.
            self.connectors.disconnect(text.strip())
            self.reload_extensions()
            self.connectors_menu(mode)
        elif action in ('disconnect', 'leave'):
            # Sent by the mod when the player leaves the world: stop all work, keep history.
            for item in list(self.chats.values()):
                await self.stop(item)
        elif action == 'reload':
            names = self.reload_extensions()
            self.emit(chat, 'Расширения и коннекторы перезагружены для следующих запросов: '
                      + (', '.join(names) or 'нет'), kind='status')
        elif action in ('tools', 'capabilities'):
            self.emit(chat, json.dumps(self.capabilities(), ensure_ascii=False, indent=2), kind='status')
        elif action == 'tokens':
            self.tokens_report(chat)
        elif action == 'auto':
            value = text.strip().lower()
            if value:
                if value not in AUTO_MODES:
                    raise ValueError('auto: off | edits | all')
                chat.auto = value
                if value == 'off':
                    chat.allowed.clear()
                self.persist()
            self.emit(chat, AUTO_MODES[chat.auto], kind='auto', mode_value=chat.auto,
                      allowed=list(chat.allowed))
        # file manager (never moves Claude)
        elif action == 'ls':
            self.files.ls(mode, text)
        elif action == 'cd':
            self.files.cd(mode, text)
        elif action == 'pwd':
            self.files.pwd(mode)
        elif action == 'cat':
            self.files.cat(mode, text)
        elif action == 'tree':
            self.files.tree(mode, text)
        elif action == 'find':
            self.files.find(mode, text)
        elif action == 'mkdir':
            self.files.mkdir(mode, text)
        elif action == 'touch':
            self.files.touch(mode, text)
        elif action == 'mv':
            self.files.mv(mode, text)
        elif action == 'cp':
            self.files.mv(mode, text, copy=True)
        elif action == 'rm':
            self.files.rm(mode, text)
        elif action == 'open':
            self.files.open(mode, text)
        elif action == 'code':
            self.files.open(mode, text, editor=True)
        # projects, git, agents, connectors, tasks
        elif action == 'project':
            await self.project_command(mode, text)
        elif action == 'git':
            await self.git.command(mode, text)
        elif action == 'agents':
            self.agents_menu(mode)
        elif action == 'agent':
            sub, _, rest = text.strip().partition(' ')
            if sub == 'new':
                name, _, about = rest.partition(' ')
                self.agents.create(name, about.strip())
                self.agents_menu(mode)
            elif sub in ('rm', 'remove'):
                self.agents.remove(rest.strip())
                self.agents_menu(mode)
            elif sub in ('on', 'off'):
                self.agents.toggle(rest.strip(), sub == 'on')
                self.agents_menu(mode)
            elif sub in self.agents.enabled():
                if not rest.strip():
                    raise ValueError(f'Формат: agent {sub} <задача>')
                await self.enqueue(self.current_chat('cc'), f'Use the {sub} subagent to do this: {rest.strip()}')
            else:
                raise ValueError('agent <имя> <задача> | new <имя> <описание> | on/off <имя> | rm <имя>')
        elif action == 'spawn':
            await self.spawn(mode, text.strip())
        elif action == 'tasks':
            self.tasks_menu(mode)
        elif action == 'stop':
            target = self.find_chat(text)
            await self.stop(target)
            self.emit(target, 'Остановлено из менеджера задач.', kind='status')
            self.tasks_menu(mode)
        elif action == 'connectors':
            self.connectors_menu(mode)
        elif action == 'connect':
            cid = text.strip()
            missing = self.connectors.connect(cid, data.get('secret'))
            if missing:
                self.emit(chat, f'{cid}: нужен ключ', kind='need_key', connector=cid,
                          fields=self.connectors.fields(cid))
            else:
                self.reload_extensions()
                self.emit(chat, f'Коннектор {cid} подключён — заработает со следующего запроса /cc.',
                          kind='status')
                self.connectors_menu(mode)
        elif action == 'disconnect_connector':
            self.connectors.disconnect(text.strip())
            self.reload_extensions()
            self.connectors_menu(mode)
        elif action == 'key':
            cid = text.strip()
            from backends.connectors import CATALOG
            if cid not in CATALOG:
                raise ValueError(f'Нет коннектора {cid}')
            self.emit(chat, f'{cid}: ключи', kind='need_key', connector=cid, fields=self.connectors.fields(cid))
        elif action == 'crash':
            await self.enqueue(self.current_chat('cc'), self.prompt(CRASH_PROMPT))
        elif action == 'clearqueue':
            chat.queue.clear()
            self.emit(chat, 'Очередь очищена.', kind='status')
        elif action == 'status':
            state = 'ожидание'
            if chat.task:
                state = 'работает' + (', f доступно' if chat.live is not None else '')
            where = f'проект {chat.project} · {self.chat_cwd(chat)}' if mode == 'cc' else 'чат'
            self.emit(chat, f'{chat.title} | {chat.backend} | {state} | очередь {len(chat.queue)} | '
                      f'auto {chat.auto} | {where}', kind='status')
        elif action == 'new':
            chat = self.new_chat(mode, text or 'local')
            self.emit(chat, 'Новый чат создан.', kind='status')
            self.chat_menu(mode)
        elif action == 'select':
            target = self.chats.get(text)
            if not target or target.mode != mode:
                raise ValueError('Unknown chat')
            if mode == 'cc' and target.project != self.projects.current:
                self.projects.current = target.project
                self.projects.save()
            self.selected[mode] = target.id
            self.persist()
            self.chat_menu(mode)
        elif action == 'chat':
            if mode == 'cc':
                await self.sync_sessions()
            self.chat_menu(mode)
        elif action == 'sh':
            # /sh <cmd> (system shell), /ps, /cmd, /bash <cmd>; `kill [id]` stops running commands.
            kind, _, rest = text.partition(' ')
            if kind not in ('auto', 'ps', 'cmd', 'bash'):
                kind, rest = 'auto', text
            if rest.strip() in ('kill', 'stop') or rest.strip().startswith(('kill ', 'stop ')):
                await self.shell.kill(rest.strip().partition(' ')[2].strip() or None)
            else:
                await self.shell.run(mode, kind, rest)
        elif action == 'gh_login':
            await self.github.login()
        elif action == 'gh_cancel':
            await self.github.cancel()
        elif action == 'gh_logout':
            await self.github.logout()
        elif action == 'gh_token':
            self.github.set_token(data.get('secret') or text)
        elif action == 'gh_connector':
            self.github.connector(text.strip() != 'off')
        elif action == 'sync':
            new = await self.sync_sessions()
            self.emit(chat, f'Синхронизировано с Claude Code на ПК: новых сессий {new}.', kind='status')
            self.chat_menu('cc')
        elif action == 'terminal':
            from backends.terminal import open_terminal
            target = self.current_chat('cc')
            line = await asyncio.to_thread(open_terminal, self.chat_cwd(target), self.config, target.sdk_id)
            self.emit(target, f'Открыт терминал: {line}', kind='terminal', command=line,
                      folder=self.chat_cwd(target), busy=bool(target.task))
        elif action == 'rename':
            name = text.strip()
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
            self.answer(self.chats[p['chat']], request, text)
        elif action == 'pending':
            for p in self.pending.values():
                if self.chats[p['chat']].mode == mode:
                    event = dict(p['event'])
                    for key in ('seq', 'text', 'chat', 'mode', 'title', 'project'):
                        event.pop(key, None)
                    self.emit(self.chats[p['chat']], p['event']['text'], **event)
        elif action == 'history':
            self.history(mode, data.get('chat') or chat.id)
        elif action == 'login':
            await self.account.login(console=text == 'console')
        elif action == 'login_code':
            await self.account.submit_code(text)
        elif action == 'login_cancel':
            await self.account.cancel_login()
        elif action == 'logout':
            await self.account.logout()
        elif action == 'apikey':
            self.account.set_api_key(text)
        elif action == 'local':
            if mode != 'cc':
                raise ValueError('Use /cc local to import CLI coding sessions')
            new = await self.sync_sessions()
            self.emit(chat, f'Синхронизировано с Claude Code на ПК: новых сессий {new}.', kind='status')
            self.chat_menu(mode)
        else:
            raise ValueError('Unknown action')
        return {'ok': True}

    def find_chat(self, prefix):
        prefix = (prefix or '').strip()
        matches = [c for c in self.chats.values() if prefix and c.id.startswith(prefix)]
        if len(matches) != 1:
            raise ValueError('Чат не найден (нужен id из /cc tasks)')
        return matches[0]

    def history(self, mode, chat_id):
        """Re-send this chat's recent events (for the conversation screen after a reconnect)."""
        chat = self.chats.get(chat_id)
        if not chat:
            raise ValueError('Unknown chat')
        recent = [e for e in self.events if e['chat'] == chat_id][-200:]
        self.emit(chat, '', kind='history', items=recent)

    async def close(self):
        await self.account.cancel_login()
        await self.github.cancel()
        for chat in self.chats.values():
            await self.stop(chat)
        for job in list(self.git.jobs):
            job.cancel()
        await self.shell.close()
        self.persist()


# Short and legacy command names → canonical actions (full names keep working).
ALIASES = {'queue': 'q', 'force': 'f', 'c': 'chat', 'chats': 'chat', 'dir': 'ls', 'type': 'cat',
           'usage': 'tokens', 'cq': 'clearqueue', 'p': 'project', 'projects': 'project', 'g': 'git',
           'ps': 'tasks', 'jobs': 'tasks', 'mcp': 'connectors', 'unconnect': 'disconnect_connector',
           'detach': 'disconnect_connector', 'explorer': 'open', 'edit': 'code', 'run': 'spawn',
           'pc': 'terminal', 'resume': 'terminal', 'term': 'terminal', '$': 'sh', 'shell': 'sh'}
USAGE_KEYS = ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')
AUTO_MODES = {
    'off': 'Авторазрешение выключено: каждое действие спрашивает подтверждение.',
    'edits': 'Авторазрешение: чтение, поиск и правка файлов без вопросов; команды (Bash) и интернет спрашивают.',
    'all': 'Авторазрешение ВСЕГО: Claude выполняет любые команды без вопросов, с правами моста. Будь осторожен.',
}
CRASH_PROMPT = ('Investigate the latest Minecraft failure: read the newest crash report with the '
                'minecraft_logs tool (or the tail of latest.log if there is no crash), explain the cause in plain '
                'words, name the responsible mod/class and propose a fix. If the cause is in this project, fix it.')


def create_app(bridge, token):
    @web.middleware
    async def authenticate(request, handler):
        if not secrets.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + token):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        if request.headers.get('Origin'):
            return web.json_response({'error': 'Browser origins are not accepted'}, status=403)
        try:
            return await handler(request)
        except (ValueError, KeyError, TypeError, OSError) as e:
            return web.json_response({'error': bridge.tr(str(e))}, status=400)
        except Exception as e:
            return web.json_response({'error': str(e)[:1000]}, status=500)
    app = web.Application(middlewares=[authenticate], client_max_size=65536)
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
        batch = [e for e in bridge.events if e['seq'] > cursor][:80]
        return web.json_response({'events': batch, 'boot': bridge.boot,
                                 'cursor': batch[-1]['seq'] if batch else max(cursor, 0)})
    async def state(request):
        """Snapshot for the conversation screen: chats, projects, selection."""
        chats = [{'id': c.id, 'mode': c.mode, 'title': bridge.tr(c.title), 'project': c.project, 'backend': c.backend,
                  'origin': c.origin,
                  'running': bool(c.task), 'queue': len(c.queue), 'updated': c.updated}
                 for c in bridge.chats.values()]
        return web.json_response({'chats': chats, 'selected': bridge.selected,
                                  'project': bridge.projects.current,
                                  'project_path': bridge.projects.path_of_current(),
                                  'projects': bridge.projects.rows(bridge.chats)})
    app.router.add_post('/command', command)
    app.router.add_get('/events', events)
    app.router.add_get('/state', state)
    async def account(request):
        status = await bridge.account.status()
        status['github'] = await bridge.github.status()
        return web.json_response(status)
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
