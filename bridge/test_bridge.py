import asyncio
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from aiohttp.test_utils import make_mocked_request
from unittest.mock import AsyncMock
from bridge import Bridge, create_app
from backends.sdk import SdkTurn, TurnClosed
try:
    import claude_agent_sdk as sdk
except ImportError:
    sdk = None


class FakeLive:
    def __init__(self, closed=False):
        self.closed = closed
        self.received = []

    async def inject(self, text):
        if self.closed:
            raise TurnClosed()
        self.received.append(text)


class FakeClient:
    """Stands in for ClaudeSDKClient: records stdin writes, replays scripted stdout."""
    def __init__(self):
        self.queries = []
        self.out = asyncio.Queue()

    async def query(self, text):
        self.queries.append(text)

    async def interrupt(self):
        raise AssertionError('f must never interrupt')

    async def receive_messages(self):
        while True:
            message = await self.out.get()
            if message is None:
                return
            yield message


@unittest.skipUnless(sdk, 'claude-agent-sdk is not installed')
class SdkTurnTests(unittest.IsolatedAsyncioTestCase):
    """Streaming-input logic against real SDK message types and a fake CLI."""
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = Bridge(self.temp.name, AsyncMock())
        self.chat = self.bridge.chats[self.bridge.selected['cc']]
        self.client = FakeClient()
        self.turn = SdkTurn(self.bridge, self.chat, self.client, sdk)

    async def asyncTearDown(self):
        await self.bridge.close()
        self.temp.cleanup()

    def put(self, *messages):
        for m in messages:
            self.client.out.put_nowait(m)

    @staticmethod
    def result(subtype='success', is_error=False, result=None):
        return sdk.ResultMessage(subtype=subtype, duration_ms=1, duration_api_ms=1,
                                 is_error=is_error, num_turns=1, session_id='s1', result=result)

    def texts(self):
        return [e['text'] for e in self.bridge.events if e.get('kind') == 'text']

    async def test_extra_message_absorbed_mid_turn(self):
        drive = asyncio.create_task(self.turn.drive('first'))
        self.put(sdk.UserMessage(content='first', origin={'kind': 'human'}),
                 sdk.AssistantMessage(content=[sdk.TextBlock('working')], model='m'))
        await self.turn.inject('extra')
        self.put(sdk.UserMessage(content='extra'),
                 sdk.UserMessage(content=[sdk.ToolResultBlock('t1', 'ok')], tool_use_result={}),
                 sdk.AssistantMessage(content=[sdk.TextBlock('both done')], model='m'),
                 self.result())
        await asyncio.wait_for(drive, 2)
        self.assertEqual(self.client.queries, ['first', 'extra'])
        self.assertEqual(self.texts(), ['working', 'both done'])
        self.assertEqual(self.chat.sdk_id, 's1')

    async def test_result_before_extra_ack_keeps_reading(self):
        drive = asyncio.create_task(self.turn.drive('first'))
        self.put(sdk.UserMessage(content='first'),
                 sdk.AssistantMessage(content=[sdk.TextBlock('answer 1')], model='m'))
        await self.turn.inject('extra')
        # CLI finished turn 1 before reading the extra message from stdin.
        self.put(self.result())
        await asyncio.sleep(0.05)
        self.assertFalse(drive.done())
        self.put(sdk.UserMessage(content='extra'),
                 sdk.AssistantMessage(content=[sdk.TextBlock('answer 2')], model='m'),
                 self.result())
        await asyncio.wait_for(drive, 2)
        self.assertEqual(self.texts(), ['answer 1', 'answer 2'])

    async def test_merged_echo_acknowledges_both(self):
        # Seen with the real CLI: prompts queued before it reads stdin become ONE message.
        turn = SdkTurn(self.bridge, self.chat, self.client, sdk, trusted_replay=True)
        drive = asyncio.create_task(turn.drive('first'))
        await asyncio.sleep(0)
        await turn.inject('extra')
        self.put(sdk.UserMessage(content='first\nextra'),
                 sdk.AssistantMessage(content=[sdk.TextBlock('both')], model='m'),
                 self.result())
        await asyncio.wait_for(drive, 2)
        self.assertEqual((turn.sent, turn.acked), (2, 2))

    async def test_cost_is_cumulative_per_process(self):
        # Seen with the real CLI: usage is per turn, total_cost_usd grows per process.
        self.chat.run_usage = {}
        drive = asyncio.create_task(self.turn.drive('first'))
        self.put(sdk.UserMessage(content='first'))
        await self.turn.inject('extra')
        usage = {'input_tokens': 2, 'output_tokens': 3}
        def result(cost):
            return sdk.ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False,
                                     num_turns=1, session_id='s1', usage=usage, total_cost_usd=cost)
        self.put(result(0.5), sdk.UserMessage(content='extra'), result(1.0))
        await asyncio.wait_for(drive, 2)
        self.assertEqual(self.chat.usage['output_tokens'], 6)
        self.assertAlmostEqual(self.chat.usage['cost_usd'], 1.0)

    async def test_tool_progress_lines(self):
        drive = asyncio.create_task(self.turn.drive('go'))
        self.put(sdk.UserMessage(content='go'),
                 sdk.AssistantMessage(content=[sdk.ToolUseBlock('t1', 'Bash', {'command': 'ls -la'}),
                                               sdk.ToolUseBlock('t2', 'TodoWrite', {'todos': [
                                                   {'content': 'A', 'status': 'completed'}]})], model='m'),
                 sdk.UserMessage(content=[sdk.ToolResultBlock('t1', 'boom', True)], tool_use_result={}),
                 self.result())
        await asyncio.wait_for(drive, 2)
        kinds = [(e['kind'], e['text']) for e in self.bridge.events if e['kind'] in ('tool', 'todo', 'tool_error')]
        self.assertEqual(kinds, [('tool', 'ls -la'), ('todo', 'План'), ('tool_error', 'boom')])

    async def test_inject_after_result_reports_closed(self):
        drive = asyncio.create_task(self.turn.drive('first'))
        self.put(sdk.UserMessage(content='first'), self.result())
        await asyncio.wait_for(drive, 2)
        with self.assertRaises(TurnClosed):
            await self.turn.inject('late')
        self.assertEqual(self.client.queries, ['first'])

    async def test_cli_without_replay_refuses_force(self):
        drive = asyncio.create_task(self.turn.drive('first'))
        self.put(sdk.AssistantMessage(content=[sdk.TextBlock('no echo')], model='m'))
        async with asyncio.timeout(2):
            while self.turn.replay is None:
                await asyncio.sleep(0)
        with self.assertRaisesRegex(ValueError, 'replay'):
            await self.turn.inject('extra')
        self.put(self.result())
        await asyncio.wait_for(drive, 2)
        self.assertEqual(self.client.queries, ['first'])

    async def test_error_in_first_turn_does_not_drop_extra(self):
        drive = asyncio.create_task(self.turn.drive('first'))
        self.put(sdk.UserMessage(content='first'))
        await self.turn.inject('extra')
        self.put(self.result('error_during_execution', True, 'boom'),
                 sdk.UserMessage(content='extra'),
                 sdk.AssistantMessage(content=[sdk.TextBlock('recovered')], model='m'),
                 self.result())
        await asyncio.wait_for(drive, 2)
        self.assertEqual(self.texts(), ['recovered'])
        self.assertTrue(any(e.get('kind') == 'error' for e in self.bridge.events))


class FeatureTests(unittest.IsolatedAsyncioTestCase):
    """ls/cd/cat, auto-approval, token accounting, progress descriptions."""
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = Bridge(self.temp.name, AsyncMock())
        self.chat = self.bridge.chats[self.bridge.selected['cc']]
        ws = self.bridge.workspace
        (ws / 'src').mkdir()
        (ws / 'src' / 'Main.java').write_text('class Main {}\n', encoding='utf-8')
        (ws / 'notes.txt').write_text('line1\nline2\n', encoding='utf-8')
        (ws / 'blob.bin').write_bytes(b'\0\1\2')

    async def asyncTearDown(self):
        await self.bridge.close()
        self.temp.cleanup()

    def last(self):
        return self.bridge.events[-1]

    async def cmd(self, action, text=''):
        await self.bridge.command({'mode': 'cc', 'action': action, 'text': text})
        return self.last()

    async def test_file_manager_does_not_move_claude(self):
        listing = await self.cmd('ls')
        self.assertEqual(listing['kind'], 'listing')
        names = [(r['name'], r['dir']) for r in listing['rows']]
        self.assertEqual(names[0], ('extensions', True))  # folders first
        self.assertIn(('notes.txt', False), names)
        claude_before = self.bridge.chat_cwd(self.chat)
        await self.cmd('cd', 'src')
        self.assertEqual(self.bridge.files.here('cc'), (self.bridge.workspace / 'src').resolve())
        self.assertEqual(self.bridge.chat_cwd(self.chat), claude_before)  # Claude stays put
        pwd = await self.cmd('pwd')
        self.assertTrue(pwd['text'].endswith('src'))
        self.assertEqual(pwd['claude'], claude_before)
        self.assertIn('class Main', (await self.cmd('cat', 'Main.java'))['text'])
        await self.cmd('cd', '..')
        self.assertEqual(self.bridge.files.here('cc'), self.bridge.workspace)
        self.assertEqual((await self.cmd('dir'))['kind'], 'listing')  # alias
        with self.assertRaisesRegex(ValueError, 'двоичный'):
            await self.cmd('cat', 'blob.bin')
        with self.assertRaisesRegex(ValueError, 'Нет такой папки'):
            await self.cmd('cd', 'missing')
        await self.cmd('cd', 'src')
        restored = Bridge(self.temp.name)
        self.assertTrue(str(restored.files.here('cc')).endswith('src'))
        await restored.close()

    async def test_file_operations(self):
        await self.cmd('mkdir', 'tmp/a')
        await self.cmd('touch', 'tmp/a/x.txt')
        self.assertTrue((self.bridge.workspace / 'tmp' / 'a' / 'x.txt').exists())
        await self.cmd('cp', 'tmp/a/x.txt tmp/y.txt')
        await self.cmd('mv', 'tmp/y.txt tmp/z.txt')
        self.assertTrue((self.bridge.workspace / 'tmp' / 'z.txt').exists())
        tree = await self.cmd('tree', 'tmp 3')
        self.assertIn('a/x.txt', [r['path'] for r in tree['rows']])
        found = await self.cmd('find', 'z.txt')
        self.assertEqual([r['path'] for r in found['rows']], ['tmp/z.txt'])
        ask = await self.cmd('rm', 'tmp')
        self.assertEqual(ask['kind'], 'confirm')  # rm asks first
        self.assertTrue((self.bridge.workspace / 'tmp').exists())
        await self.cmd('rm', ask['command'][3:])
        self.assertFalse((self.bridge.workspace / 'tmp').exists())
        with self.assertRaisesRegex(ValueError, 'опасно'):
            await self.cmd('rm', '--yes ~')
        lines = await self.cmd('cat', 'notes.txt 2-2')
        self.assertEqual(lines['text'], 'line2')

    async def test_auto_modes(self):
        from backends.sdk import auto_allows
        self.assertFalse(auto_allows(self.chat, 'Read'))
        event = await self.cmd('auto', 'edits')
        self.assertEqual(event['kind'], 'auto')
        self.assertTrue(auto_allows(self.chat, 'Edit'))
        self.assertTrue(auto_allows(self.chat, 'mcp__minecraft__open_browser'))
        self.assertFalse(auto_allows(self.chat, 'Bash'))
        await self.cmd('auto', 'all')
        self.assertTrue(auto_allows(self.chat, 'Bash'))
        await self.cmd('auto', 'off')
        self.chat.allowed.append('WebFetch')
        self.assertTrue(auto_allows(self.chat, 'WebFetch'))
        await self.cmd('auto', 'off')
        self.assertFalse(auto_allows(self.chat, 'WebFetch'))  # off also forgets "always"
        with self.assertRaises(ValueError):
            await self.cmd('auto', 'yolo')

    async def test_always_button_is_valid_answer(self):
        task = asyncio.create_task(self.bridge.ask_user(
            self.chat, 'permission', 'Bash', ['Разрешить', 'Отклонить', 'Всегда разрешать Bash']))
        await asyncio.sleep(0)
        request = next(iter(self.bridge.pending))
        with self.assertRaises(ValueError):
            self.bridge.answer(self.chat, request, '4')
        self.bridge.answer(self.chat, request, '3')
        self.assertEqual(await task, '3')

    async def test_tokens_report(self):
        self.chat.run_usage = {}
        self.bridge.add_usage(self.chat, {'input_tokens': 10, 'output_tokens': 5,
                                          'cache_read_input_tokens': 100}, 0.01)
        other = self.bridge.new_chat('c', 'local')
        self.bridge.add_usage(other, {'input_tokens': 1, 'output_tokens': 1}, 0.001)
        event = await self.cmd('tokens')
        self.assertEqual(event['kind'], 'tokens')
        this, project, total = event['rows']
        self.assertEqual((this['input'], this['output'], this['cache']), (110, 5, 100))
        self.assertEqual(project['input'], 110)
        self.assertEqual((total['input'], total['output']), (111, 6))
        self.assertAlmostEqual(total['cost'], 0.011)

    def test_describe(self):
        from backends.describe import describe, todo_rows
        cwd = str(self.bridge.workspace)
        self.assertEqual(describe('Read', {'file_path': str(self.bridge.workspace / 'src' / 'Main.java')}, cwd),
                         ('📄', 'Читаю src/Main.java'))
        icon, text = describe('Bash', {'command': 'git status', 'description': 'Check repo'}, cwd)
        self.assertEqual((icon, text), ('$', 'git status  — Check repo'))
        self.assertEqual(describe('mcp__ext__run', {}, cwd), ('⚙', 'ext.run'))
        rows = todo_rows({'todos': [{'content': 'Fix', 'activeForm': 'Fixing', 'status': 'in_progress'},
                                    {'content': 'Test', 'status': 'pending'}]})
        self.assertEqual(rows, [{'text': 'Fixing', 'status': 'in_progress'}, {'text': 'Test', 'status': 'pending'}])


class WorkspaceTests(unittest.IsolatedAsyncioTestCase):
    """Projects, git, agents, connectors, task manager, Minecraft logs."""
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.started = []
        self.finish = asyncio.Queue()
        async def runner(chat, text):
            self.started.append((chat.id, text))
            await self.finish.get()
        self.bridge = Bridge(self.temp.name, runner)
        self.ws = self.bridge.workspace
        (self.ws / 'mod').mkdir()

    async def asyncTearDown(self):
        await self.bridge.close()
        self.temp.cleanup()

    async def cmd(self, action, text='', mode='cc', **extra):
        await self.bridge.command({'mode': mode, 'action': action, 'text': text, **extra})
        return self.bridge.events[-1] if self.bridge.events else None

    async def test_projects_switch_claude_folder_and_chats(self):
        default_chat = self.bridge.current_chat('cc')
        self.assertEqual(default_chat.project, 'workspace')
        await self.cmd('cd', 'mod')
        await self.cmd('project', 'here')  # project from the file manager folder
        self.assertEqual(self.bridge.projects.current, 'mod')
        mod_chat = self.bridge.current_chat('cc')
        self.assertNotEqual(mod_chat.id, default_chat.id)
        self.assertEqual(Path(self.bridge.chat_cwd(mod_chat)), (self.ws / 'mod').resolve())
        menu = await self.cmd('chats')
        self.assertEqual([r['id'] for r in menu['rows']], [mod_chat.id])  # only this project's chats
        await self.cmd('project', 'open workspace')
        self.assertEqual(self.bridge.current_chat('cc').id, default_chat.id)  # its old chat comes back
        await self.cmd('p', 'open mod')
        self.assertEqual(self.bridge.current_chat('cc').id, mod_chat.id)
        listing = await self.cmd('project')
        self.assertEqual({r['name'] for r in listing['rows']}, {'workspace', 'mod'})
        await self.cmd('project', 'new fresh')
        self.assertTrue((self.ws / 'fresh').is_dir())
        await self.cmd('project', 'rename fresh fresh2')
        self.assertIn('fresh2', self.bridge.projects.items)
        await self.cmd('project', 'remove fresh2')
        self.assertTrue((self.ws / 'fresh').is_dir())  # files untouched
        self.assertNotIn('fresh2', self.bridge.projects.items)
        await self.cmd('project', 'open mod')
        restored = Bridge(self.temp.name)
        self.assertEqual(restored.projects.current, 'mod')
        self.assertEqual(restored.current_chat('cc').id, mod_chat.id)
        await restored.close()

    async def test_git_status_commit_branch_log_diff(self):
        if not shutil.which('git'):
            self.skipTest('git not installed')
        await self.cmd('cd', 'mod')
        await self.cmd('project', 'here')
        none = await self.cmd('git')
        self.assertEqual(none['kind'], 'git_none')
        await self.cmd('git', 'init')
        repo = self.ws / 'mod'
        for key, value in (('user.email', 't@example.com'), ('user.name', 'T')):
            await self.bridge.git.ok('config', key, value, cwd=repo)
        (repo / 'a.txt').write_text('one\n', encoding='utf-8')
        status = await self.cmd('git', 'status')
        self.assertEqual(status['kind'], 'git_status')
        self.assertIn('a.txt', [f['path'] for f in status['files']])
        await self.cmd('git', 'commit first commit')
        status = self.bridge.events[-1]
        self.assertEqual(status['files'], [])
        self.assertIn('first commit', status['last'])
        (repo / 'a.txt').write_text('two\n', encoding='utf-8')
        diff = await self.cmd('git', 'diff a.txt')
        self.assertIn('+two', diff['text'])
        await self.cmd('git', 'branch feature-x')
        branches = await self.cmd('git', 'branch')
        self.assertEqual([r['name'] for r in branches['rows'] if r['current']], ['feature-x'])
        log = await self.cmd('g', 'log')
        self.assertEqual(log['rows'][0]['subject'], 'first commit')
        await self.cmd('git', 'commit')  # no message: Claude writes the commit
        await asyncio.sleep(0)
        self.assertIn('коммит', self.started[-1][1])

    async def test_agents_presets_custom_and_project_files(self):
        listing = await self.cmd('agents')
        names = {r['name'] for r in listing['rows']}
        self.assertTrue({'code-reviewer', 'debugger', 'test-writer'} <= names)
        await self.cmd('agent', 'new api-checker checks REST endpoints')
        folder = self.ws / '.claude' / 'agents'
        folder.mkdir(parents=True)
        (folder / 'lint-fixer.md').write_text('---\nname: lint-fixer\ndescription: Fixes lint\n'
                                             'tools: Read, Edit\n---\nYou fix lint errors.\n', encoding='utf-8')
        rows = {r['name']: r for r in (await self.cmd('agents'))['rows']}
        self.assertEqual(rows['api-checker']['source'], 'свой')
        self.assertEqual(rows['lint-fixer']['source'], 'проект')
        self.assertEqual(rows['lint-fixer']['tools'], ['Read', 'Edit'])
        await self.cmd('agent', 'off debugger')
        self.assertNotIn('debugger', self.bridge.agents.enabled())
        definitions = self.bridge.agents.definitions()
        self.assertIn('lint-fixer', definitions)
        self.assertNotIn('debugger', definitions)
        await self.cmd('agent', 'code-reviewer check the last commit')
        await asyncio.sleep(0)
        self.assertIn('code-reviewer subagent', self.started[-1][1])

    async def test_spawn_runs_in_parallel_without_switching(self):
        current = self.bridge.current_chat('cc')
        event = await self.cmd('spawn', '@test-writer cover utils.py')
        await asyncio.sleep(0)
        self.assertEqual(event['kind'], 'spawned')
        self.assertEqual(self.bridge.current_chat('cc').id, current.id)
        spawned = self.bridge.chats[event['target']]
        self.assertIn('test-writer subagent', self.started[-1][1])
        tasks = await self.cmd('ps')
        self.assertEqual([r['id'] for r in tasks['rows']], [spawned.id])
        await self.cmd('stop', spawned.id[:8])
        self.assertIsNone(spawned.task)

    async def test_connectors_keys_and_servers(self):
        from unittest import mock
        rows = {r['id']: r for r in (await self.cmd('connectors'))['rows']}
        self.assertIn('playwright', rows)
        self.assertEqual(rows['brave-search']['missing'], ['BRAVE_API_KEY'])
        with mock.patch('shutil.which', return_value='npx'):
            need = await self.cmd('connect', 'brave-search')
            self.assertEqual(need['kind'], 'need_key')
            await self.cmd('connect', 'brave-search', secret={'BRAVE_API_KEY': 'abc123'})
            await self.cmd('connect', 'playwright')
        server = self.bridge.extensions['brave-search']
        self.assertEqual(server['env']['BRAVE_API_KEY'], 'abc123')
        self.assertIn('@brave/brave-search-mcp-server', server['args'])
        self.assertIn('playwright', self.bridge.extensions)
        self.assertNotIn('abc123', json.dumps(list(self.bridge.events), ensure_ascii=False))  # never echoed
        await self.cmd('disconnect', 'playwright')
        self.assertNotIn('playwright', self.bridge.extensions)
        with mock.patch.object(self.bridge.connectors, 'gh_token', return_value='gho_x'):
            await self.cmd('connect', 'github')
        self.assertEqual(self.bridge.extensions['github']['headers']['Authorization'], 'Bearer gho_x')
        self.bridge.account.set_api_key('sk-ant-keep-me-123456')  # shares secrets.json
        self.assertEqual(self.bridge.connectors.secrets('brave-search')['BRAVE_API_KEY'], 'abc123')
        self.assertEqual(self.bridge.account.api_key(), 'sk-ant-keep-me-123456')

    async def test_minecraft_logs_and_cq(self):
        from backends.sdk import read_game_logs
        game = Path(self.temp.name)
        (game / 'crash-reports').mkdir()
        (game / 'crash-reports' / 'crash-2026-10-27_01.00.00-client.txt').write_text(
            '---- Minecraft Crash Report ----\nNullPointerException', encoding='utf-8')
        (game / 'logs').mkdir()
        (game / 'logs' / 'latest.log').write_text('\n'.join(f'line {i}' for i in range(500)), encoding='utf-8')
        self.assertIn('NullPointerException', read_game_logs(game, 'crash'))
        self.assertTrue(read_game_logs(game, 'latest', 30).endswith('line 499'))
        await self.cmd('q', 'one')
        await self.cmd('q', 'two')
        await self.cmd('cq')
        self.assertFalse(self.bridge.current_chat('cc').queue)


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = Bridge(self.temp.name, AsyncMock())
        self.chat = self.bridge.current_chat('cc')
        self.client = FakeClient()
        self.turn = SdkTurn(self.bridge, self.chat, self.client, sdk, trusted_replay=True)

    async def asyncTearDown(self):
        await self.bridge.close()
        self.temp.cleanup()

    @staticmethod
    def delta(text):
        return sdk.StreamEvent(uuid='u', session_id='s', event={
            'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': text}})

    @staticmethod
    def done():
        return sdk.ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False,
                                 num_turns=1, session_id='s')

    async def test_lines_stream_and_final_text_not_duplicated(self):
        drive = asyncio.create_task(self.turn.drive('go'))
        stop = sdk.StreamEvent(uuid='u', session_id='s', event={'type': 'content_block_stop'})
        for m in (sdk.UserMessage(content='go'), self.delta('Hello wo'), self.delta('rld\nSecond'),
                  self.delta(' line'),
                  sdk.AssistantMessage(content=[sdk.TextBlock('Hello world\nSecond line')], model='m'),
                  stop, self.done()):
            self.client.out.put_nowait(m)
        await asyncio.wait_for(drive, 2)
        parts = [(e['text'], e['first']) for e in self.bridge.events if e['kind'] == 'text_part']
        self.assertEqual(parts, [('Hello world', True), ('Second line', False)])
        self.assertFalse([e for e in self.bridge.events if e['kind'] == 'text'])

    async def test_subagent_progress_is_tagged(self):
        drive = asyncio.create_task(self.turn.drive('go'))
        for m in (sdk.UserMessage(content='go'),
                  sdk.AssistantMessage(content=[sdk.ToolUseBlock('t1', 'Task', {
                      'subagent_type': 'code-reviewer', 'description': 'Review diff'})], model='m'),
                  sdk.AssistantMessage(content=[sdk.TextBlock('inner notes'),
                                                sdk.ToolUseBlock('t2', 'Read', {'file_path': 'a.py'})],
                                       model='m', parent_tool_use_id='t1'),
                  self.done()):
            self.client.out.put_nowait(m)
        await asyncio.wait_for(drive, 2)
        tools = [(e['text'], e.get('agent')) for e in self.bridge.events if e['kind'] == 'tool']
        self.assertEqual(tools, [('Агент code-reviewer: Review diff', None), ('Читаю a.py', 'code-reviewer')])
        self.assertFalse([e for e in self.bridge.events if e['kind'] == 'text'])  # subagent text hidden


class AccountTests(unittest.IsolatedAsyncioTestCase):
    """In-game account flow against a fake `claude auth` CLI."""
    async def asyncSetUp(self):
        import sys
        from unittest import mock
        self.temp = tempfile.TemporaryDirectory()
        fake = Path(__file__).with_name('fake_claude_auth.py')
        patches = [mock.patch('backends.account.cli_command', lambda config: [sys.executable, str(fake)]),
                   mock.patch.dict('os.environ', {'FAKE_AUTH_FILE': str(Path(self.temp.name) / 'logged-in')})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.bridge = Bridge(self.temp.name, AsyncMock())

    async def asyncTearDown(self):
        await self.bridge.close()
        self.temp.cleanup()

    async def login_state(self, *states):
        async with asyncio.timeout(15):
            while self.bridge.account.login_state not in states:
                await asyncio.sleep(0.02)

    async def test_login_with_code_then_logout(self):
        status = await self.bridge.account.status()
        self.assertFalse(status['account']['logged_in'])
        await self.bridge.command({'mode': 'c', 'action': 'login'})
        await self.login_state('waiting_code')
        self.assertTrue(self.bridge.account.login_url.startswith('https://claude.com/'))
        await self.bridge.command({'mode': 'c', 'action': 'login_code', 'text': 'good-code#abc'})
        await self.login_state('done', 'failed')
        self.assertEqual(self.bridge.account.login_state, 'done')
        status = await self.bridge.account.status()
        self.assertEqual(status['account']['email'], 'player@example.com')
        await self.bridge.command({'mode': 'c', 'action': 'logout'})
        self.assertFalse((await self.bridge.account.status())['account']['logged_in'])

    async def test_wrong_code_reports_error(self):
        await self.bridge.account.login()
        await self.login_state('waiting_code')
        await self.bridge.account.submit_code('bad')
        await self.login_state('done', 'failed')
        self.assertEqual(self.bridge.account.login_state, 'failed')
        self.assertIn('invalid code', self.bridge.account.login_error)

    async def test_cancel_login_kills_cli(self):
        await self.bridge.account.login()
        await self.login_state('waiting_code')
        process = self.bridge.account.process
        await self.bridge.command({'mode': 'c', 'action': 'login_cancel'})
        self.assertIsNotNone(process.returncode)
        self.assertEqual(self.bridge.account.login_state, 'idle')

    async def test_api_key_stored_masked_and_removed(self):
        await self.bridge.command({'mode': 'c', 'action': 'apikey', 'text': 'sk-test-0123456789abcdef'})
        self.assertEqual(self.bridge.account.api_key(), 'sk-test-0123456789abcdef')
        status = await self.bridge.account.status()
        self.assertNotIn('0123456789ab', json.dumps(status))
        self.assertTrue(status['api_key'].endswith('cdef'))
        with self.assertRaises(ValueError):
            self.bridge.account.set_api_key('bad key with spaces')
        await self.bridge.command({'mode': 'c', 'action': 'apikey', 'text': ''})
        with unittest.mock.patch.dict('os.environ', {}, clear=False) as env:
            env.pop('ANTHROPIC_API_KEY', None)
            self.assertIsNone(self.bridge.account.api_key())


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.started = []
        self.finish = asyncio.Queue()
        async def runner(chat, text):
            self.started.append(text)
            await self.finish.get()
        self.bridge = Bridge(self.temp.name, runner)
        self.chat = self.bridge.chats[self.bridge.selected['cc']]

    async def asyncTearDown(self):
        await self.bridge.close()
        self.temp.cleanup()

    async def wait_started(self, count):
        async with asyncio.timeout(2):
            while len(self.started) < count:
                await asyncio.sleep(0)

    async def test_fifo(self):
        await self.bridge.enqueue(self.chat, 'first')
        await self.bridge.enqueue(self.chat, 'second')
        await self.wait_started(1)
        self.assertEqual(self.started, ['first'])
        await self.finish.put(True)
        await self.wait_started(2)
        self.assertEqual(self.started, ['first', 'second'])

    async def test_force_adds_message_without_cancelling(self):
        live = FakeLive()
        async def runner(chat, text):
            self.started.append(text)
            chat.live = live
            try:
                await self.finish.get()
            finally:
                chat.live = None
        self.bridge.runner = runner
        await self.bridge.enqueue(self.chat, 'first')
        await self.wait_started(1)
        task = self.chat.task
        await self.bridge.enqueue(self.chat, 'queued')
        await self.bridge.command({'mode': 'cc', 'action': 'force', 'text': 'extra'})
        async with asyncio.timeout(2):
            while not live.received:
                await asyncio.sleep(0)
        self.assertEqual(live.received, ['extra'])
        self.assertIs(self.chat.task, task)
        self.assertFalse(task.cancelled())
        self.assertEqual(self.started, ['first'])
        self.assertEqual(list(self.chat.queue), ['queued'])
        await self.finish.put(True)
        await self.wait_started(2)
        self.assertEqual(self.started, ['first', 'queued'])

    async def test_force_when_idle_starts_immediately(self):
        await self.bridge.force(self.chat, 'now')
        await self.wait_started(1)
        self.assertEqual(self.started, ['now'])

    async def test_force_lost_race_runs_next_without_cancel(self):
        live = FakeLive(closed=True)
        async def runner(chat, text):
            self.started.append(text)
            chat.live = live
            await self.finish.get()
        self.bridge.runner = runner
        await self.bridge.enqueue(self.chat, 'first')
        await self.wait_started(1)
        await self.bridge.enqueue(self.chat, 'queued')
        await self.bridge.force(self.chat, 'late')
        async with asyncio.timeout(2):
            while list(self.chat.queue) != ['late', 'queued']:
                await asyncio.sleep(0)
        self.assertEqual(self.started, ['first'])
        await self.finish.put(True)
        await self.wait_started(2)
        self.assertEqual(self.started, ['first', 'late'])

    async def test_force_api_is_explicitly_refused(self):
        chat = self.bridge.new_chat('c', 'api')
        await self.bridge.enqueue(chat, 'first')
        await self.wait_started(1)
        task = chat.task
        with self.assertRaisesRegex(ValueError, 'HTTP'):
            await self.bridge.force(chat, 'extra')
        self.assertIs(chat.task, task)
        self.assertFalse(task.done())
        self.assertFalse(chat.queue)

    async def test_command_aliases(self):
        for action in ('c', 'chats', 'chat'):
            await self.bridge.command({'mode': 'cc', 'action': action})
            self.assertEqual(self.bridge.events[-1]['kind'], 'chats')
        await self.bridge.command({'mode': 'cc', 'action': 'queue', 'text': 'x'})
        await self.wait_started(1)

    async def test_cancel_before_worker_started(self):
        await self.bridge.enqueue(self.chat, 'never')
        await self.bridge.stop(self.chat)
        self.assertFalse(self.chat.queue)
        self.assertIsNone(self.chat.task)

    async def test_cancel_clears_queue(self):
        await self.bridge.enqueue(self.chat, 'first')
        await self.wait_started(1)
        await self.bridge.enqueue(self.chat, 'queued')
        await self.bridge.stop(self.chat)
        self.assertFalse(self.chat.queue)
        self.assertIsNone(self.chat.task)

    async def test_parallel_chats(self):
        other = self.bridge.new_chat('cc', 'local')
        await self.bridge.enqueue(self.chat, 'one')
        await self.bridge.enqueue(other, 'two')
        await self.wait_started(2)
        self.assertEqual(set(self.started), {'one', 'two'})

    async def test_permissions_are_single_use_and_chat_scoped(self):
        task = asyncio.create_task(self.bridge.ask_user(self.chat, 'permission', 'Run command?'))
        await asyncio.sleep(0)
        request = next(iter(self.bridge.pending))
        other = self.bridge.new_chat('cc', 'local')
        with self.assertRaises(ValueError):
            self.bridge.answer(other, request, '1')
        with self.assertRaises(ValueError):
            self.bridge.answer(self.chat, request, 'yes')
        self.bridge.answer(self.chat, request, '2')
        self.assertEqual(await task, '2')
        with self.assertRaises(ValueError):
            self.bridge.answer(self.chat, request, '1')

    async def test_multiple_choice_and_custom_text(self):
        for text, expected in [('1,3', 'A, C'), ('my custom text', 'my custom text')]:
            task = asyncio.create_task(self.bridge.ask_user(self.chat, 'question', 'Choose', ['A','B','C'], True))
            await asyncio.sleep(0)
            request = next(iter(self.bridge.pending))
            self.bridge.answer(self.chat, request, text)
            self.assertEqual(await task, expected)

    async def test_single_choice_rejects_multi(self):
        task = asyncio.create_task(self.bridge.ask_user(self.chat, 'question', 'Choose', ['A','B']))
        await asyncio.sleep(0)
        request = next(iter(self.bridge.pending))
        with self.assertRaises(ValueError):
            self.bridge.answer(self.chat, request, '1,2')
        self.bridge.answer(self.chat, request, '1')
        self.assertEqual(await task, 'A')

    async def test_state_roundtrip(self):
        self.chat.title = 'Saved'
        self.chat.sdk_id = 'sdk-session'
        self.bridge.persist()
        restored = Bridge(self.temp.name)
        self.assertEqual(restored.chats[self.chat.id].sdk_id, 'sdk-session')
        self.assertEqual(restored.chats[self.chat.id].title, 'Saved')
        await restored.close()

    async def test_atomic_extension_reload(self):
        extension = self.bridge.workspace / 'extensions' / 'example'
        extension.mkdir()
        manifest = extension / 'manifest.json'
        manifest.write_text(json.dumps({'id': 'example', 'server': {'command': 'python',
                            'args': ['${extension}/server.py']}}))
        self.assertEqual(self.bridge.reload_extensions(), ['example'])
        self.assertEqual(Path(self.bridge.extensions['example']['args'][0]), extension.resolve() / 'server.py')
        manifest.write_text('{broken')
        with self.assertRaises(ValueError):
            self.bridge.reload_extensions()
        self.assertIn('example', self.bridge.extensions)

    async def test_reserved_extension_name_rejected(self):
        extension = self.bridge.workspace / 'extensions' / 'bad'
        extension.mkdir()
        (extension / 'manifest.json').write_text(json.dumps({'id': 'minecraft', 'server': {'command': 'python'}}))
        with self.assertRaises(ValueError):
            self.bridge.reload_extensions()

    async def test_http_auth_and_command_flow(self):
        # Exercise actual routing + auth middleware in-process: sandbox forbids TCP sockets.
        app = create_app(self.bridge, 'test-token')
        async def call(method, path, headers=None, data=None):
            request = make_mocked_request(method, path, headers=headers or {})
            if data is not None:
                request.json = AsyncMock(return_value=data)
            route = await app.router.resolve(request)
            return await app.middlewares[0](request, route.handler)
        response = await call('GET', '/events')
        self.assertEqual(response.status, 401)
        headers = {'Authorization': 'Bearer test-token'}
        response = await call('POST', '/command', {**headers, 'Origin':'https://other'},
                              {'mode':'cc','action':'status'})
        self.assertEqual(response.status, 403)
        response = await call('POST', '/command', headers,
                              {'mode':'cc','action':'q','text':'HTTP question'})
        self.assertEqual(response.status, 200)
        await self.wait_started(1)
        # Fresh client: no history replay, only a starting cursor and the bridge id.
        response = await call('GET', '/events?after=0', headers)
        first = json.loads(response.text)
        self.assertEqual(first['events'], [])
        self.bridge.emit(self.chat, 'after connect', kind='status')
        response = await call('GET', f"/events?after={first['cursor']}&boot={first['boot']}", headers)
        self.assertEqual([e['text'] for e in json.loads(response.text)['events']], ['after connect'])
        # Bridge restarted (new boot id): old cursor is ignored, nothing is hidden.
        response = await call('GET', f"/events?after=99999&boot=old-instance", headers)
        self.assertGreater(len(json.loads(response.text)['events']), 0)
        response = await call('POST', '/command', headers, {'mode':'bad','action':'q'})
        self.assertEqual(response.status, 400)

    async def test_restart_preserves_session(self):
        self.chat.sdk_id = 'resume-me'
        await self.bridge.command({'mode':'cc', 'action':'restart'})
        self.assertEqual(self.chat.sdk_id, 'resume-me')


if __name__ == '__main__':
    unittest.main()
