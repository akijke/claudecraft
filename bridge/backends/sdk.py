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
import json
from urllib.parse import urlparse


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
    def __init__(self, bridge, chat, client, types, ack_timeout=30):
        self.bridge = bridge
        self.chat = chat
        self.client = client
        self.types = types
        self.sent = 0
        self.acked = 0
        self.accepting = True
        # None until we know whether the CLI echoes prompts (needed for safe f).
        self.replay = None
        self.ready = asyncio.Event()
        self.ack_timeout = ack_timeout
        self.write_lock = asyncio.Lock()  # FIFO: several f messages keep their order

    async def inject(self, text):
        """Add a user message to the running session without interrupting it."""
        if not self.ready.is_set():
            try:
                await asyncio.wait_for(self.ready.wait(), self.ack_timeout)
            except asyncio.TimeoutError:
                raise ValueError('Claude ещё запускается; повтори f через пару секунд') from None
        if not self.accepting:
            raise TurnClosed()
        if not self.replay:
            raise ValueError('Этот Claude CLI не подтверждает входящие сообщения '
                             '(--replay-user-messages), поэтому f нельзя доставить без риска потерять ответ. '
                             'Обнови claude-agent-sdk / Claude Code или используй q. Текущая работа продолжается.')
        async with self.write_lock:
            if not self.accepting:
                raise TurnClosed()
            # Count first: the reader must not finish while this write is in flight.
            self.sent += 1
            try:
                await self.client.query(text)
            except Exception:
                self.sent -= 1
                raise

    async def drive(self, text):
        T = self.types
        self.sent = 1
        await self.client.query(text)
        error = None
        try:
            async for message in self.client.receive_messages():
                if is_replayed_prompt(message, T):
                    self.acked += 1
                    if self.replay is None:
                        self.replay = True
                        self.ready.set()
                    continue
                if isinstance(message, T.SystemMessage) and message.subtype == 'init':
                    self.chat.sdk_id = message.data.get('session_id', self.chat.sdk_id)
                    self.bridge.persist()
                elif isinstance(message, T.AssistantMessage):
                    if self.replay is None:
                        # Output before any echo: this CLI does not replay prompts.
                        self.replay = False
                        self.acked = self.sent
                        self.ready.set()
                    for block in message.content:
                        if isinstance(block, T.TextBlock):
                            self.bridge.emit(self.chat, block.text, kind='text')
                        elif isinstance(block, T.ToolUseBlock):
                            self.bridge.emit(self.chat, f'Инструмент: {block.name}', kind='status')
                elif isinstance(message, T.ResultMessage):
                    self.chat.sdk_id = message.session_id or self.chat.sdk_id
                    if self.replay is None:
                        self.replay = False
                        self.acked = self.sent
                        self.ready.set()
                    if message.is_error:
                        error = message.result or message.subtype
                    if self.acked >= self.sent:
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


def _types():
    import claude_agent_sdk as sdk
    return sdk


async def run(bridge, chat, text):
    from claude_agent_sdk import (ClaudeAgentOptions, ClaudeSDKClient, PermissionResultAllow,
        PermissionResultDeny, tool, create_sdk_mcp_server)

    async def permission(name, data, context):
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
        answer = await bridge.ask_user(chat, 'permission',
            f'{name}\n' + json.dumps(data, ensure_ascii=False)[:16000])
        return (PermissionResultAllow(updated_input=data) if answer == '1'
                else PermissionResultDeny(message='User declined in Minecraft'))

    @tool('open_browser', 'Show a browser URL to the Minecraft user. Wait for explicit user confirmation.', {'url': str})
    async def open_browser(args):
        url = args['url']
        parsed = urlparse(url)
        if parsed.scheme not in ('https', 'http') or not parsed.hostname or len(url) > 2048:
            return {'content': [{'type': 'text', 'text': 'Invalid HTTP(S) URL'}], 'is_error': True}
        bridge.emit(chat, 'Открой ссылку во внешнем браузере, затем нажми «Готово».', kind='url', url=url)
        result = await bridge.ask_user(chat, 'question', 'Завершил действие в браузере?', ['Готово', 'Отмена'])
        return {'content': [{'type': 'text', 'text': result}]}

    tools = (['Read', 'Write', 'Edit', 'Bash', 'Glob', 'Grep', 'WebSearch', 'WebFetch',
              'AskUserQuestion'] if chat.mode == 'cc' else ['AskUserQuestion'])
    mcp = dict(bridge.extensions) if chat.mode == 'cc' else {}
    @tool('reload_extensions', 'Reload local MCP extension manifests for the next user request.', {})
    async def reload_tools(args):
        names = bridge.reload_extensions()
        bridge.emit(chat, 'Claude перезагрузил расширения: ' + ', '.join(names), kind='status')
        return {'content': [{'type': 'text', 'text': 'Loaded for next request: ' + ', '.join(names)}]}

    minecraft_tools = [open_browser, reload_tools] if chat.mode == 'cc' else [open_browser]
    mcp['minecraft'] = create_sdk_mcp_server(name='minecraft', tools=minecraft_tools)
    options = ClaudeAgentOptions(cwd=str(bridge.workspace), resume=chat.sdk_id,
        tools=tools, mcp_servers=mcp, can_use_tool=permission,
        permission_mode='default', setting_sources=[],
        # Echo accepted prompts so `f` messages can be matched against ResultMessage.
        extra_args={'replay-user-messages': None},
        system_prompt=('You are Claude in Minecraft. Reply in the user language. '
                       'The user may send extra messages while you work; take them into account. '
                       'Use AskUserQuestion for choices and minecraft open_browser when the user needs a browser. '
                       'Do not open browser using shell commands. ' +
                       ('Help with code in the configured workspace. Use Bash for docker, ssh and native shell commands when requested. '
                        'Capabilities: ' + json.dumps(bridge.capabilities()) + '. '
                        'Extensions live in workspace/extensions/<id>/manifest.json. After editing them, '
                        'call minecraft reload_extensions or ask the user to run /cc reload; changes apply to the next request. '
                        'You inherit bridge account permissions; do not claim privilege elevation.' if chat.mode == 'cc'
                        else 'You are a chat assistant; you cannot edit files or control the game.')))
    if bridge.config.get('cli_path'):
        options.cli_path = bridge.config['cli_path']
    # Registered before connecting, so an early `f` waits for the session instead of missing it.
    turn = SdkTurn(bridge, chat, None, _types())
    chat.live = turn
    try:
        async with ClaudeSDKClient(options=options) as client:
            turn.client = client
            await turn.drive(text)
    finally:
        turn.accepting = False
        turn.ready.set()
        if chat.live is turn:
            chat.live = None
        bridge.persist()
