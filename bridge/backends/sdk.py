"""Claude Agent SDK backend.

One queue item = one live SDK session (ClaudeSDKClient in streaming-input mode).
`f` messages are written into that live session with client.query() while Claude
works; nothing is interrupted or restarted.

Race with completion: the CLI is started with --replay-user-messages, so every
user message it accepts from stdin is echoed back (UserMessage without
parent_tool_use_id / tool results). A ResultMessage ends the run only when all
messages sent so far were acknowledged; an unacknowledged extra message means the
CLI will start one more turn for it, so we keep reading. The decision to stop and
the "accepting" flag flip happen without an await in between, so an `f` either
gets into the session or is told the session is already closed.
"""
import asyncio
from pathlib import Path
from collections import deque
import json
import logging
from urllib.parse import urlparse
from backends.describe import EDIT_TOOLS, describe, todo_rows, clip
from backends import undo as undo_files
from backends import images
from backends import permrules


log = logging.getLogger('claudecraft.sdk')
PLAN_MARK = '[[plan]]\n'  # queue item marker: run this request in plan mode (/cc plan)


class TurnClosed(Exception):
    """The live session already finished; the caller must schedule the message."""


def is_replayed_prompt(message, types):
    """True for a user prompt echoed by --replay-user-messages (not a tool result)."""
    if not isinstance(message, types.UserMessage):
        return False
    if message.parent_tool_use_id is not None or message.tool_use_result is not None:
        return False
    origin = message.origin or {}
    if origin.get('kind') not in (None, 'human'):
        return False
    if isinstance(message.content, list):
        return not any(isinstance(b, types.ToolResultBlock) for b in message.content)
    return True


class SdkTurn:
    def __init__(self, bridge, chat, client, types, trusted_replay=False):
        self.bridge = bridge
        self.chat = chat
        self.client = None
        self.types = types
        # The bundled CLI is known to echo prompts, so f can be written immediately.
        # For an unknown cli_path we wait for proof (the first echo) before writing.
        self.trusted_replay = trusted_replay
        self.sent = 0
        self.acked = 0
        # Texts written to the CLI and not yet echoed back, in send order.
        self.unacked = deque()
        self.accepting = True
        # None until we know whether the CLI echoes prompts (needed for safe f).
        self.replay = None
        self.ready = asyncio.Event()
        self.write_lock = asyncio.Lock()  # FIFO: several f messages keep their order
        self.cwd = None          # folder used to shorten paths in progress lines
        self.cost_seen = 0.0     # total_cost_usd is cumulative per CLI process
        self.stream = ''         # partial text not yet sent (streamed line by line)
        self.streamed = False    # current assistant message already shown via deltas
        self.first_part = True   # next streamed line starts a new answer block
        self.subagents = {}      # Task tool_use id -> subagent name
        if client is not None:
            self.attach(client)

    def attach(self, client):
        """Called once the CLI process is connected."""
        self.client = client
        if self.trusted_replay and self.replay is None:
            self.replay = True
            self.ready.set()

    def _no_echo(self):
        """Output arrived before any echo: this CLI does not replay prompts."""
        extra = self.sent - 1
        self.replay = False
        self.acked = self.sent
        self.unacked.clear()
        self.ready.set()
        if extra > 0:
            self.bridge.emit(self.chat, 'Этот Claude CLI не подтверждает сообщения f; ответ на уже '
                             'отправленные f может не показаться. Обнови Claude Code.', kind='error')

    async def inject(self, text):
        """Add a user message to the running session without interrupting it."""
        if not self.ready.is_set():
            # The CLI echoes the first prompt only together with the first model output,
            # which can take long while Claude thinks. Runs in a background delivery task,
            # so waiting here blocks nothing; cancel/stop cancels it.
            self.bridge.emit(self.chat, 'f будет передано, как только Claude начнёт отвечать…', kind='status')
            await self.ready.wait()
        if not self.accepting:
            raise TurnClosed()
        if not self.replay:
            raise ValueError('Этот Claude CLI не подтверждает входящие сообщения '
                             '(--replay-user-messages), поэтому f нельзя доставить без риска потерять ответ. '
                             'Обнови claude-agent-sdk / Claude Code или используй q. Текущая работа продолжается.')
        async with self.write_lock:
            if not self.accepting:
                raise TurnClosed()
            # Register first: the reader must not finish while this write is in flight.
            try:
                await self.write(text)
            except Exception:
                self.sent -= 1
                self.unacked.pop()
                raise

    async def write(self, text):
        """Counts the message as sent and writes it; image markers become image blocks."""
        plain, message = images.user_message(text)
        self.sent += 1
        self.unacked.append(text if message is None else plain)
        if message is None:
            await self.client.query(text)
        else:
            async def one():
                yield message
            await self.client.query(one())

    def acknowledge(self, message):
        """Match an echoed prompt against sent texts.

        Prompts that queue up before the CLI reads them are merged into ONE user
        message (texts joined by newlines) with one echo, so count by content.
        """
        content = message.content
        if isinstance(content, list):
            content = '\n'.join(b.text for b in content if isinstance(b, self.types.TextBlock))
        position = matched = 0
        while self.unacked:
            index = content.find(self.unacked[0], position)
            if index < 0:
                break
            position = index + len(self.unacked[0])
            self.unacked.popleft()
            matched += 1
        if not matched and self.unacked:
            matched = 1  # unknown echo format: still make progress
            self.unacked.popleft()
        self.acked += matched

    def stream_delta(self, event):
        """Text deltas of the main agent: emit complete lines as they arrive."""
        kind = event.get('type')
        if kind == 'message_start':
            # Prompt size of this model call = how full the context is right now.
            u = (event.get('message') or {}).get('usage') or {}
            used = sum(int(u.get(k) or 0) for k in ('input_tokens', 'cache_read_input_tokens',
                                                     'cache_creation_input_tokens'))
            if used:
                self.chat.context = used
                self.chat.live_tokens += used
                self.bridge.budget_check(self.chat, self.chat.live_tokens)
            return
        if kind == 'content_block_delta' and event.get('delta', {}).get('type') == 'text_delta':
            self.stream += event['delta'].get('text', '')
            self.streamed = True
            while '\n' in self.stream:
                line, self.stream = self.stream.split('\n', 1)
                self.emit_part(line)
        elif kind == 'content_block_stop':
            self.flush_stream()

    def emit_part(self, line):
        self.bridge.emit(self.chat, line, kind='text_part', first=self.first_part)
        self.first_part = False

    def flush_stream(self):
        if self.stream:
            self.emit_part(self.stream)
            self.stream = ''

    async def drive(self, text):
        T = self.types
        async with self.write_lock:
            await self.write(text)
        error = None
        try:
            async for message in self.client.receive_messages():
                log.debug('sdk <- %s sent=%d acked=%d %.300r', type(message).__name__,
                          self.sent, self.acked, message)
                if is_replayed_prompt(message, T):
                    if self.replay is not False:
                        self.acknowledge(message)
                        self.replay = True
                        self.ready.set()
                    continue
                if isinstance(message, T.StreamEvent):
                    if message.parent_tool_use_id is None:
                        self.stream_delta(message.event)
                    continue
                if isinstance(message, T.RateLimitEvent):
                    info = message.rate_limit_info
                    self.bridge.rate_limits[info.rate_limit_type or 'limit'] = {
                        'status': info.status, 'utilization': info.utilization, 'resets_at': info.resets_at}
                    if info.status in ('allowed_warning', 'rejected'):
                        self.bridge.emit(self.chat, 'Лимит подписки: ' + ('почти исчерпан' if info.status ==
                                         'allowed_warning' else 'исчерпан'), kind='error' if info.status ==
                                         'rejected' else 'status')
                    continue
                if isinstance(message, (T.TaskStartedMessage, T.TaskUpdatedMessage, T.TaskNotificationMessage,
                                        T.TaskProgressMessage)):
                    self.bridge.background_task(self.chat, message)
                    continue
                if isinstance(message, getattr(T, 'HookEventMessage', ())) :
                    from backends import hooks as hook_log
                    row = hook_log.record(message)
                    if row and (row['outcome'] not in ('success', '') or (row['exit'] not in (0, None))):
                        self.bridge.emit(self.chat, f"Хук {row['event']} {row['name']}: {row['outcome'] or row['exit']}",
                                         kind='hook_failed', output=row['output'])
                    continue
                if isinstance(message, T.SystemMessage) and message.subtype == 'compact_boundary':
                    meta = message.data.get('compact_metadata') or {}
                    self.chat.context = 0
                    self.bridge.emit(self.chat, f"История сжата (было {meta.get('pre_tokens', '?')} токенов).",
                                     kind='status')
                    continue
                if isinstance(message, T.SystemMessage) and message.subtype == 'init':
                    for server in message.data.get('mcp_servers') or []:
                        if isinstance(server, dict) and server.get('name'):
                            self.bridge.connectors.status[server['name']] = server.get('status')
                    self.chat.sdk_id = message.data.get('session_id', self.chat.sdk_id)
                    if message.data.get('cwd') and not getattr(self.chat, 'session_cwd', None):
                        self.chat.session_cwd = message.data['cwd']
                    self.bridge.persist()
                elif isinstance(message, T.UserMessage):
                    # Tool results: only failures are worth a chat line.
                    if isinstance(message.content, list):
                        for block in message.content:
                            if isinstance(block, T.ToolResultBlock) and block.is_error:
                                body = block.content if isinstance(block.content, str) else json.dumps(block.content, ensure_ascii=False)
                                self.bridge.emit(self.chat, clip(body, 300), kind='tool_error')
                elif isinstance(message, T.AssistantMessage):
                    if self.acked == 0 and self.replay is not False:
                        self._no_echo()
                    sub = message.parent_tool_use_id
                    agent = self.subagents.get(sub) if sub else None
                    if not sub and self.streamed:
                        self.flush_stream()  # text already shown line by line
                    for block in message.content:
                        if isinstance(block, T.TextBlock):
                            if sub:
                                continue  # a subagent's thinking-out-loud stays out of the chat
                            if not self.streamed:
                                self.bridge.emit(self.chat, block.text, kind='text')
                        elif isinstance(block, T.ToolUseBlock):
                            if block.name in ('TodoWrite',):
                                self.bridge.emit(self.chat, 'План', kind='todo', rows=todo_rows(block.input))
                                continue
                            if block.name in ('Task', 'Agent'):
                                self.subagents[block.id] = block.input.get('subagent_type') or 'агент'
                            icon, line = describe(block.name, block.input, self.cwd)
                            self.bridge.emit(self.chat, line, kind='tool', icon=icon, tool=block.name,
                                             agent=agent)
                    if not sub:
                        self.streamed = False
                        self.first_part = True
                elif isinstance(message, T.ResultMessage):
                    self.chat.sdk_id = message.session_id or self.chat.sdk_id
                    cost = message.total_cost_usd or 0.0
                    models = message.model_usage or {}
                    model = max(models, key=lambda m: (models[m] or {}).get('costUSD', 0)) if models else None
                    self.bridge.add_usage(self.chat, message.usage, max(cost - self.cost_seen, 0.0), model)
                    self.cost_seen = max(cost, self.cost_seen)
                    if self.acked == 0 and self.replay is not False:
                        self._no_echo()
                    if message.is_error:
                        error = message.result or message.subtype
                    if not self.unacked:
                        self.accepting = False  # no await between check and flip
                        break
                    if error:
                        self.bridge.emit(self.chat, 'Ошибка: ' + str(error)[:1500], kind='error')
                        error = None
                    self.bridge.emit(self.chat, 'Обрабатываю дополнительное сообщение…', kind='status')
        finally:
            self.accepting = False
            self.ready.set()
        if error:
            raise ValueError(error)


def auto_allows(chat, name):
    """Auto-approval policy of the chat (/cc auto off|edits|all + "always" buttons)."""
    if name in chat.allowed:
        return True
    if chat.auto == 'all':
        return True
    return chat.auto == 'edits' and (name in EDIT_TOOLS or name.startswith('mcp__minecraft__'))


def read_game_logs(game_dir, kind, lines=300):
    """latest.log tail, newest crash report, or the crash report list (for minecraft_logs)."""
    lines = max(20, min(lines, 2000))
    crashes = sorted((game_dir / 'crash-reports').glob('crash-*.txt'), key=lambda p: p.stat().st_mtime,
                     reverse=True) if (game_dir / 'crash-reports').is_dir() else []
    if kind == 'crash_list':
        return '\n'.join(p.name for p in crashes[:30]) or 'No crash reports.'
    if kind == 'crash':
        if not crashes:
            return 'No crash reports in ' + str(game_dir / 'crash-reports')
        return f'{crashes[0].name}\n' + crashes[0].read_text(encoding='utf-8', errors='replace')[:60000]
    log = game_dir / 'logs' / 'latest.log'
    if not log.is_file():
        return 'No logs/latest.log in ' + str(game_dir)
    text = log.read_text(encoding='utf-8', errors='replace').splitlines()
    return '\n'.join(text[-lines:])


def _types():
    import claude_agent_sdk as sdk
    return sdk


async def run(bridge, chat, text):
    plan_mode = text.startswith(PLAN_MARK)
    if plan_mode:
        text = text[len(PLAN_MARK):]
    plan_file = [None]  # set when Claude writes its plan (plan mode)
    from claude_agent_sdk import (ClaudeAgentOptions, ClaudeSDKClient, PermissionResultAllow,
        PermissionResultDeny, tool, create_sdk_mcp_server, HookMatcher)

    async def permission(name, data, context):
        try:
            return await decide(name, data)
        except TimeoutError:
            # No click within 10 minutes: refuse the tool, keep the session alive.
            bridge.emit(chat, f'Нет ответа 10 минут — {name} отклонён.', kind='status')
            return PermissionResultDeny(message='No answer from the Minecraft user in time')

    async def decide(name, data):
        if plan_mode and name in ('Write', 'Edit', 'MultiEdit') and Path(str(data.get('file_path', ''))).parent.name == 'plans':
            plan_file[0] = Path(data['file_path'])  # the plan file the CLI asked for: no question
            return PermissionResultAllow(updated_input=data)
        if name == 'ExitPlanMode':
            # /cc plan: Claude explored read-only and proposes a plan. Accept → it carries on and
            # executes it; any other text → it revises the plan; 3 → stop.
            plan = str(data.get('plan') or '')
            if not plan and plan_file[0] is not None:
                try:
                    plan = plan_file[0].read_text(encoding='utf-8', errors='replace')
                except OSError:
                    pass
            answer = await bridge.ask_user(chat, 'plan', plan[:20000] or '(пустой план)',
                                           ['Принять', 'Править', 'Отклонить'],
                                           path=str(plan_file[0]) if plan_file[0] else None)
            if answer.strip() == '1':
                bridge.emit(chat, 'План принят — выполняю.', kind='status')
                return PermissionResultAllow(updated_input=data)
            if answer.strip() in ('3', '2'):
                bridge.emit(chat, 'План отклонён.', kind='status')
                return PermissionResultDeny(message='The user rejected the plan. Stop and wait for new instructions.',
                                            interrupt=True)
            bridge.emit(chat, 'Отправил правки к плану.', kind='status')
            return PermissionResultDeny(message='The user wants changes to the plan: ' + answer.strip()
                                        + '\nRevise the plan and present it again with ExitPlanMode.')
        if name == 'AskUserQuestion':
            answers = {}
            for question in data.get('questions', []):
                options = question.get('options', [])
                labels = [o['label'] for o in options]
                description = question['question'] + '\n' + '\n'.join(
                    f"{i+1}. {o['label']} — {o.get('description', '')}" for i, o in enumerate(options))
                answers[question['question']] = await bridge.ask_user(
                    chat, 'question', description, labels, question.get('multiSelect', False))
            return PermissionResultAllow(updated_input={**data, 'answers': answers})
        path = undo_files.target(name, data, effective) if chat.mode == 'cc' else None
        changes = undo_files.diff(name, data, path) if path else None
        icon, line = describe(name, data, effective)
        verdict, rule = permrules.decide(name, data, effective) if chat.mode == 'cc' else (None, None)
        if verdict == 'deny':
            bridge.emit(chat, f'{name} запрещён правилом {rule}', kind='status')
            return PermissionResultDeny(message=f'Denied by the permission rule {rule}')
        if verdict == 'allow' or (verdict is None and auto_allows(chat, name)):
            if path:
                bridge.checkpoints.remember(chat.id, path)
                if changes:
                    bridge.emit(chat, f'{icon} {line}', kind='edit_diff', path=str(path), **changes)
            return PermissionResultAllow(updated_input=data)
        suggestion = permrules.suggest(name, data, effective) if chat.mode == 'cc' else name
        answer = await bridge.ask_user(chat, 'permission',
            f'{name}\n' + json.dumps(data, ensure_ascii=False)[:16000],
            ['Разрешить', 'Отклонить', f'Всегда: {suggestion}'], summary=f'{icon} {line}', tool=name,
            diff=changes, path=str(path) if path else None, rule=rule, suggestion=suggestion)
        if path and answer in ('1', '3'):
            bridge.checkpoints.remember(chat.id, path)
        if answer == '3':
            if chat.mode == 'cc':
                permrules.add(effective, 'allow', suggestion)
                bridge.emit(chat, f'Правило allow {suggestion} записано в .claude/settings.json проекта '
                            '(/cc perms — все правила).', kind='status')
            elif name not in chat.allowed:
                chat.allowed.append(name)
                bridge.persist()
        return (PermissionResultAllow(updated_input=data) if answer in ('1', '3')
                else PermissionResultDeny(message='User declined in Minecraft'))

    @tool('open_browser', 'Show a browser URL to the Minecraft user. Wait for explicit user confirmation.', {'url': str})
    async def open_browser(args):
        url = args['url']
        parsed = urlparse(url)
        if parsed.scheme not in ('https', 'http') or not parsed.hostname or len(url) > 2048:
            return {'content': [{'type': 'text', 'text': 'Invalid HTTP(S) URL'}], 'is_error': True}
        bridge.emit(chat, 'Открой ссылку во внешнем браузере, затем нажми «Готово».', kind='url', url=url)
        try:
            result = await bridge.ask_user(chat, 'question', 'Завершил действие в браузере?', ['Готово', 'Отмена'])
        except TimeoutError:
            return {'content': [{'type': 'text', 'text': 'No answer from the user'}], 'is_error': True}
        return {'content': [{'type': 'text', 'text': result}]}

    @tool('minecraft_logs', 'Read Minecraft logs of this game instance: kind="crash" (newest crash '
          'report), "crash_list", or "latest" (tail of logs/latest.log). Use for crash/mod debugging.',
          {'kind': str, 'lines': int})
    async def minecraft_logs(args):
        return {'content': [{'type': 'text', 'text': read_game_logs(bridge.game_dir, args.get('kind', 'latest'),
                                                                     int(args.get('lines') or 300))}]}

    async def snapshot_hook(data, tool_use_id, context):
        # Runs for every file tool, also when settings.json rules allow it without asking us,
        # so /cc undo always has the file as it was. Never blocks the tool.
        try:
            path = undo_files.target(data.get('tool_name', ''), data.get('tool_input') or {}, effective)
            if path:
                bridge.checkpoints.remember(chat.id, path)
        except Exception:
            log.exception('snapshot hook')
        return {}

    tools = (['Read', 'Write', 'Edit', 'Bash', 'Glob', 'Grep', 'WebSearch', 'WebFetch', 'Task',
              'NotebookEdit', 'AskUserQuestion'] if chat.mode == 'cc' else ['AskUserQuestion'])
    mcp = dict(bridge.extensions) if chat.mode == 'cc' else {}
    @tool('reload_extensions', 'Reload local MCP extension manifests for the next user request.', {})
    async def reload_tools(args):
        names = bridge.reload_extensions()
        bridge.emit(chat, 'Claude перезагрузил расширения: ' + ', '.join(names), kind='status')
        return {'content': [{'type': 'text', 'text': 'Loaded for next request: ' + ', '.join(names)}]}

    if chat.mode == 'cc':
        for problem in await bridge.oauth.refresh_enabled():
            bridge.emit(chat, 'Не удалось обновить вход коннектора: ' + problem, kind='error')
        if any(bridge.connectors.is_oauth(c) for c in bridge.connectors.enabled):
            bridge.reload_extensions()  # fresh tokens into the MCP configs
            mcp = dict(bridge.extensions)
    minecraft_tools = [open_browser, reload_tools, minecraft_logs] if chat.mode == 'cc' else [open_browser]
    mcp['minecraft'] = create_sdk_mcp_server(name='minecraft', tools=minecraft_tools)
    # Claude works in the chat's project folder. The CLI stores a session under the
    # folder it was created in, so an older session keeps that folder and gets the
    # project folder via add_dirs.
    effective = bridge.chat_cwd(chat)
    session_dir = (chat.session_cwd or effective) if chat.sdk_id else effective
    if not chat.sdk_id:
        chat.session_cwd = None
    extra_dirs = [effective] if Path(effective) != Path(session_dir) else []
    if extra_dirs:
        text = f'[Рабочая папка проекта: {effective}]\n{text}'
    from backends import styles
    style_addition = styles.system_addition(bridge.config, chat.project, effective) if chat.mode == 'cc' else ''
    from backends import skills as skill_files
    skills = skill_files.sdk_option(effective, set(bridge.config.get('skills_disabled', []))) if chat.mode == 'cc' else None
    if skills:
        tools = tools + ['Skill']
    if plan_mode:
        tools = tools + ['ExitPlanMode'] if 'ExitPlanMode' not in tools else tools
    options = ClaudeAgentOptions(cwd=session_dir, resume=chat.sdk_id, add_dirs=extra_dirs,
        tools=tools, mcp_servers=mcp, can_use_tool=permission,
        permission_mode='plan' if plan_mode else 'default',
        # CLAUDE.md memory, permission rules and hooks from disk, like Claude Code (off: load_claude_settings=false)
        setting_sources=['user', 'project', 'local'] if bridge.config.get('load_claude_settings', True) else [],
        # Lines of the answer are shown while they are generated.
        include_partial_messages=True, include_hook_events=True,
        hooks={'PreToolUse': [HookMatcher(matcher='Write|Edit|MultiEdit|NotebookEdit', hooks=[snapshot_hook])]}
        if chat.mode == 'cc' else None,
        model=chat.model, effort=chat.effort, skills=skills,
        agents=bridge.agents.definitions(effective) if chat.mode == 'cc' else None,
        # Echo accepted prompts so `f` messages can be matched against ResultMessage.
        extra_args={'replay-user-messages': None},
        system_prompt=('You are Claude in Minecraft. Reply in the language the user writes in; '
                       + ('if unclear, use Russian. ' if bridge.language() == 'ru' else 'if unclear, use English. ') +
                       'The user may send extra messages while you work; take them into account. '
                       'Use AskUserQuestion for choices and minecraft open_browser when the user needs a browser. '
                       'Do not open browser using shell commands. ' +
                       ('You are a programming assistant inside Minecraft (the user is a developer). '
                        f'Current project: {chat.project} at {effective}. '
                        'Delegate focused work to subagents with the Task tool when it helps. '
                        'Use Bash for git, docker, ssh and native shell commands when requested. '
                        'Capabilities: ' + json.dumps(bridge.capabilities()) + '. '
                        'Extensions live in workspace/extensions/<id>/manifest.json. After editing them, '
                        'call minecraft reload_extensions or ask the user to run /cc reload; changes apply to the next request. '
                        'You inherit bridge account permissions; do not claim privilege elevation.' if chat.mode == 'cc'
                        else 'You are a chat assistant; you cannot edit files or control the game.'))
                      + style_addition)
    if bridge.config.get('cli_path'):
        options.cli_path = bridge.config['cli_path']
    # Registered before connecting, so an early `f` waits for the session instead of missing it.
    turn = SdkTurn(bridge, chat, None, _types(), trusted_replay=not bridge.config.get('cli_path'))
    turn.cwd = effective
    chat.live = turn
    bridge.checkpoints.begin(chat.id, text)
    try:
        async with ClaudeSDKClient(options=options) as client:
            turn.attach(client)
            await turn.drive(text)
    finally:
        turn.accepting = False
        turn.ready.set()
        if chat.live is turn:
            chat.live = None
        bridge.checkpoints.finish(chat.id)
        bridge.persist()
