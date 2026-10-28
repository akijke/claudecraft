"""Authenticated loopback bridge. Run with --game-dir pointing at the launcher instance."""
import argparse
import html
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
from backends.connectors import CATALOG as CONNECTOR_CATALOG, Connectors
from backends.files import FileManager
from backends.undo import Checkpoints
from backends import images
from backends.ptyterm import Terminals
from backends.oauth import OAuth
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
    model: str | None = None         # None = Claude Code default; alias (opus/sonnet/haiku) or full id
    effort: str | None = None        # None = default; low | medium | high | xhigh | max
    context: int = 0                 # tokens in the context at the last model call (0 = unknown)
    context_warned: bool = False     # the >80 % warning was shown (not saved)
    last_replace: dict | None = None  # /cc replace preview waiting for "apply" (not saved)
    worktree: str | None = None      # /cc spawn in a git project: the task's own checkout
    wt_branch: str | None = None
    wt_repo: str | None = None
    paused: bool = False             # budget reached: the queue waits for /cc budget resume (not saved)
    budget_override: bool = False    # "continue anyway" for the current request
    budget_hit: tuple | None = None  # the limit that interrupted the running request
    live_tokens: int = 0             # tokens of the running request, counted per model call
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
         'allowed', 'usage', 'updated', 'origin', 'model', 'effort', 'context', 'worktree', 'wt_branch', 'wt_repo')


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
        self.checkpoints = Checkpoints(self.directory / 'undo')
        from backends.worktrees import Worktrees
        self.worktrees = Worktrees(self)
        from backends.budget import Budget
        self.budget = Budget(self)
        from backends.ledger import Ledger
        self.ledger = Ledger(self.directory)
        from backends.lsp import Lsp
        self.lsp = Lsp(self)
        self.terminals = Terminals(self)
        self.oauth = OAuth(self)
        self.port = 18741  # set by main(); used for the OAuth redirect
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
        self.bg_tasks = {}
        self.branch_cache = {}  # folder -> (git branch, time) for the status line  # Claude's background tasks: id -> info (see background_task)
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
        if chat.worktree and Path(chat.worktree).is_dir():
            return chat.worktree
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
                if chat.paused:
                    break
                hit = self.budget.exceeded(chat, scopes=('chat', 'day'))
                if hit:
                    self.pause_for_budget(chat, hit)
                    break
                question = chat.queue.popleft()
                self.emit(chat, 'Думаю…', kind='thinking', queued=len(chat.queue))
                chat.run_usage = {}
                chat.live_tokens = 0
                chat.budget_hit = None
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
                              output=u.get('output_tokens', 0), cost=round(u.get('cost_usd', 0.0), 4),
                              context=chat.context, window=context_window(chat))
                    self.context_check(chat)
                    if chat.worktree and not chat.queue:
                        self.emit(self.current_chat('cc'), f'Задача «{chat.title}» готова в своём worktree',
                                  kind='worktree_ready', target=chat.id, branch=chat.wt_branch, path=chat.worktree)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if chat.budget_hit:
                        continue  # interrupted by the budget: the card is already shown
                    message = 'Ошибка: ' + str(e)[:1500]
                    if re.search(r'auth|login|credential|401|api key', str(e), re.I):
                        message += '\nПроверь вход в аккаунт Claude: /c settings'
                    self.emit(chat, message, kind='error')
        except asyncio.CancelledError:
            self.emit(chat, 'Текущий запрос прерван.', kind='status')
        finally:
            chat.task = None
            chat.step = ''
            chat.budget_override = False

    async def status_line(self, mode):
        """Model, effort, git branch, context, tokens, limits and queue of the selected chat (status line)."""
        from backends.budget import tokens_of
        chat = self.current_chat(mode)
        folder = self.chat_cwd(chat)
        branch, stamp = self.branch_cache.get(folder, (None, 0))
        if time.monotonic() - stamp > 20:
            code, out = await self.git.run('rev-parse', '--abbrev-ref', 'HEAD', cwd=folder) if shutil.which('git') else (1, '')
            branch = out.strip() if code == 0 else None
            self.branch_cache[folder] = (branch, time.monotonic())
        limits = {k: v.get('utilization') for k, v in self.rate_limits.items() if v.get('utilization') is not None}
        return {'model': chat.model or 'default', 'effort': chat.effort or 'default', 'branch': branch,
                'context': context_percent(chat), 'tokens': tokens_of(chat.usage),
                'cost': round(chat.usage.get('cost_usd', 0.0), 4), 'day_cost': round(self.budget._today()['usd'], 4),
                'queue': len(chat.queue), 'running': chat.task is not None, 'auto': chat.auto,
                'paused': chat.paused, 'limits': limits, 'backend': chat.backend}

    def context_check(self, chat):
        """Warns once per crossing of 80 % context fill."""
        pct = context_percent(chat)
        if pct >= 80 and not getattr(chat, 'context_warned', False):
            chat.context_warned = True
            self.emit(chat, f'Контекст заполнен на {pct}% — сожми историю: /cc compact [на чём сосредоточиться]',
                      kind='context_warn', percent=pct)
        elif pct < 80:
            chat.context_warned = False

    def add_usage(self, chat, usage, cost=0.0, model=None):
        """Add one turn's tokens (and cost delta) to the chat totals and the running item."""
        for bucket in (chat.usage, chat.run_usage):
            for key in USAGE_KEYS:
                bucket[key] = bucket.get(key, 0) + int((usage or {}).get(key) or 0)
            bucket['cost_usd'] = bucket.get('cost_usd', 0.0) + float(cost or 0.0)
        chat.usage['turns'] = chat.usage.get('turns', 0) + 1
        from backends.budget import tokens_of
        self.budget.add(cost, tokens_of(usage or {}))
        self.ledger.add(chat.project if chat.mode == 'cc' else 'chat', chat.mode, model or chat.model, usage, cost)
        self.budget_check(chat)

    def budget_check(self, chat, live_tokens=0):
        """Interrupts the running request when a limit is reached (called per turn and per model call)."""
        if chat.budget_hit:
            return
        hit = self.budget.exceeded(chat, live_tokens)
        if not hit:
            return
        chat.budget_hit = hit
        live = chat.live
        if live is not None and live.client is not None:
            asyncio.get_running_loop().create_task(live.client.interrupt())
        self.pause_for_budget(chat, hit)

    def pause_for_budget(self, chat, hit):
        scope, kind, spent, limit = hit
        chat.paused = True
        shown = (lambda v: f'${v:.2f}') if kind == 'usd' else (lambda v: f'{v:,}'.replace(',', ' '))
        names = {'task': 'запрос', 'chat': 'чат', 'day': 'день'}
        self.emit(chat, f'Лимит «{names[scope]}» достигнут: {shown(spent)} из {shown(limit)} — работа на паузе',
                  kind='budget_hit', scope=scope, limit_kind=kind, spent=spent, limit=limit, queued=len(chat.queue))

    def replace(self, mode, chat, text):
        """/cc replace [-r] [-c] [--in <glob>] [--here] <old> -> <new>  (preview) · replace apply <token>"""
        from backends import replace as rep
        words = text.strip()
        if words.startswith('apply '):
            token = words[6:].strip()
            last = getattr(chat, 'last_replace', None)
            if not last or last['token'] != token:
                raise ValueError('Нет такого превью — сделай /cc replace заново')
            if chat.task is not None:
                raise ValueError('Claude сейчас работает в этом чате — замени после него (или в другом чате)')
            self.checkpoints.begin(chat.id, f"replace {last['query']} -> {last['replacement']}")
            try:
                changed = rep.apply(last['root'], last['query'], last['replacement'], last['regex'], last['case'],
                                    last['glob'], token, remember=lambda p: self.checkpoints.remember(chat.id, p))
            finally:
                self.checkpoints.finish(chat.id)
            chat.last_replace = None
            self.emit(chat, f'Заменено в файлах: {len(changed)}', kind='replace_done', files=changed)
            return
        flags = {'regex': False, 'case': False, 'glob': None, 'here': False}
        while True:
            if words.startswith('-r '):
                flags['regex'], words = True, words[3:].lstrip()
            elif words.startswith('-c '):
                flags['case'], words = True, words[3:].lstrip()
            elif words.startswith('--here '):
                flags['here'], words = True, words[7:].lstrip()
            elif words.startswith('--in '):
                glob, _, words = words[5:].partition(' ')
                flags['glob'], words = glob, words.lstrip()
            else:
                break
        if ' -> ' not in words:
            raise ValueError('replace: /cc replace [-r] [-c] [--in *.java] <что> -> <на что>')
        query, _, replacement = words.partition(' -> ')
        folder = self.files.here(mode) if flags['here'] or mode == 'c' else self.chat_cwd(chat)
        result = rep.preview(folder, query, replacement, flags['regex'], flags['case'], flags['glob'])
        chat.last_replace = {k: result[k] for k in ('root', 'query', 'replacement', 'regex', 'case', 'glob', 'token')}
        result.pop('hashes')
        self.emit(chat, 'Замена', kind='replace_preview', **result)

    def session_dir(self, chat):
        return chat.session_cwd or self.chat_cwd(chat)

    async def rewind(self, chat, arg):
        """/cc rewind — the chat's prompts; /cc rewind <n> — back to before prompt n (history and files)."""
        from backends import rewind
        if chat.backend != 'local' or not chat.sdk_id:
            raise ValueError('Откатывать нечего: у чата ещё нет сессии Claude Code')
        if not arg:
            found, _ = await asyncio.to_thread(rewind.prompts, chat.sdk_id, self.session_dir(chat))
            rows = [{'n': n, 'text': images.display(t)[:160]} for n, _, t, _ in found][-30:]
            self.emit(chat, 'История запросов', kind='rewind_list', rows=rows, chat_id=chat.id)
            return
        if chat.task is not None:
            raise ValueError('Claude ещё работает в этом чате — дождись конца или /cc cancel')
        if not arg.isdigit():
            raise ValueError('rewind: /cc rewind — список · /cc rewind <номер>')
        new_id, dropped = await asyncio.to_thread(rewind.branch, chat.sdk_id, self.session_dir(chat), int(arg), False)
        old = chat.sdk_id
        chat.sdk_id = new_id
        if new_id is None:
            chat.session_cwd = None
        # files: undo the checkpoints of the dropped requests, newest first
        norm = lambda t: images.parse(t or '')[0][:150]
        dropped_texts = {norm(t) for t in dropped}
        restored, removed = [], []
        while True:
            turns = self.checkpoints.load(chat.id)
            if not turns or norm(turns[-1].get('prompt')) not in dropped_texts:
                break
            r, m, _, _ = self.checkpoints.undo(chat.id)
            restored += r
            removed += m
        chat.context = 0
        self.persist()
        self.emit(chat, f'Откат к сообщению {arg}: убрано запросов из истории — {len(dropped)}', kind='rewound',
                  restored=restored, removed=removed, prompt=images.display(dropped[0])[:2000] if dropped else '',
                  old_session=old, session=new_id)

    async def fork(self, chat, arg):
        """/cc fork [n] — a new chat that continues from prompt n's answer (default: the latest); files untouched."""
        from backends import rewind
        if chat.backend != 'local' or not chat.sdk_id:
            raise ValueError('Ветвить нечего: у чата ещё нет сессии Claude Code')
        found, _ = await asyncio.to_thread(rewind.prompts, chat.sdk_id, self.session_dir(chat))
        number = int(arg) if arg.isdigit() else len(found)
        new_id, _ = await asyncio.to_thread(rewind.branch, chat.sdk_id, self.session_dir(chat), number, True)
        branch = self.new_chat(chat.mode, 'local', chat.project)
        branch.sdk_id, branch.session_cwd = new_id, self.session_dir(chat)
        branch.model, branch.effort, branch.auto = chat.model, chat.effort, chat.auto
        branch.title = ('✦ ' + chat.title)[:60]
        self.persist()
        self.emit(branch, f'Ветка от сообщения {number} чата «{chat.title}» — продолжай здесь, исходный чат не изменился',
                  kind='forked', source=chat.id, number=number)

    def undo(self, chat, arg=''):
        """/cc undo — restore files Claude changed in the newest request; /cc undo list — history."""
        if arg in ('list', 'ls', 'history'):
            turns = self.checkpoints.history(chat.id)
            self.emit(chat, 'Правки Claude', kind='undo_list', turns=turns[::-1][:10])
            return
        if arg:
            raise ValueError('undo: без аргумента — откатить последний запрос; undo list — история')
        restored, removed, skipped, prompt = self.checkpoints.undo(chat.id)
        self.emit(chat, 'Откат правок Claude', kind='undo', prompt=prompt, restored=restored,
                  removed=removed, skipped=skipped, left=len(self.checkpoints.load(chat.id)))

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
        self.emit(chat, 'Токены', kind='tokens', rows=rows, note=note, limits=limits,
                  context=chat.context, window=context_window(chat), percent=context_percent(chat))

    @staticmethod
    def check_text(text):
        if not isinstance(text, str) or not text.strip() or len(text) > 8000:
            raise ValueError('Question must contain 1..8000 characters')

    def start(self, chat, text, first=False):
        if len(chat.queue) >= 20:
            raise ValueError('Queue is full (20)')
        if chat.paused and chat.task is None:
            chat.queue.appendleft(text) if first else chat.queue.append(text)
            self.emit(chat, 'Чат на паузе из-за лимита — /cc budget resume', kind='status')
            return False
        if first:
            chat.queue.appendleft(text)
        else:
            chat.queue.append(text)
        if chat.title == 'Новый чат':
            chat.title = images.display(text.replace('[[plan]]\n', '[plan] '))[:60]
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

    def background_task(self, chat, message):
        """Claude's background work (Bash run_in_background, background agents) for /cc tasks."""
        name = type(message).__name__
        task = self.bg_tasks.setdefault(message.task_id, {'id': message.task_id, 'chat': chat.id, 'status': 'running',
                                                          'description': '', 'type': '', 'output_file': None,
                                                          'started': time.time()})
        data = message.data or {}
        if name == 'TaskStartedMessage':
            task.update(description=(message.description or '')[:200], type=message.task_type or '',
                        output_file=data.get('output_file') or task['output_file'])
            self.emit(chat, f'Фоновая задача Claude: {task["description"] or task["type"]}', kind='bg_task',
                      task=task['id'], status='running')
        elif name == 'TaskUpdatedMessage' and message.status:
            task['status'] = message.status
        elif name == 'TaskNotificationMessage':
            task.update(status=message.status or task['status'], output_file=message.output_file or task['output_file'],
                        summary=(message.summary or '')[:300])
        while len(self.bg_tasks) > 50:  # oldest finished ones go first
            done = [k for k, t in self.bg_tasks.items() if t['status'] != 'running']
            self.bg_tasks.pop(done[0] if done else next(iter(self.bg_tasks)))

    def bg_output(self, task_id, lines=40):
        task = self.bg_tasks.get(task_id)
        if not task:
            raise ValueError('Нет такой фоновой задачи')
        path = task.get('output_file')
        if not path or not Path(path).is_file():
            return task, ''
        text = Path(path).read_text(encoding='utf-8', errors='replace').splitlines()
        return task, '\n'.join(text[-lines:])

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
        background = [{'id': t['id'], 'chat': t['chat'], 'description': t['description'] or t['type'], 'status': t['status'],
                       'type': t['type'], 'live': self.chats.get(t['chat']) is not None and self.chats[t['chat']].live is not None}
                      for t in list(self.bg_tasks.values())[-15:]]
        self.emit(self.current_chat(mode), 'Задачи', kind='tasks', rows=rows, background=background)

    async def spawn(self, mode, text):
        """Run a task in a NEW chat of the current project, in parallel, without switching to it."""
        agent = None
        if text.startswith('@'):
            agent, _, text = text[1:].partition(' ')
            if agent not in self.agents.enabled():
                raise ValueError(f'Нет включённого агента {agent}: /cc agents')
        here = text.startswith('--here ')
        if here:
            text = text[7:].strip()
        self.check_text(text)
        chat = self.new_chat('cc', 'local', select=False)
        chat.title = ((agent + ': ') if agent else '') + text[:50]
        prompt = f'Use the {agent} subagent to do this: {text}' if agent else text
        worktree = None
        if not here and self.config.get('spawn_worktrees', True):
            try:
                worktree = await self.worktrees.create(chat, self.chat_cwd(chat))
            except ValueError as e:
                self.emit(self.current_chat(mode), f'worktree не создан ({e}) — задача работает в папке проекта',
                          kind='status')
        self.start(chat, prompt)
        self.emit(self.current_chat(mode), f'Запущено параллельно: {chat.title}', kind='spawned',
                  target=chat.id, target_title=chat.title, worktree=str(worktree) if worktree else None,
                  branch=chat.wt_branch)

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
        elif action == 'img':
            # /cc img [f] <file> [question]; the mod's /cc shot sends a fresh screenshot here.
            force = text.startswith('f ')
            path, question = images.split_arg(text[2:] if force else text,
                                              lambda p: self.files.resolve(mode, p).is_file())
            if not path:
                raise ValueError('img: /cc img <файл> [вопрос]')
            if chat.backend == 'api':
                raise ValueError('Картинки работают в чатах Claude Code (SDK), не в Messages API-чате. /c new local')
            path = images.check(self.files.resolve(mode, path))
            prompt = images.marked([path], question)
            self.emit(chat, f'Картинка для Claude: {path.name}', kind='image', path=str(path), question=question)
            await (self.force(chat, prompt) if force else self.enqueue(chat, prompt))
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
        elif action in ('perms', 'permissions'):
            from backends import permrules
            project = self.chat_cwd(chat)
            sub, _, rule = text.strip().partition(' ')
            if sub in permrules.KINDS:
                permrules.add(project, sub, rule)
            elif sub in ('rm', 'remove', 'del'):
                permrules.remove(project, rule.strip())
            elif sub:
                raise ValueError('perms [allow|deny|ask <правило> | rm <правило>]')
            table = permrules.rules(project)
            self.emit(chat, 'Правила разрешений', kind='perms', path=str(permrules.project_settings(project)),
                      rules={k: [{'rule': r, 'source': s} for r, s in v] for k, v in table.items()})
        elif action == 'style':
            from backends import styles
            from backends.util import save_json
            project = chat.project if mode == 'cc' else None
            if not project:
                raise ValueError('Стиль задаётся для проекта: используй /cc style')
            sub, _, rest = text.strip().partition(' ')
            all_styles = styles.available(self.chat_cwd(chat))
            mine = self.config.setdefault('styles', {}).setdefault(project, {})
            if sub == 'append':
                if rest.strip() in ('', 'off'):
                    mine.pop('append', None)
                else:
                    mine['append'] = rest.strip()[:2000]
            elif sub:
                if sub not in all_styles:
                    raise ValueError('Нет стиля ' + sub + '. Есть: ' + ', '.join(all_styles))
                mine['style'] = sub
            if sub:
                save_json(self.directory / 'bridge-config.json', self.config)
            self.emit(chat, 'Стиль ответов', kind='styles', project=project, current=mine.get('style') or 'default',
                      append=mine.get('append', ''),
                      rows=[{k: v for k, v in s.items() if k != 'prompt'} for s in all_styles.values()])
        elif action == 'hooks':
            from backends import hooks
            project = self.chat_cwd(chat)
            sub, _, rest = text.strip().partition(' ')
            if sub == 'add':
                head, _, command = rest.partition(' -- ')
                event, _, matcher = head.strip().partition(' ')
                hooks.add(project, event.strip(), matcher.strip(), command)
            elif sub in ('rm', 'remove'):
                hooks.remove(project, rest.strip())
            elif sub == 'log':
                self.emit(chat, 'Журнал хуков', kind='hooks_log', rows=list(hooks.LOG)[-30:])
                return {'ok': True}
            elif sub:
                raise ValueError('hooks [add <Событие> [matcher] -- <команда> | rm <id> | log]')
            self.emit(chat, 'Хуки', kind='hooks', rows=hooks.listing(project),
                      files=[{'source': s, 'path': str(p)} for s, p in hooks.sources(project)],
                      loaded=self.config.get('load_claude_settings', True))
        elif action == 'skills':
            from backends import skills
            from backends.util import save_json
            sub, _, name = text.strip().partition(' ')
            disabled = set(self.config.get('skills_disabled', []))
            if sub in ('on', 'off'):
                if name.strip() not in skills.discover(self.chat_cwd(chat)):
                    raise ValueError(f'Нет скилла {name.strip()}')
                (disabled.discard if sub == 'on' else disabled.add)(name.strip())
                self.config['skills_disabled'] = sorted(disabled)
                save_json(self.directory / 'bridge-config.json', self.config)
            elif sub:
                raise ValueError('skills [on|off <имя>]')
            self.emit(chat, 'Скиллы', kind='skills', rows=skills.rows(self.chat_cwd(chat), disabled),
                      project=str(Path(self.chat_cwd(chat)) / '.claude' / 'skills'), user=str(skills.user_dir()))
        elif action in ('memory', 'mem'):
            from backends import memory
            sub, _, rest = text.strip().partition(' ')
            if sub == 'edit':
                self.emit(chat, 'Память', kind='open_editor', path=memory.ensure(rest.strip()))
            elif sub in ('on', 'off'):
                from backends.util import save_json
                self.config['load_claude_settings'] = sub == 'on'
                save_json(self.directory / 'bridge-config.json', self.config)
            elif sub:
                raise ValueError('memory [edit <файл> | on | off]')
            if sub != 'edit':
                self.emit(chat, 'Память Claude', kind='memory', files=memory.files(self.chat_cwd(chat)),
                          loaded=self.config.get('load_claude_settings', True))
        elif action == 'remember':
            from backends import memory
            path = memory.remember(self.chat_cwd(chat), text)
            self.emit(chat, f'Запомнено в {path}', kind='status')
        elif action == 'rewind':
            await self.rewind(chat, text.strip())
        elif action in ('search', 'grep-chats'):
            from backends import search
            folder = self.chat_cwd(chat) if mode == 'cc' else str(self.workspace)
            found = await asyncio.to_thread(search.search, text, folder, list(self.chats.values()))
            self.emit(chat, text.strip(), kind='search_results', rows=found, folder=folder)
        elif action == 'open_session':
            # a search hit from a session this bridge has no chat for (e.g. started on the PC)
            sid = text.strip()
            existing = next((c for c in self.chats.values() if c.sdk_id == sid), None)
            target = existing or self.new_chat('cc', 'local')
            if not existing:
                target.sdk_id, target.session_cwd, target.title = sid, self.chat_cwd(chat), 'PC: ' + sid[:8]
            self.selected['cc'] = target.id
            self.persist()
            self.emit(target, 'Сессия открыта — пиши, Claude продолжит её', kind='status')
        elif action == 'export':
            from backends import export
            fmt = 'html' if text.strip().lower() == 'html' else 'md'
            if chat.backend == 'local':
                if not chat.sdk_id:
                    raise ValueError('Экспортировать нечего: в чате ещё не было разговора')
                entries = await asyncio.to_thread(export.entries_from_session, chat.sdk_id, self.session_dir(chat))
            else:
                entries = export.entries_from_messages(chat.messages)
            folder = self.chat_cwd(chat) if chat.mode == 'cc' else self.workspace
            path = await asyncio.to_thread(export.write, folder, chat.title, entries, fmt)
            self.emit(chat, f'Чат сохранён: {path.name}', kind='exported', path=str(path), format=fmt, count=len(entries))
        elif action == 'fork':
            await self.fork(chat, text.strip())
        elif action == 'budget':
            sub, _, rest = text.strip().partition(' ')
            if sub == 'resume':
                chat.paused = False
                chat.budget_override = True  # the next request may go past the limit once
                if chat.queue and chat.task is None:
                    chat.task = asyncio.create_task(self.pump(chat))
                self.emit(chat, 'Пауза снята — продолжаю (лимит пропускается для одного запроса).', kind='status')
            elif sub:
                self.budget.set(sub, rest)
            if sub != 'resume':
                self.emit(chat, 'Бюджет', kind='budget', **self.budget.report(chat))
        elif action == 'replace':
            self.replace(mode, chat, text)
        elif action == 'plan':
            if not text.strip():
                raise ValueError('plan: /cc plan <задача> — Claude сначала составит план')
            if chat.backend == 'api':
                raise ValueError('План работает в чатах Claude Code (SDK)')
            from backends.sdk import PLAN_MARK
            await self.enqueue(chat, PLAN_MARK + text.strip())
        elif action == 'commands':
            from backends.commands import listing
            self.emit(chat, 'Команды', kind='commands', rows=listing(self.chat_cwd(chat) if mode == 'cc' else None))
        elif action == 'slash' or (action or '').startswith('/'):
            from backends.commands import expand
            line = text if action == 'slash' else action + ' ' + text
            kind, value, extra = expand(self.chat_cwd(chat) if mode == 'cc' else None, line)
            if kind == 'action':
                return await self.command({'mode': mode, 'action': value, 'text': extra})
            self.emit(chat, '/' + line.strip().lstrip('/').split(' ')[0], kind='slash', prompt=value[:300])
            await self.enqueue(chat, value)
        elif action == 'doctor':
            from backends.doctor import report
            data = await report(self)
            self.emit(chat, 'Диагностика', kind='doctor', **data)
        elif action == 'compact':
            if chat.backend == 'api':
                raise ValueError('compact работает в чатах Claude Code (SDK)')
            if not chat.sdk_id:
                raise ValueError('Сжимать нечего: в этом чате ещё не было разговора')
            await self.enqueue(chat, ('/compact ' + text.strip()).strip())
        elif action == 'context':
            self.emit(chat, 'Контекст', kind='context', context=chat.context, window=context_window(chat),
                      percent=context_percent(chat))
        elif action == 'undo':
            self.undo(chat, text.strip())
        elif action in ('model', 'effort'):
            value = text.strip()
            if value:
                if action == 'model':
                    if value != 'default' and value not in MODELS and not re.fullmatch(r'claude-[a-z0-9.\-]{2,60}', value):
                        raise ValueError('model: opus | sonnet | haiku | default | claude-…')
                    chat.model = None if value == 'default' else value
                else:
                    if value != 'default' and value not in EFFORTS:
                        raise ValueError('effort: low | medium | high | xhigh | max | default')
                    chat.effort = None if value == 'default' else value
                self.persist()
            self.emit(chat, 'Модель', kind='model', model=chat.model, effort=chat.effort,
                      models=list(MODELS), efforts=list(EFFORTS), backend=chat.backend)
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
        elif action in ('worktree', 'wt'):
            sub, _, rest = text.strip().partition(' ')
            if sub in ('', 'list', 'ls'):
                rows = [{'id': c.id, 'title': c.title, 'branch': c.wt_branch, 'path': c.worktree,
                         'busy': c.task is not None} for c in self.chats.values() if c.worktree]
                self.emit(chat, 'Worktrees', kind='worktrees', rows=rows)
                return {'ok': True}
            target = self.find_chat(rest)
            if target.task is not None and sub in ('merge', 'drop'):
                raise ValueError('Задача ещё работает — дождись конца или /cc stop')
            if sub == 'merge':
                result = await self.worktrees.merge(target)
                self.emit(chat, f'Слито в проект: {target.title} — {result}', kind='status')
            elif sub == 'diff':
                out = await self.worktrees.diff(target)
                lines = out.splitlines()
                self.emit(chat, '\n'.join(lines[:400]), kind='diff', path=target.title, staged=False,
                          truncated=len(lines) > 400, empty=not lines)
            elif sub in ('drop', 'rm', 'remove'):
                await self.worktrees.drop(target)
                self.emit(chat, f'Worktree удалён без слияния: {target.title}', kind='status')
            else:
                raise ValueError('worktree: list | merge <чат> | diff <чат> | drop <чат>')
        elif action == 'spawn':
            await self.spawn(mode, text.strip())
        elif action in ('bg', 'background'):
            sub, _, tid = text.strip().partition(' ')
            if sub == 'out':
                task, out = self.bg_output(tid.strip())
                self.emit(chat, out or 'пока нет вывода', kind='shell_out', task=task['id'])
            elif sub == 'stop':
                task = self.bg_tasks.get(tid.strip())
                owner = self.chats.get(task['chat']) if task else None
                if not owner or not owner.live or not owner.live.client:
                    raise ValueError('Задача уже не работает (фоновые процессы живут, пока идёт запрос Claude)')
                await owner.live.client.stop_task(task['id'])
                self.emit(chat, f'Остановлено: {task["description"]}', kind='status')
            else:
                raise ValueError('bg out|stop <id>')
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
            cid, _, extra = text.strip().partition(' ')
            if self.connectors.is_oauth(cid) and extra.strip() == 'signout':
                self.oauth.forget(cid)
                self.connectors.disconnect(cid)
                self.reload_extensions()
                self.emit(chat, f'{cid}: вход забыт', kind='status')
                self.connectors_menu(mode)
                return {'ok': True}
            if self.connectors.is_oauth(cid) and (not self.oauth.has_token(cid) or extra.strip() == 'login'):
                url = await self.oauth.start(cid, CONNECTOR_CATALOG[cid]['url'])
                self.emit(chat, f'Войди в {CONNECTOR_CATALOG[cid]["name"]} в браузере — потом всё подключится само.',
                          kind='url', url=url)
                return {'ok': True}
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
        elif action == 'install_cli':
            install = getattr(self.account, 'install_cli', None)
            if install is None:
                raise ValueError('Claude Code CLI уже встроен в SDK — ставить нечего')
            await install()
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
        self.terminals.close_all()
        await self.oauth.close()
        await self.lsp.close()
        self.persist()


# Short and legacy command names → canonical actions (full names keep working).
ALIASES = {'queue': 'q', 'force': 'f', 'c': 'chat', 'chats': 'chat', 'dir': 'ls', 'type': 'cat',
           'usage': 'tokens', 'cq': 'clearqueue', 'p': 'project', 'projects': 'project', 'g': 'git',
           'ps': 'tasks', 'jobs': 'tasks', 'mcp': 'connectors', 'unconnect': 'disconnect_connector',
           'detach': 'disconnect_connector', 'explorer': 'open', 'edit': 'code', 'run': 'spawn',
           'pc': 'terminal', 'resume': 'terminal', 'term': 'terminal', '$': 'sh', 'shell': 'sh',
           'image': 'img', 'picture': 'img', '#': 'remember'}
MODELS = ('opus', 'sonnet', 'haiku')


def context_window(chat):
    """Context size of the chat's model: 1M for "[1m]" model ids, otherwise 200k."""
    return 1_000_000 if '[1m]' in (chat.model or '') else 200_000


def context_percent(chat):
    return min(100, round(100 * (chat.context or 0) / context_window(chat)))
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
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
        if request.path == '/oauth/callback' and request.method == 'GET':
            return await oauth_callback(request)  # the browser has no bridge token; `state` protects it
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
    # 4 MB: the in-game editor saves whole files (loopback only, token required).
    app = web.Application(middlewares=[authenticate], client_max_size=4 * 1024 * 1024)
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
                                  'status': {m: await bridge.status_line(m) for m in ('c', 'cc')},
                                  'project': bridge.projects.current,
                                  'project_path': bridge.projects.path_of_current(),
                                  'projects': bridge.projects.rows(bridge.chats)})
    app.router.add_post('/command', command)
    app.router.add_get('/events', events)
    app.router.add_get('/state', state)
    async def files(request):
        """File manager window: listing of a folder (path empty = current browsing folder)."""
        mode = request.query.get('mode', 'cc')
        return web.json_response(bridge.files.snapshot(mode if mode in ('c', 'cc') else 'cc',
                                                       request.query.get('path', '')))
    async def file(request):
        mode = request.query.get('mode', 'cc')
        return web.json_response(bridge.files.read(mode if mode in ('c', 'cc') else 'cc',
                                                   request.query.get('path', ''),
                                                   int(request.query.get('start', '1') or 1)))
    async def grep(request):
        q = request.query
        mode = q.get('mode', 'cc')
        result = await asyncio.to_thread(bridge.files.grep, mode if mode in ('c', 'cc') else 'cc', q.get('path', ''),
                                         q.get('q', ''), q.get('case') == '1', q.get('regex') == '1')
        return web.json_response(result)
    app.router.add_get('/grep', grep)
    async def edit(request):
        mode = request.query.get('mode', 'cc')
        return web.json_response(bridge.files.load_for_edit(mode if mode in ('c', 'cc') else 'cc',
                                                            request.query.get('path', '')))
    async def save(request):
        data = await request.json()
        mode = data.get('mode', 'cc')
        try:
            result = bridge.files.save(mode if mode in ('c', 'cc') else 'cc', data.get('path', ''),
                                       data.get('text', ''), data.get('mtime'), data.get('eol', '\n'),
                                       bool(data.get('force')))
        except ValueError as e:
            if 'изменился на диске' in str(e):  # conflict: the editor asks before overwriting
                return web.json_response({'error': bridge.tr(str(e)), 'conflict': True}, status=409)
            raise
        return web.json_response(result)
    app.router.add_get('/edit', edit)
    app.router.add_post('/save', save)
    app.router.add_get('/files', files)
    app.router.add_get('/file', file)
    async def account(request):
        status = await bridge.account.status()
        status['github'] = await bridge.github.status()
        return web.json_response(status)
    app.router.add_get('/account', account)
    async def commands(request):
        from backends.commands import listing
        mode = request.query.get('mode', 'cc')
        chat = bridge.current_chat(mode if mode in ('c', 'cc') else 'cc')
        rows = listing(bridge.chat_cwd(chat) if chat.mode == 'cc' else None)
        for r in rows:  # not an event, so translate here
            r['description'], r['hint'] = bridge.tr(r['description']), bridge.tr(r['hint'])
        return web.json_response({'commands': rows})
    app.router.add_get('/commands', commands)
    async def usage(request):
        days = max(1, min(int(request.query.get('days') or 14), 90))
        return web.json_response(await asyncio.to_thread(bridge.ledger.aggregate, days))
    app.router.add_get('/usage', usage)
    async def format_code(request):
        from backends import formatters
        data = await request.json()
        mode = data.get('mode') if data.get('mode') in ('c', 'cc') else 'cc'
        path = bridge.files.resolve(mode, data.get('path', ''))
        text, tool = await formatters.format_text(path, str(data.get('text', '')))
        return web.json_response({'text': text, 'tool': tool})
    app.router.add_post('/format', format_code)
    async def lsp(request):
        data = await request.json()
        op = request.path.rsplit('/', 1)[-1]
        mode = data.get('mode') if data.get('mode') in ('c', 'cc') else 'cc'
        path = bridge.files.resolve(mode, data.get('path', ''))
        root = bridge.chat_cwd(bridge.current_chat('cc'))
        if not str(path).startswith(str(Path(root).resolve())):
            root = str(path.parent)  # a file outside the project: its own folder is the workspace
        text = str(data.get('text', ''))
        if op == 'sync':
            return web.json_response(await bridge.lsp.sync(path, text, root))
        line, col = int(data.get('line') or 0), int(data.get('col') or 0)
        if op == 'definition':
            return web.json_response(await bridge.lsp.definition(path, text, line, col, root))
        if op == 'complete':
            return web.json_response(await bridge.lsp.complete(path, text, line, col, root))
        raise ValueError('lsp: sync | definition | complete')
    app.router.add_post('/lsp/{op}', lsp)
    async def blame(request):
        mode = request.query.get('mode', 'cc')
        path = bridge.files.resolve(mode if mode in ('c', 'cc') else 'cc', request.query.get('path', ''))
        return web.json_response({'lines': await bridge.git.blame(path)})
    app.router.add_get('/blame', blame)
    async def index(request):
        mode = request.query.get('mode', 'cc')
        chat = bridge.current_chat(mode if mode in ('c', 'cc') else 'cc')
        root = bridge.chat_cwd(chat) if chat.mode == 'cc' else bridge.files.here(chat.mode)
        return web.json_response(await asyncio.to_thread(bridge.files.index, root))
    app.router.add_get('/index', index)

    async def oauth_callback(request):
        q = request.query
        page = ('<!doctype html><meta charset="utf-8"><title>Claudecraft</title><body style="font-family:sans-serif;'
                'background:#1e1e22;color:#ddd;display:grid;place-items:center;height:90vh"><div><h2>{}</h2>'
                '<p>{}</p></div>')
        chat = bridge.current_chat('cc')
        try:
            cid = await bridge.oauth.callback(q.get('state'), q.get('code', ''), q.get('error'))
            bridge.connectors.connect(cid)
            bridge.reload_extensions()
        except Exception as e:
            bridge.emit(chat, str(e), kind='error')
            text = html.escape(bridge.tr(str(e)))
            return web.Response(text=page.format('✖ Claudecraft', text), content_type='text/html', status=400)
        name = CONNECTOR_CATALOG[cid]['name']
        bridge.emit(chat, f'Коннектор {cid} подключён — заработает со следующего запроса /cc.', kind='status')
        bridge.connectors_menu('cc')
        return web.Response(text=page.format('✔ ' + html.escape(name), html.escape(
            bridge.tr('Готово — можно вернуться в Minecraft.'))), content_type='text/html')
    app.router.add_get('/oauth/callback', oauth_callback)

    # ---- interactive terminals (in-game terminal window) ----
    def size(data):
        return max(10, min(int(data.get('cols') or 100), 400)), max(3, min(int(data.get('rows') or 30), 200))
    async def pty_open(request):
        data = await request.json()
        mode = data.get('mode') if data.get('mode') in ('c', 'cc') else 'cc'
        cwd = bridge.files.resolve(mode, data.get('cwd') or '')
        if not cwd.is_dir():
            raise ValueError(f'Нет такой папки: {cwd}')
        cols, rows = size(data)
        term = await asyncio.to_thread(bridge.terminals.open, str(data.get('shell') or 'auto'), cwd, cols, rows)
        return web.json_response(term.snapshot())
    async def pty_input(request):
        data = await request.json()
        text = data.get('data', '')
        if not isinstance(text, str) or len(text) > 65536:
            raise ValueError('input: up to 64 KB of text')
        await asyncio.to_thread(bridge.terminals.get(data.get('id', '')).write, text)
        return web.json_response({'ok': True})
    async def pty_resize(request):
        data = await request.json()
        bridge.terminals.get(data.get('id', '')).resize(*size(data))
        return web.json_response({'ok': True})
    async def pty_screen(request):
        term = bridge.terminals.get(request.query.get('id', ''))
        scroll = int(request.query.get('scroll') or 0)
        with term.lock:
            version = term.screen.version
        if request.query.get('version') == str(version) and scroll == int(request.query.get('was') or -1):
            return web.json_response({'same': True, 'alive': term.ended is None})
        return web.json_response(term.snapshot(scroll))
    async def pty_close(request):
        data = await request.json()
        bridge.terminals.close(data.get('id', ''))
        return web.json_response({'terminals': bridge.terminals.list()})
    async def pty_list(request):
        return web.json_response({'terminals': bridge.terminals.list()})
    app.router.add_post('/pty/open', pty_open)
    app.router.add_post('/pty/input', pty_input)
    app.router.add_post('/pty/resize', pty_resize)
    app.router.add_get('/pty/screen', pty_screen)
    app.router.add_post('/pty/close', pty_close)
    app.router.add_get('/pty/list', pty_list)
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
    bridge.port = args.port
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
