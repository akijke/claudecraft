import asyncio
import json
from pathlib import Path
import os
import shutil
import sys
import tempfile
import unittest
from urllib.parse import quote
from aiohttp.test_utils import make_mocked_request
from unittest import mock
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
        self.assertEqual(kinds, [('tool', 'ls -la'), ('todo', 'Plan'), ('tool_error', 'boom')])

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
        self.assertIn('commit', self.started[-1][1].lower())

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
        self.assertEqual(rows['api-checker']['source'], 'custom')
        self.assertEqual(rows['lint-fixer']['source'], 'project')
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
        self.assertEqual(tools, [('Agent code-reviewer: Review diff', None), ('Reading a.py', 'code-reviewer')])
        self.assertFalse([e for e in self.bridge.events if e['kind'] == 'text'])  # subagent text hidden


class LanguageTests(unittest.IsolatedAsyncioTestCase):
    """English by default; Russian when the mod's client.json says so."""
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = Bridge(self.temp.name, AsyncMock())
        self.chat = self.bridge.current_chat('cc')

    async def asyncTearDown(self):
        await self.bridge.close()
        self.temp.cleanup()

    def test_templates_and_nesting(self):
        from backends.i18n import to_english
        self.assertEqual(to_english('Нет такой папки: C:/x'), 'No such folder: C:/x')
        self.assertEqual(to_english('Ошибка: Нет такого файла: a.txt'), 'Error: No such file: a.txt')
        self.assertEqual(to_english('Ищу «TODO» в src'), 'Searching “TODO” in src')
        self.assertEqual(to_english('Ошибка: x\nПроверь вход в аккаунт Claude: /c settings'),
                         'Error: x\nCheck your Claude login: /c settings')
        self.assertEqual(to_english('Привет, Claude'), 'Привет, Claude')  # unknown text is left alone

    async def test_default_english_then_russian(self):
        event = self.bridge.emit(self.chat, 'Очередь очищена.', kind='status')
        self.assertEqual(event['text'], 'Queue cleared.')
        answer = self.bridge.emit(self.chat, 'Готово', kind='text')  # Claude's words are never touched
        self.assertEqual(answer['text'], 'Готово')
        (Path(self.temp.name) / 'client.json').write_text(json.dumps({'language': 'ru'}), encoding='utf-8')
        self.assertEqual(self.bridge.emit(self.chat, 'Очередь очищена.', kind='status')['text'], 'Очередь очищена.')

    async def test_http_errors_translated(self):
        app = create_app(self.bridge, 't')
        request = make_mocked_request('POST', '/command', headers={'Authorization': 'Bearer t'})
        request.json = AsyncMock(return_value={'mode': 'cc', 'action': 'cd', 'text': 'missing'})
        route = await app.router.resolve(request)
        response = await app.middlewares[0](request, route.handler)
        self.assertEqual(response.status, 400)
        self.assertTrue(json.loads(response.text)['error'].startswith('No such folder: '))


class PcSyncTests(unittest.IsolatedAsyncioTestCase):
    """Folder picked on the PC becomes a project; Claude Code sessions are shared with the PC."""
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = Bridge(self.temp.name, AsyncMock())
        self.folder = Path(self.temp.name) / 'My Game Mod'
        self.folder.mkdir()

    async def asyncTearDown(self):
        await self.bridge.close()
        self.temp.cleanup()

    def session(self, sid, title, modified):
        return sdk.SDKSessionInfo(session_id=sid, summary=title, last_modified=modified, file_size=1,
                                  custom_title=None, first_prompt=title, git_branch=None,
                                  cwd=str(self.folder), tag=None, created_at=None)

    async def test_pick_folder_imports_pc_sessions_once(self):
        from unittest import mock
        sessions = [self.session('pc-1', 'Fix renderer', 2_000_000_000_000),
                    self.session('pc-2', 'Write README', 1_000_000_000_000)]
        with mock.patch('claude_agent_sdk.list_sessions', return_value=sessions) as listing:
            await self.bridge.command({'mode': 'cc', 'action': 'project', 'text': f'pick {self.folder}'})
            self.assertEqual(listing.call_args.kwargs['directory'], str(self.folder.resolve()))
            name = self.bridge.projects.current
            self.assertEqual(name, 'My-Game-Mod')  # safe name from the folder
            chats = [c for c in self.bridge.chats.values() if c.project == name]
            self.assertEqual({c.sdk_id for c in chats}, {'pc-1', 'pc-2'})
            self.assertEqual(self.bridge.current_chat('cc').sdk_id, 'pc-1')  # newest PC session opens
            self.assertTrue(all(c.origin == 'pc' for c in chats))
            # picking the same folder again just opens it, and syncing never duplicates
            await self.bridge.command({'mode': 'cc', 'action': 'project', 'text': f'pick {self.folder}'})
            await self.bridge.command({'mode': 'cc', 'action': 'sync', 'text': ''})
            self.assertEqual(len([c for c in self.bridge.chats.values() if c.project == name]), 2)
            menu = [e for e in self.bridge.events if e['kind'] == 'chats'][-1]
            self.assertTrue(menu['rows'][0]['label'].startswith('PC · '))

    async def test_open_in_terminal_resumes_same_session(self):
        from unittest import mock
        chat = self.bridge.current_chat('cc')
        chat.sdk_id = 'abc-123'
        with mock.patch('backends.terminal.subprocess.Popen') as popen, \
                mock.patch('backends.terminal.shutil.which', return_value='C:/bin/claude.exe'):
            await self.bridge.command({'mode': 'cc', 'action': 'pc', 'text': ''})
        command = popen.call_args.args[0]
        self.assertIn('--resume abc-123', command if isinstance(command, str) else ' '.join(command))
        self.assertEqual(popen.call_args.kwargs.get('cwd'), self.bridge.chat_cwd(chat))
        event = self.bridge.events[-1]
        self.assertEqual((event['kind'], event['command']), ('terminal', 'claude --resume abc-123'))


class ShellTests(unittest.IsolatedAsyncioTestCase):
    """Real shell commands from chat: output, UTF-8, cd between commands, exit codes, kill."""
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = Bridge(self.temp.name, AsyncMock())
        (self.bridge.workspace / 'sub').mkdir()

    async def asyncTearDown(self):
        await self.bridge.close()
        self.temp.cleanup()

    async def sh(self, text):
        await self.bridge.command({'mode': 'cc', 'action': 'sh', 'text': text})

    async def finished(self, count=1):
        async with asyncio.timeout(60):
            while len([e for e in self.bridge.events if e['kind'] == 'shell_end']) < count:
                await asyncio.sleep(0.05)
        return [e for e in self.bridge.events if e['kind'] == 'shell_end'][-1]

    def output(self):
        return '\n'.join(e['text'] for e in self.bridge.events if e['kind'] == 'shell_out')

    async def test_command_output_cd_and_exit_code(self):
        await self.sh('cd sub')  # handled by the bridge: next commands run in sub
        self.assertTrue(str(self.bridge.files.here('cc')).endswith('sub'))
        kind = 'ps' if os.name == 'nt' else 'bash'
        await self.sh(f'{kind} echo привет; echo $PWD' if kind == 'bash' else 'ps echo привет; (Get-Location).Path')
        end = await self.finished()
        self.assertEqual(end['code'], 0)
        self.assertIn('привет', self.output())
        self.assertIn('sub', self.output())
        await self.sh('exit 3' if os.name != 'nt' else 'cmd exit /b 3')
        self.assertEqual((await self.finished(2))['code'], 3)

    async def test_kill_long_command(self):
        await self.sh('Start-Sleep 30' if os.name == 'nt' else 'sleep 30')
        await asyncio.sleep(1)
        self.assertEqual(len(self.bridge.shell.running()), 1)
        await self.sh('kill')
        end = await self.finished()
        self.assertTrue(end.get('killed'))
        self.assertFalse(self.bridge.shell.running())


class FileWindowTests(unittest.IsolatedAsyncioTestCase):
    """HTTP data for the file manager window."""
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = Bridge(self.temp.name, AsyncMock())
        ws = self.bridge.workspace
        (ws / 'src').mkdir()
        (ws / 'src' / 'A.java').write_text('line1\nline2\nline3\n', encoding='utf-8')
        (ws / 'b.bin').write_bytes(b'\0\1')
        self.app = create_app(self.bridge, 't')

    async def asyncTearDown(self):
        await self.bridge.close()
        self.temp.cleanup()

    async def get(self, path):
        request = make_mocked_request('GET', path, headers={'Authorization': 'Bearer t'})
        route = await self.app.router.resolve(request)
        response = await self.app.middlewares[0](request, route.handler)
        return response.status, json.loads(response.text)

    async def test_editor_load_save_and_conflict(self):
        path = self.bridge.workspace / 'src' / 'C.txt'
        path.write_bytes(b'one\r\ntwo\r\n')
        status, data = await self.get('/edit?mode=cc&path=' + quote(str(path)))
        self.assertEqual((data['text'], data['eol']), ('one\ntwo\n', '\r\n'))
        saved = self.bridge.files.save('cc', str(path), 'one\nTWO\n', data['mtime'], data['eol'])
        self.assertEqual(path.read_bytes(), b'one\r\nTWO\r\n')  # line endings kept
        with self.assertRaisesRegex(ValueError, 'изменился'):
            self.bridge.files.save('cc', str(path), 'x', data['mtime'])  # stale mtime
        self.bridge.files.save('cc', str(path), 'x\n', saved['mtime'] - 1, force=True)
        self.assertEqual(path.read_bytes(), b'x\n')

    async def test_grep_in_contents(self):
        ws = self.bridge.workspace
        (ws / 'src' / 'B.java').write_text('class B {\n  // TODO fix\n}\n', encoding='utf-8')
        (ws / 'node_modules').mkdir()
        (ws / 'node_modules' / 'x.js').write_text('TODO hidden', encoding='utf-8')
        status, data = await self.get('/grep?mode=cc&q=todo')
        self.assertEqual(status, 200)
        self.assertEqual([(m['path'], m['line']) for m in data['matches']], [('src/B.java', 2)])  # skips node_modules
        status, data = await self.get('/grep?mode=cc&q=TODO&case=1&regex=1')
        self.assertEqual(len(data['matches']), 1)
        status, data = await self.get('/grep?mode=cc&q=' + quote('line[') + '&regex=1')
        self.assertEqual(status, 400)

    async def test_listing_preview_and_errors(self):
        status, data = await self.get('/files?mode=cc')
        self.assertEqual(status, 200)
        self.assertEqual(data['rows'][0]['name'], 'extensions')  # folders first
        self.assertIn('src', [r['name'] for r in data['rows'] if r['dir']])
        self.assertTrue(data['roots'])
        src = str(self.bridge.workspace / 'src')
        status, data = await self.get('/files?mode=cc&path=' + quote(src))
        self.assertEqual([r['name'] for r in data['rows']], ['A.java'])
        self.assertEqual(Path(self.bridge.files.here('cc')), Path(src))  # window folder = browsing folder
        status, data = await self.get('/file?mode=cc&path=' + quote(src + '/A.java') + '&start=2')
        self.assertEqual((data['lines'], data['total'], data['start']), (['line2', 'line3'], 3, 2))
        status, data = await self.get('/file?mode=cc&path=' + quote(str(self.bridge.workspace / 'b.bin')))
        self.assertTrue(data['binary'])
        status, data = await self.get('/files?mode=cc&path=' + quote(src + '/missing'))
        self.assertEqual(status, 400)
        self.assertIn('No such folder', data['error'])


class ScreenTests(unittest.TestCase):
    """The VT emulator behind the in-game terminal."""
    def test_text_colors_cursor_and_erase(self):
        from backends.vt import Screen
        replies = []
        s = Screen(20, 4, reply=replies.append)
        s.feed('ab\x1b[31mred\x1b[0m\r\nline2\x1b[2;3H#\x1b[6n')
        snap = s.snapshot()
        self.assertEqual(snap['rows'][0], [['ab', None, None, False], ['red', 0xC50F1F, None, False]])
        self.assertEqual(''.join(r[0] for r in snap['rows'][1]), 'li#e2')
        self.assertEqual(replies, ['\x1b[2;4R'])
        s.feed('\x1b[1;1H\x1b[2K')
        self.assertEqual(s.snapshot()['rows'][0], [])

    def test_scrollback_wrap_and_alt_screen(self):
        from backends.vt import Screen
        s = Screen(10, 3)
        s.feed('1\r\n2\r\n3\r\n4\r\n0123456789AB')
        self.assertEqual(s.text().split('\n'), ['1', '2', '3', '4', '0123456789', 'AB'])
        s.feed('\x1b[?1049h\x1b[Hvim')
        self.assertEqual(''.join(r[0] for r in s.snapshot()['rows'][0]), 'vim')
        s.feed('\x1b[?1049l')
        self.assertEqual(''.join(r[0] for r in s.snapshot()['rows'][-1]), 'AB')
        self.assertEqual(s.snapshot(scroll=2)['rows'][0], [['2', None, None, False]])


class TerminalHttpTests(unittest.IsolatedAsyncioTestCase):
    """A real shell in a pseudo console through the HTTP API."""
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = Bridge(self.temp.name, AsyncMock())
        self.app = create_app(self.bridge, 't')

    async def asyncTearDown(self):
        await self.bridge.close()
        self.temp.cleanup()

    async def call(self, method, path, body=None):
        request = make_mocked_request(method, path, headers={'Authorization': 'Bearer t'})
        if body is not None:
            request.json = AsyncMock(return_value=body)
        route = await self.app.router.resolve(request)
        response = await self.app.middlewares[0](request, route.handler)
        return response.status, json.loads(response.text)

    async def test_shell_roundtrip(self):
        status, term = await self.call('POST', '/pty/open', {'mode': 'cc', 'cols': 70, 'rows': 12,
                                                             'shell': 'cmd' if os.name == 'nt' else 'sh'})
        self.assertEqual(status, 200, term)
        tid = term['id']
        await self.call('POST', '/pty/input', {'id': tid, 'data': 'echo okpty\r'})
        text = ''
        for _ in range(100):
            await asyncio.sleep(0.1)
            _, snap = await self.call('GET', f'/pty/screen?id={tid}')
            text = '\n'.join(''.join(r[0] for r in row) for row in snap['rows'])
            if text.count('okpty') >= 2:  # the typed command and its output
                break
        self.assertGreaterEqual(text.count('okpty'), 2, text)
        _, same = await self.call('GET', f"/pty/screen?id={tid}&version={snap['version']}&was=0")
        self.assertTrue(same.get('same') or same.get('version'))
        await self.call('POST', '/pty/input', {'id': tid, 'data': 'exit\r'})
        for _ in range(60):
            await asyncio.sleep(0.1)
            _, snap = await self.call('GET', f'/pty/screen?id={tid}')
            if not snap['alive']:
                break
        self.assertFalse(snap['alive'])
        _, listing = await self.call('POST', '/pty/close', {'id': tid})
        self.assertEqual(listing['terminals'], [])


class OAuthTests(unittest.IsolatedAsyncioTestCase):
    """Browser sign-in for remote MCP connectors against a local fake service."""
    async def asyncSetUp(self):
        from aiohttp import web as aweb
        from aiohttp.test_utils import TestServer
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = Bridge(self.temp.name, AsyncMock())
        self.bridge.port = 18999
        self.seen = {}
        app = aweb.Application()
        def base():
            return f'http://127.0.0.1:{self.server.port}'
        async def mcp(request):  # unauthenticated MCP call → 401 with the metadata pointer
            return aweb.Response(status=401, headers={'WWW-Authenticate':
                f'Bearer resource_metadata="{base()}/.well-known/oauth-protected-resource/mcp"'})
        async def resource(request):
            return aweb.json_response({'resource': base() + '/mcp', 'authorization_servers': [base()],
                                       'scopes_supported': ['read', 'write']})
        async def server_meta(request):
            return aweb.json_response({'authorization_endpoint': base() + '/authorize',
                                       'token_endpoint': base() + '/token', 'registration_endpoint': base() + '/register'})
        async def register(request):
            self.seen['register'] = await request.json()
            return aweb.json_response({'client_id': 'cid-1'}, status=201)
        async def token(request):
            form = dict(await request.post())
            self.seen.setdefault('token', []).append(form)
            if form['grant_type'] == 'authorization_code':
                import base64, hashlib
                challenge = base64.urlsafe_b64encode(hashlib.sha256(form['code_verifier'].encode()).digest()).rstrip(b'=').decode()
                if form['code'] != 'good-code' or challenge != self.seen['challenge']:
                    return aweb.json_response({'error': 'invalid_grant'}, status=400)
                return aweb.json_response({'access_token': 'at-1', 'refresh_token': 'rt-1', 'expires_in': 30})
            return aweb.json_response({'access_token': 'at-2', 'expires_in': 3600})
        app.router.add_post('/mcp', mcp)
        app.router.add_get('/.well-known/oauth-protected-resource/mcp', resource)
        app.router.add_get('/.well-known/oauth-authorization-server', server_meta)
        app.router.add_post('/register', register)
        app.router.add_post('/token', token)
        self.server = TestServer(app, host='127.0.0.1')
        await self.server.start_server()
        from backends.connectors import CATALOG
        self.catalog_patch = mock.patch.dict(CATALOG, {'fake': {'name': 'Fake', 'group': 'x', 'type': 'http',
                                                                 'auth': 'oauth', 'url': base() + '/mcp', 'about': ''}})
        self.catalog_patch.start()

    async def asyncTearDown(self):
        self.catalog_patch.stop()
        await self.server.close()
        await self.bridge.close()
        self.temp.cleanup()

    async def test_sign_in_refresh_and_server_config(self):
        from urllib.parse import urlparse, parse_qs
        await self.bridge.command({'mode': 'cc', 'action': 'connect', 'text': 'fake'})
        event = [e for e in self.bridge.events if e['kind'] == 'url'][-1]
        q = {k: v[0] for k, v in parse_qs(urlparse(event['url']).query).items()}
        self.assertEqual((q['client_id'], q['code_challenge_method'], q['scope']), ('cid-1', 'S256', 'read write'))
        self.assertEqual(q['redirect_uri'], 'http://127.0.0.1:18999/oauth/callback')
        self.assertEqual(self.seen['register']['redirect_uris'], [q['redirect_uri']])
        self.seen['challenge'] = q['code_challenge']
        # the browser comes back to the bridge without the bridge token
        app = create_app(self.bridge, 't')
        request = make_mocked_request('GET', f"/oauth/callback?code=good-code&state={q['state']}")
        response = await app.middlewares[0](request, None)
        self.assertEqual(response.status, 200, response.text)
        self.assertIn('fake', self.bridge.connectors.enabled)
        self.assertEqual(self.bridge.connectors.servers()['fake']['headers'], {'Authorization': 'Bearer at-1'})
        # the same state cannot be used twice
        again = await app.middlewares[0](make_mocked_request('GET', f"/oauth/callback?code=good-code&state={q['state']}"), None)
        self.assertEqual(again.status, 400)
        # expires in 30 s < margin → refreshed before the next request, refresh token kept
        self.assertEqual(await self.bridge.oauth.refresh_enabled(), [])
        self.assertEqual(self.bridge.connectors.servers()['fake']['headers'], {'Authorization': 'Bearer at-2'})
        self.assertEqual(self.bridge.oauth.stored('fake')['refresh_token'], 'rt-1')
        secrets_text = (Path(self.temp.name) / 'secrets.json').read_text(encoding='utf-8')
        self.assertNotIn('at-2', json.dumps([e for e in self.bridge.events]))
        self.assertIn('at-2', secrets_text)
        await self.bridge.command({'mode': 'cc', 'action': 'connect', 'text': 'fake signout'})
        self.assertNotIn('fake', self.bridge.connectors.enabled)
        self.assertFalse(self.bridge.oauth.has_token('fake'))


@unittest.skipUnless(shutil.which('git'), 'git is not installed')
class HunkTests(unittest.IsolatedAsyncioTestCase):
    async def test_stage_and_unstage_one_hunk(self):
        import subprocess
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = mock.patch.dict(os.environ, {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't',
                                           'GIT_COMMITTER_EMAIL': 't@t', 'GIT_CONFIG_GLOBAL': os.devnull})
        env.start()
        self.addCleanup(env.stop)
        bridge = Bridge(Path(temp.name) / 'b', AsyncMock())
        self.addAsyncCleanup(bridge.close)
        repo = Path(bridge.projects.path_of_current())
        lines = [f'line {i}' for i in range(1, 31)]
        (repo / 'f.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        git = lambda *a: subprocess.run(['git', *a], cwd=repo, capture_output=True, text=True, check=True).stdout
        git('init', '-q'); git('add', '.'); git('commit', '-qm', 'init')
        lines[1], lines[27] = 'CHANGED TOP', 'CHANGED BOTTOM'
        (repo / 'f.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        await bridge.command({'mode': 'cc', 'action': 'git', 'text': 'hunks'})
        event = [e for e in bridge.events if e['kind'] == 'git_hunks'][-1]
        self.assertEqual(len(event['unstaged']), 2)
        top = next(h for h in event['unstaged'] if '+CHANGED TOP' in h['lines'])
        await bridge.command({'mode': 'cc', 'action': 'git', 'text': 'stage ' + top['id']})
        staged = git('diff', '--cached')
        self.assertIn('+CHANGED TOP', staged)
        self.assertNotIn('CHANGED BOTTOM', staged)
        self.assertIn('+CHANGED BOTTOM', git('diff'))
        with self.assertRaises(ValueError):  # the same card again: already applied
            await bridge.command({'mode': 'cc', 'action': 'git', 'text': 'stage ' + top['id']})
        event = [e for e in bridge.events if e['kind'] == 'git_hunks'][-1]
        await bridge.command({'mode': 'cc', 'action': 'git', 'text': 'unstage ' + event['staged'][0]['id']})
        self.assertEqual(git('diff', '--cached'), '')


@unittest.skipUnless(shutil.which('git'), 'git is not installed')
class WorktreeTests(unittest.IsolatedAsyncioTestCase):
    async def test_spawn_in_worktree_merge_diff_conflict_drop(self):
        import subprocess
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = mock.patch.dict(os.environ, {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't',
                                           'GIT_COMMITTER_EMAIL': 't@t', 'GIT_CONFIG_GLOBAL': os.devnull})
        env.start()
        self.addCleanup(env.stop)
        async def runner(chat, text):  # stands in for Claude: edits files where the chat works
            folder = Path(bridge.chat_cwd(chat))
            name, _, content = text.partition('=')
            (folder / name).write_text(content, encoding='utf-8')
        bridge = Bridge(Path(temp.name) / 'b', runner)
        self.addAsyncCleanup(bridge.close)
        repo = Path(bridge.projects.path_of_current())
        git = lambda *a, cwd=repo: subprocess.run(['git', *a], cwd=cwd, capture_output=True, text=True).stdout
        (repo / 'shared.txt').write_text('base\n', encoding='utf-8')
        git('init', '-q'); git('add', '.'); git('commit', '-qm', 'init')

        async def spawn(text):
            await bridge.command({'mode': 'cc', 'action': 'spawn', 'text': text})
            event = [e for e in bridge.events if e['kind'] == 'spawned'][-1]
            target = bridge.chats[event['target']]
            while target.task is not None:
                await asyncio.sleep(0.02)
            return event, target

        event, a = await spawn('new.txt=from task A')
        self.assertTrue(event['worktree'] and event['branch'].startswith('cc/'))
        self.assertNotEqual(Path(a.worktree), repo)
        self.assertFalse((repo / 'new.txt').exists())            # the project is untouched while it runs
        self.assertTrue([e for e in bridge.events if e['kind'] == 'worktree_ready'])
        await bridge.command({'mode': 'cc', 'action': 'worktree', 'text': 'diff ' + a.id})
        self.assertIn('+from task A', [e for e in bridge.events if e['kind'] == 'diff'][-1]['text'])
        await bridge.command({'mode': 'cc', 'action': 'worktree', 'text': 'merge ' + a.id})
        self.assertEqual((repo / 'new.txt').read_text(encoding='utf-8'), 'from task A')
        self.assertIsNone(a.worktree)
        self.assertNotIn('cc/', git('branch'))

        _, b = await spawn('shared.txt=B\n')
        _, c = await spawn('shared.txt=C\n')
        await bridge.command({'mode': 'cc', 'action': 'worktree', 'text': 'merge ' + b.id})
        with self.assertRaises(ValueError) as caught:
            await bridge.command({'mode': 'cc', 'action': 'worktree', 'text': 'merge ' + c.id})
        self.assertIn('shared.txt', str(caught.exception))
        self.assertEqual((repo / 'shared.txt').read_text(encoding='utf-8'), 'B')   # aborted cleanly
        self.assertEqual(git('status', '--porcelain'), '')
        await bridge.command({'mode': 'cc', 'action': 'worktree', 'text': 'drop ' + c.id})
        self.assertIsNone(c.worktree)
        self.assertEqual(git('worktree', 'list').count('\n'), 1)
        await bridge.command({'mode': 'cc', 'action': 'spawn', 'text': '--here x.txt=inline'})
        here = bridge.chats[[e for e in bridge.events if e['kind'] == 'spawned'][-1]['target']]
        self.assertIsNone(here.worktree)


@unittest.skipUnless(shutil.which('git'), 'git is not installed')
class GraphBlameTests(unittest.IsolatedAsyncioTestCase):
    async def test_graph_and_blame(self):
        import subprocess
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = mock.patch.dict(os.environ, {'GIT_AUTHOR_NAME': 'Alex', 'GIT_AUTHOR_EMAIL': 'a@a', 'GIT_COMMITTER_NAME': 'Alex',
                                           'GIT_COMMITTER_EMAIL': 'a@a', 'GIT_CONFIG_GLOBAL': os.devnull})
        env.start()
        self.addCleanup(env.stop)
        bridge = Bridge(Path(temp.name) / 'b', AsyncMock())
        self.addAsyncCleanup(bridge.close)
        repo = Path(bridge.projects.path_of_current())
        git = lambda *a: subprocess.run(['git', *a], cwd=repo, capture_output=True, text=True, check=True)
        (repo / 'a.txt').write_text('one\ntwo\n', encoding='utf-8')
        git('init', '-q'); git('add', '.'); git('commit', '-qm', 'first commit')
        git('checkout', '-qb', 'feature')
        (repo / 'a.txt').write_text('one\ntwo\nthree\n', encoding='utf-8')
        git('commit', '-qam', 'add three')
        await bridge.command({'mode': 'cc', 'action': 'git', 'text': 'graph'})
        rows = [e for e in bridge.events if e['kind'] == 'git_graph'][-1]['rows']
        self.assertEqual([r['subject'] for r in rows if r['hash']], ['add three', 'first commit'])
        self.assertIn('feature', rows[0]['refs'])
        self.assertTrue(rows[0]['graph'].startswith('*'))
        blame = await bridge.git.blame(repo / 'a.txt')
        self.assertEqual([(b['author'], b['summary']) for b in blame],
                         [('Alex', 'first commit'), ('Alex', 'first commit'), ('Alex', 'add three')])


@unittest.skipUnless(shutil.which('git'), 'git is not installed')
class ConflictTests(unittest.IsolatedAsyncioTestCase):
    async def test_pick_sides_per_block_and_claude(self):
        import subprocess
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = mock.patch.dict(os.environ, {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't',
                                           'GIT_COMMITTER_EMAIL': 't@t', 'GIT_CONFIG_GLOBAL': os.devnull})
        env.start()
        self.addCleanup(env.stop)
        bridge = Bridge(Path(temp.name) / 'b', AsyncMock())
        self.addAsyncCleanup(bridge.close)
        repo = Path(bridge.projects.path_of_current())
        git = lambda *a: subprocess.run(['git', *a], cwd=repo, capture_output=True, text=True)
        lines = ['top', 'a', 'b', 'c', 'd', 'e', 'f', 'g', 'bottom']
        (repo / 'f.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        git('init', '-q', '-b', 'main'); git('add', '.'); git('commit', '-qm', 'base')
        git('checkout', '-qb', 'other')
        (repo / 'f.txt').write_text('\n'.join(['TOP-THEIRS'] + lines[1:-1] + ['BOTTOM-THEIRS']) + '\n', encoding='utf-8')
        git('commit', '-qam', 'theirs')
        git('checkout', '-q', 'main')
        (repo / 'f.txt').write_text('\n'.join(['TOP-OURS'] + lines[1:-1] + ['BOTTOM-OURS']) + '\n', encoding='utf-8')
        git('commit', '-qam', 'ours')
        git('merge', 'other')                                        # two conflict blocks
        await bridge.command({'mode': 'cc', 'action': 'git', 'text': 'conflicts'})
        card = [e for e in bridge.events if e['kind'] == 'git_conflicts'][-1]
        self.assertTrue(card['merging'])
        self.assertEqual([len(f['blocks']) for f in card['files']], [2])
        await bridge.command({'mode': 'cc', 'action': 'git', 'text': 'pick f.txt 0 ours'})
        await bridge.command({'mode': 'cc', 'action': 'git', 'text': 'pick f.txt 0 theirs'})   # what is left is block 0 again
        text = (repo / 'f.txt').read_text(encoding='utf-8')
        self.assertIn('TOP-OURS', text)
        self.assertIn('BOTTOM-THEIRS', text)
        self.assertNotIn('<<<<<<<', text)
        self.assertEqual(git('diff', '--name-only', '--diff-filter=U').stdout, '')   # staged
        with self.assertRaises(ValueError):
            await bridge.command({'mode': 'cc', 'action': 'git', 'text': 'resolve'})  # nothing left for Claude
        from backends.conflicts import parse, resolve
        sample = 'x\n<<<<<<< HEAD\nours\n||||||| base\nold\n=======\ntheirs\n>>>>>>> branch\ny'
        self.assertEqual(parse(sample)[0]['base'], ['old'])
        self.assertEqual(resolve(sample, 0, 'both'), 'x\nours\ntheirs\ny')


class ReplaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_apply_stale_and_undo(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        bridge = Bridge(Path(temp.name) / 'b', AsyncMock())
        self.addAsyncCleanup(bridge.close)
        project = Path(bridge.projects.path_of_current())
        (project / 'src').mkdir()
        (project / 'src' / 'A.java').write_bytes(b'class A {\r\n  int oldName = 1; // oldName\r\n}\r\n')
        (project / 'src' / 'b.py').write_text('oldName = 2\n', encoding='utf-8')
        (project / 'node_modules').mkdir()
        (project / 'node_modules' / 'x.js').write_text('oldName', encoding='utf-8')
        await bridge.command({'mode': 'cc', 'action': 'replace', 'text': '--in *.java oldName -> newName'})
        p = [e for e in bridge.events if e['kind'] == 'replace_preview'][-1]
        self.assertEqual((p['count'], p['files']), (2, 1))
        self.assertEqual(p['changes'][0]['after'], 'int newName = 1; // newName')
        await bridge.command({'mode': 'cc', 'action': 'replace', 'text': 'apply ' + p['token']})
        self.assertEqual((project / 'src' / 'A.java').read_bytes(), b'class A {\r\n  int newName = 1; // newName\r\n}\r\n')
        self.assertEqual((project / 'src' / 'b.py').read_text(encoding='utf-8'), 'oldName = 2\n')
        self.assertEqual((project / 'node_modules' / 'x.js').read_text(encoding='utf-8'), 'oldName')
        # regex with groups; a file edited after the preview blocks apply
        await bridge.command({'mode': 'cc', 'action': 'replace', 'text': r'-r (\w+)Name = (\d) -> \1_name = \2'})
        p2 = [e for e in bridge.events if e['kind'] == 'replace_preview'][-1]
        self.assertEqual({c['after'] for c in p2['changes']}, {'old_name = 2', 'int new_name = 1; // newName'})
        (project / 'src' / 'b.py').write_text('oldName = 3\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            await bridge.command({'mode': 'cc', 'action': 'replace', 'text': 'apply ' + p2['token']})
        # /cc undo restores the first replacement
        await bridge.command({'mode': 'cc', 'action': 'undo'})
        self.assertIn(b'oldName', (project / 'src' / 'A.java').read_bytes())


class PermissionRuleTests(unittest.IsolatedAsyncioTestCase):
    def test_matching(self):
        from backends.permrules import matches, suggest
        project = tempfile.gettempdir()
        p = lambda rel: str(Path(project) / rel)
        cases = [
            ('Bash', 'Bash', {'command': 'rm -rf x'}, True),
            ('Bash(git *)', 'Bash', {'command': 'git push origin main'}, True),
            ('Bash(git *)', 'Bash', {'command': 'gitk'}, False),
            ('Bash(npm run test:*)', 'Bash', {'command': 'npm run test -- --watch'}, True),
            ('Bash(npm run test:*)', 'Bash', {'command': 'npm run build'}, False),
            ('Bash(git status)', 'Bash', {'command': 'git  status'}, True),
            ('Edit(src/**)', 'Write', {'file_path': p('src/a/b/C.java')}, True),
            ('Edit(src/**)', 'Edit', {'file_path': p('docs/x.md')}, False),
            ('Edit(*.md)', 'Edit', {'file_path': p('docs/x.md')}, True),
            ('Read(**/.env)', 'Read', {'file_path': p('app/.env')}, True),
            ('Read(**/.env)', 'Grep', {'path': p('.env')}, True),
            ('WebFetch(domain:github.com)', 'WebFetch', {'url': 'https://api.github.com/x'}, True),
            ('WebFetch(domain:github.com)', 'WebFetch', {'url': 'https://evilgithub.com/'}, False),
            ('mcp__github', 'mcp__github__create_issue', {}, True),
            ('mcp__github__get_me', 'mcp__github__create_issue', {}, False),
        ]
        for rule, name, data, expected in cases:
            self.assertEqual(matches(rule, name, data, project), expected, (rule, name, data))
        self.assertEqual(suggest('Bash', {'command': 'git push origin'}, project), 'Bash(git push:*)')
        self.assertEqual(suggest('Bash', {'command': 'ls -la'}, project), 'Bash(ls:*)')
        self.assertEqual(suggest('Write', {'file_path': p('src/ui/A.java')}, project), 'Edit(src/ui/**)')

    async def test_rules_file_deny_wins_and_command(self):
        from backends import permrules
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = mock.patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(Path(temp.name) / 'home')})
        env.start()
        self.addCleanup(env.stop)
        bridge = Bridge(Path(temp.name) / 'b', AsyncMock())
        self.addAsyncCleanup(bridge.close)
        project = Path(bridge.projects.path_of_current())
        (project / '.claude').mkdir()
        (project / '.claude' / 'settings.json').write_text(json.dumps({'model': 'keep-me'}), encoding='utf-8')
        await bridge.command({'mode': 'cc', 'action': 'perms', 'text': 'allow Bash(git *)'})
        await bridge.command({'mode': 'cc', 'action': 'perms', 'text': 'deny Bash(git push:*)'})
        saved = json.loads((project / '.claude' / 'settings.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['model'], 'keep-me')
        self.assertEqual(saved['permissions'], {'allow': ['Bash(git *)'], 'deny': ['Bash(git push:*)']})
        self.assertEqual(permrules.decide('Bash', {'command': 'git push'}, project)[0], 'deny')
        self.assertEqual(permrules.decide('Bash', {'command': 'git log'}, project)[0], 'allow')
        self.assertEqual(permrules.decide('Bash', {'command': 'ls'}, project)[0], None)
        await bridge.command({'mode': 'cc', 'action': 'perms', 'text': 'rm Bash(git push:*)'})
        self.assertEqual(permrules.decide('Bash', {'command': 'git push'}, project)[0], 'allow')
        event = [e for e in bridge.events if e['kind'] == 'perms'][-1]
        self.assertEqual(event['rules']['allow'], [{'rule': 'Bash(git *)', 'source': 'project'}])
        with self.assertRaises(ValueError):
            await bridge.command({'mode': 'cc', 'action': 'perms', 'text': 'allow ((bad'})


class SlashCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = Bridge(Path(self.temp.name) / 'b', AsyncMock())
        self.user = Path(self.temp.name) / 'claude-home'
        (self.user / 'commands').mkdir(parents=True)
        (self.user / 'commands' / 'hello.md').write_text('Say hello to $1 and $2', encoding='utf-8')
        (self.user / 'commands' / 'fix.md').write_text('user version', encoding='utf-8')
        project = Path(self.bridge.projects.path_of_current())
        (project / '.claude' / 'commands' / 'front').mkdir(parents=True)
        (project / '.claude' / 'commands' / 'fix.md').write_text(
            '---\ndescription: Fix an issue\nargument-hint: <issue>\n---\nFix issue $ARGUMENTS carefully.', encoding='utf-8')
        (project / '.claude' / 'commands' / 'front' / 'test.md').write_text('# Run UI tests\nnpm test', encoding='utf-8')
        self.env = mock.patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(self.user)})
        self.env.start()

    async def asyncTearDown(self):
        self.env.stop()
        await self.bridge.close()
        self.temp.cleanup()

    def prompts(self):
        return [call.args[1] for call in self.bridge.runner.await_args_list]

    async def test_project_user_builtin_and_actions(self):
        await self.bridge.command({'mode': 'cc', 'action': 'commands'})
        rows = {r['name']: r for r in [e for e in self.bridge.events if e['kind'] == 'commands'][-1]['rows']}
        self.assertEqual((rows['fix']['source'], rows['fix']['description'], rows['fix']['hint']),
                         ('project', 'Fix an issue', '<issue>'))
        self.assertEqual(rows['front:test']['description'], 'Run UI tests')
        self.assertEqual(rows['hello']['source'], 'user')
        self.assertEqual(rows['review']['source'], 'builtin')
        await self.bridge.command({'mode': 'cc', 'action': '/fix', 'text': '#12 login'})
        await self.bridge.command({'mode': 'cc', 'action': 'slash', 'text': 'hello Alex Steve'})
        await self.bridge.command({'mode': 'cc', 'action': 'slash', 'text': 'front:test --watch'})
        for _ in range(50):
            if len(self.prompts()) == 3:
                break
            await asyncio.sleep(0.02)
        self.assertEqual(self.prompts(), ['Fix issue #12 login carefully.', 'Say hello to Alex and Steve',
                                          '# Run UI tests\nnpm test\n\n--watch'])
        await self.bridge.command({'mode': 'cc', 'action': 'slash', 'text': 'cost'})   # built-in action
        self.assertEqual([e for e in self.bridge.events][-1]['kind'], 'tokens')
        with self.assertRaises(ValueError):
            await self.bridge.command({'mode': 'cc', 'action': 'slash', 'text': 'nope'})


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_parse_limits_day_rollover_and_chat_pause(self):
        from backends.budget import parse_amount
        self.assertEqual(parse_amount('$0.50'), ('usd', 0.5))
        self.assertEqual(parse_amount('2.5$'), ('usd', 2.5))
        self.assertEqual(parse_amount('200k'), ('tokens', 200000))
        self.assertEqual(parse_amount('2M'), ('tokens', 2000000))
        self.assertEqual(parse_amount('off'), (None, None))
        with self.assertRaises(ValueError):
            parse_amount('lots')
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        bridge = Bridge(temp.name, AsyncMock())
        self.addAsyncCleanup(bridge.close)
        chat = bridge.current_chat('cc')
        await bridge.command({'mode': 'cc', 'action': 'budget', 'text': 'chat $1'})
        bridge.add_usage(chat, {'input_tokens': 10}, 1.2)          # over the chat limit
        self.assertTrue(chat.paused)
        await bridge.enqueue(chat, 'waits')
        self.assertIsNone(chat.task)
        self.assertEqual(list(chat.queue), ['waits'])
        self.assertEqual(bridge.budget.day['usd'], 1.2)
        bridge.budget.day['date'] = '2000-01-01'                    # a new day starts from zero
        self.assertEqual(bridge.budget.spent(chat)['day']['usd'], 0.0)
        await bridge.command({'mode': 'cc', 'action': 'budget', 'text': 'resume'})
        for _ in range(50):
            if bridge.runner.await_count:
                break
            await asyncio.sleep(0.02)
        self.assertEqual(bridge.runner.await_args.args[1], 'waits')


class PullRequestTests(unittest.IsolatedAsyncioTestCase):
    """PRs and issues through a fake `gh` on PATH (no GitHub access)."""
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        tools = Path(self.temp.name) / 'tools'
        tools.mkdir()
        fixtures = {
            'pr list': [{'number': 7, 'title': 'Add login', 'headRefName': 'login', 'baseRefName': 'main', 'isDraft': True,
                         'url': 'https://github.com/o/r/pull/7', 'author': {'login': 'alex'}, 'reviewDecision': 'CHANGES_REQUESTED',
                         'statusCheckRollup': [{'name': 'build', 'conclusion': 'FAILURE'}, {'name': 'lint', 'conclusion': 'SUCCESS'}]}],
            'pr view': {'number': 7, 'title': 'Add login', 'headRefName': 'login', 'baseRefName': 'main', 'isDraft': True,
                        'url': 'https://github.com/o/r/pull/7', 'state': 'OPEN', 'body': 'Adds /login',
                        'statusCheckRollup': [{'name': 'build', 'status': 'IN_PROGRESS'}],
                        'comments': [{'author': {'login': 'sam'}, 'body': 'Please add a test'}],
                        'reviews': [{'author': {'login': 'kim'}, 'body': 'Rename the flag', 'state': 'CHANGES_REQUESTED'}]},
            'api': [{'user': {'login': 'kim'}, 'body': 'typo here', 'path': 'src/Login.java', 'line': 12}],
            'issue list': [{'number': 3, 'title': 'Crash on start', 'labels': [{'name': 'bug'}], 'url': 'https://github.com/o/r/issues/3'}],
            'issue view': {'number': 3, 'title': 'Crash on start', 'body': 'NPE in Main', 'url': 'u', 'labels': [],
                           'comments': [{'author': {'login': 'sam'}, 'body': 'stack trace attached'}]},
        }
        (tools / 'fixtures.json').write_text(json.dumps(fixtures), encoding='utf-8')
        script = tools / 'fake_gh.py'
        script.write_text('import json, sys, pathlib\n'
                          'f = json.loads((pathlib.Path(__file__).parent / "fixtures.json").read_text())\n'
                          'a = sys.argv[1:]\nkey = a[0] if a[0] == "api" else a[0] + " " + a[1]\n'
                          'print(json.dumps(f[key]))\n', encoding='utf-8')
        if os.name == 'nt':
            (tools / 'gh.cmd').write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding='utf-8')
        else:
            (tools / 'gh').write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding='utf-8')
            (tools / 'gh').chmod(0o755)
        self.env = mock.patch.dict(os.environ, {'PATH': str(tools) + os.pathsep + os.environ.get('PATH', '')})
        self.env.start()
        self.bridge = Bridge(Path(self.temp.name) / 'b', AsyncMock())

    async def asyncTearDown(self):
        self.env.stop()
        await self.bridge.close()
        self.temp.cleanup()

    async def test_pr_list_view_and_claude_tasks(self):
        await self.bridge.command({'mode': 'cc', 'action': 'pr', 'text': ''})
        row = [e for e in self.bridge.events if e['kind'] == 'pr_list'][-1]['rows'][0]
        self.assertEqual((row['number'], row['draft'], row['checks']['state'], row['checks']['failed']), (7, True, 'failing', ['build']))
        await self.bridge.command({'mode': 'cc', 'action': 'pr', 'text': '7'})
        view = [e for e in self.bridge.events if e['kind'] == 'pr_view'][-1]
        self.assertEqual(view['checks']['state'], 'pending')
        self.assertEqual([(c['author'], c['where']) for c in view['comments']],
                         [('sam', ''), ('kim', 'CHANGES_REQUESTED'), ('kim', 'src/Login.java:12')])
        await self.bridge.command({'mode': 'cc', 'action': 'pr', 'text': 'reply 7'})
        await self.bridge.command({'mode': 'cc', 'action': 'pr', 'text': 'create main'})
        for _ in range(100):
            if self.bridge.runner.await_count >= 2:
                break
            await asyncio.sleep(0.02)
        prompts = [c.args[1] for c in self.bridge.runner.await_args_list]
        self.assertIn('typo here', prompts[0])
        self.assertIn('gh pr comment', prompts[0])
        self.assertIn('gh pr create --draft', prompts[1])

    async def test_issue_list_and_do(self):
        await self.bridge.command({'mode': 'cc', 'action': 'issues', 'text': ''})
        rows = [e for e in self.bridge.events if e['kind'] == 'issue_list'][-1]['rows']
        self.assertEqual((rows[0]['number'], rows[0]['labels']), (3, ['bug']))
        await self.bridge.command({'mode': 'cc', 'action': 'issue', 'text': 'do 3'})
        for _ in range(100):
            if self.bridge.runner.await_count:
                break
            await asyncio.sleep(0.02)
        prompt = self.bridge.runner.await_args.args[1]
        self.assertIn('NPE in Main', prompt)
        self.assertIn('issue-3-', prompt)
        self.assertIn('Closes #3', prompt)


class OtherModelTests(unittest.IsolatedAsyncioTestCase):
    """Ollama / LM Studio / OpenRouter through a fake OpenAI-compatible server (SSE streaming)."""
    async def asyncSetUp(self):
        from aiohttp import web as aweb
        from aiohttp.test_utils import TestServer
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = Bridge(Path(self.temp.name) / 'b')
        self.requests = []
        async def models(request):
            self.requests.append(('models', dict(request.headers)))
            return aweb.json_response({'data': [{'id': 'llama3.2', 'context_length': 131072}, {'id': 'qwen2.5-coder'}]})
        async def chat(request):
            body = await request.json()
            self.requests.append(('chat', dict(request.headers), body))
            response = aweb.StreamResponse(headers={'Content-Type': 'text/event-stream'})
            await response.prepare(request)
            for piece in ['Hello', ' from ', body['model'], '\nsecond', ' line']:
                await response.write(f'data: {json.dumps({"choices": [{"delta": {"content": piece}}]})}\n\n'.encode())
            await response.write(b'data: ' + json.dumps({'choices': [], 'usage': {'prompt_tokens': 12, 'completion_tokens': 5}}).encode() + b'\n\n')
            await response.write(b'data: [DONE]\n\n')
            return response
        app = aweb.Application()
        app.router.add_get('/v1/models', models)
        app.router.add_post('/v1/chat/completions', chat)
        self.server = TestServer(app, host='127.0.0.1')
        await self.server.start_server()
        url = f'http://127.0.0.1:{self.server.port}/v1'
        self.bridge.config['providers'] = {'ollama': {'url': url}, 'openrouter': {'url': url}}

    async def asyncTearDown(self):
        await self.server.close()
        await self.bridge.close()
        self.temp.cleanup()

    async def wait(self, chat):
        for _ in range(200):
            if chat.task is None:
                return
            await asyncio.sleep(0.02)
        self.fail('timeout')

    async def test_ollama_models_stream_history_and_openrouter_key(self):
        await self.bridge.command({'mode': 'c', 'action': 'models', 'text': 'ollama'})
        card = [e for e in self.bridge.events if e['kind'] == 'models'][-1]
        self.assertEqual([r['id'] for r in card['rows']], ['llama3.2', 'qwen2.5-coder'])
        await self.bridge.command({'mode': 'c', 'action': 'new', 'text': 'ollama qwen2.5-coder'})
        chat = self.bridge.current_chat('c')
        self.assertEqual((chat.backend, chat.model), ('ollama', 'qwen2.5-coder'))
        await self.bridge.enqueue(chat, 'hi')
        await self.wait(chat)
        parts = [e['text'] for e in self.bridge.events if e['kind'] == 'text_part']
        self.assertEqual(parts, ['Hello from qwen2.5-coder', 'second line'])
        await self.bridge.enqueue(chat, 'and again')
        await self.wait(chat)
        sent = self.requests[-1][2]['messages']
        self.assertEqual([m['role'] for m in sent], ['system', 'user', 'assistant', 'user'])
        self.assertEqual(chat.usage['input_tokens'], 24)
        with self.assertRaises(ValueError):
            await self.bridge.command({'mode': 'cc', 'action': 'new', 'text': 'ollama'})   # /cc stays Claude Code
        await self.bridge.command({'mode': 'c', 'action': 'new', 'text': 'openrouter some/model'})
        router = self.bridge.current_chat('c')
        await self.bridge.enqueue(router, 'x')
        await self.wait(router)
        self.assertTrue(any('openrouter-key' in e['text'] for e in self.bridge.events if e['kind'] == 'error'))
        await self.bridge.command({'mode': 'c', 'action': 'openrouter-key', 'text': 'sk-or-test'})
        await self.bridge.enqueue(router, 'y')
        await self.wait(router)
        self.assertEqual(self.requests[-1][1].get('Authorization'), 'Bearer sk-or-test')
        self.assertNotIn('sk-or-test', json.dumps(list(self.bridge.events)))


class AgentFileTests(unittest.IsolatedAsyncioTestCase):
    async def test_export_edit_new_import(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        bridge = Bridge(Path(temp.name) / 'b', AsyncMock())
        self.addAsyncCleanup(bridge.close)
        project = Path(bridge.projects.path_of_current())
        await bridge.command({'mode': 'cc', 'action': 'agent', 'text': 'edit code-reviewer'})
        opened = [e for e in bridge.events if e['kind'] == 'open_editor'][-1]['path']
        self.assertEqual(Path(opened), project / '.claude' / 'agents' / 'code-reviewer.md')
        text = Path(opened).read_text(encoding='utf-8')
        self.assertTrue(text.startswith('---\nname: code-reviewer\n'))
        Path(opened).write_text(text.replace('senior code reviewer', 'EDITED reviewer'), encoding='utf-8')
        self.assertIn('EDITED reviewer', bridge.agents.enabled(str(project))['code-reviewer']['prompt'])   # project file wins
        self.assertEqual([r['source'] for r in bridge.agents.rows() if r['name'] == 'code-reviewer'], ['проект'])
        await bridge.command({'mode': 'cc', 'action': 'agent', 'text': 'file my-helper'})
        self.assertIn('my-helper', bridge.agents.enabled(str(project)))
        other = Path(temp.name) / 'other.md'
        other.write_text('---\nname: imported-one\ndescription: From elsewhere\ntools: Read, Grep\n---\nDo things.', encoding='utf-8')
        await bridge.command({'mode': 'cc', 'action': 'agent', 'text': f'import {other}'})
        agent = bridge.agents.enabled(str(project))['imported-one']
        self.assertEqual((agent['description'], agent['tools']), ('From elsewhere', ['Read', 'Grep']))
        with self.assertRaises(ValueError):
            await bridge.command({'mode': 'cc', 'action': 'agent', 'text': 'file Bad Name'})


class CrossReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_review_goes_to_the_other_bridge_or_a_subagent(self):
        from aiohttp.test_utils import TestServer
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        config = Path(temp.name) / 'config'
        claude = Bridge(config / 'claude-chat', AsyncMock())
        self.addAsyncCleanup(claude.close)
        # no peer yet: an independent subagent review in the same chat
        await claude.command({'mode': 'cc', 'action': 'crossreview', 'text': 'security'})
        for _ in range(50):
            if claude.runner.await_count:
                break
            await asyncio.sleep(0.02)
        self.assertIn('code-reviewer subagent', claude.runner.await_args.args[1])
        self.assertIn('Focus: security', claude.runner.await_args.args[1])
        # the other mod's bridge is running next to it
        codex = Bridge(config / 'codexcraft', AsyncMock())
        self.addAsyncCleanup(codex.close)
        server = TestServer(create_app(codex, 'peer-token'), host='127.0.0.1')
        await server.start_server()
        self.addAsyncCleanup(server.close)
        (config / 'codexcraft' / 'connection.json').write_text(json.dumps({'port': server.port, 'token': 'peer-token'}),
                                                               encoding='utf-8')
        await claude.command({'mode': 'cc', 'action': 'crossreview', 'text': ''})
        for _ in range(100):
            if codex.runner.await_count:
                break
            await asyncio.sleep(0.02)
        prompt = codex.runner.await_args.args[1]
        self.assertIn('Cross-review: Claude changed files', prompt)
        self.assertIn(str(claude.chat_cwd(claude.current_chat('cc'))), prompt)
        self.assertEqual(claude.runner.await_count, 1)               # not reviewed locally this time
        self.assertTrue(any('Codexcraft (/codex)' in e['text'] for e in claude.events if e['kind'] == 'status'))


class WatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_rerun_on_change_failure_card_fix_button_and_recovery(self):
        from backends import watch
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        bridge = Bridge(Path(temp.name) / 'b', AsyncMock())
        self.addAsyncCleanup(bridge.close)
        project = Path(bridge.projects.path_of_current())
        (project / 'state.txt').write_text('bad', encoding='utf-8')
        command = f'"{sys.executable}" -c "import sys; print(\'checking\'); sys.exit(0 if open(\'state.txt\').read() == \'good\' else 3)"'
        chat = bridge.current_chat('cc')
        watcher = watch.Watcher(bridge, chat.id, project, command, interval=0.1, settle=0.2).start()
        bridge.watchers[str(project)] = watcher
        async def until(pred):
            for _ in range(200):
                if pred():
                    return
                await asyncio.sleep(0.05)
            self.fail('timeout')
        await until(lambda: any(e['kind'] == 'watch_failed' for e in bridge.events))
        failed = [e for e in bridge.events if e['kind'] == 'watch_failed'][-1]
        self.assertEqual(failed['code'], 3)
        self.assertIn('checking', failed['output'])
        bridge.runner.assert_not_awaited()                        # nothing goes to Claude without the click
        await bridge.command({'mode': 'cc', 'action': 'watch', 'text': 'fix'})
        await until(lambda: bridge.runner.await_count == 1)
        self.assertIn('checking', bridge.runner.await_args.args[1])
        (project / 'state.txt').write_text('good', encoding='utf-8')   # a change → rerun → passes again
        await until(lambda: any(e['kind'] == 'watch_ok' for e in bridge.events))
        await bridge.command({'mode': 'cc', 'action': 'watch', 'text': 'stop'})
        self.assertNotIn(str(project), bridge.watchers)


class LspTests(unittest.IsolatedAsyncioTestCase):
    async def test_diagnostics_definition_completion_with_a_real_protocol_server(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        bridge = Bridge(Path(temp.name) / 'b', AsyncMock())
        self.addAsyncCleanup(bridge.close)
        bridge.config['lsp'] = {'.py': [sys.executable, str(Path(__file__).with_name('fake_lsp.py'))]}
        project = Path(bridge.projects.path_of_current())
        app = create_app(bridge, 't')
        async def post(op, body):
            request = make_mocked_request('POST', f'/lsp/{op}', headers={'Authorization': 'Bearer t'})
            request.json = AsyncMock(return_value={'mode': 'cc', 'path': str(project / 'main.py'), **body})
            route = await app.router.resolve(request)
            r = await app.middlewares[0](request, route.handler)
            return r.status, json.loads(r.text)
        text = 'def helper():\n    pass\nx = helper()\ny = error_here\nhel'
        status, data = await post('sync', {'text': text})
        self.assertEqual(status, 200, data)
        self.assertEqual([(d['line'], d['col'], d['message']) for d in data['diagnostics']],
                         [(3, 4, 'found error on line 4')])
        status, data = await post('definition', {'text': text, 'line': 2, 'col': 6})
        self.assertEqual(data['locations'], [{'path': str((project / 'main.py').resolve()), 'line': 1}])
        status, data = await post('complete', {'text': text, 'line': 4, 'col': 3})
        self.assertEqual([i['label'] for i in data['items']], ['helper'])
        status, data = await post('sync', {'text': text.replace('error_here', 'fine')})   # didChange
        self.assertEqual(data['diagnostics'], [])
        bridge.config['lsp'] = {}
        status, data = await post('sync', {'text': 'x', 'path': str(project / 'a.go')})
        self.assertEqual(status, 400)
        self.assertIn('gopls', data['error'])


class FormatterTests(unittest.IsolatedAsyncioTestCase):
    async def test_formatter_from_path_and_missing_hint(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        tools = Path(temp.name) / 'tools'
        tools.mkdir()
        script = tools / 'fake_ruff.py'
        script.write_text('import sys\nsys.stdin.reconfigure(encoding="utf-8")\n'
                          'print("ARGS", " ".join(sys.argv[1:3]))\nsys.stdout.write(sys.stdin.read().upper())\n', encoding='utf-8')
        if os.name == 'nt':
            (tools / 'ruff.cmd').write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding='utf-8')
        else:
            (tools / 'ruff').write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding='utf-8')
            (tools / 'ruff').chmod(0o755)
        bridge = Bridge(Path(temp.name) / 'b', AsyncMock())
        self.addAsyncCleanup(bridge.close)
        app = create_app(bridge, 't')
        async def post(body):
            request = make_mocked_request('POST', '/format', headers={'Authorization': 'Bearer t'})
            request.json = AsyncMock(return_value=body)
            route = await app.router.resolve(request)
            r = await app.middlewares[0](request, route.handler)
            return r.status, json.loads(r.text)
        with mock.patch.dict(os.environ, {'PATH': str(tools) + os.pathsep + os.environ.get('PATH', '')}):
            status, data = await post({'mode': 'cc', 'path': 'x.py', 'text': 'a = 1\n'})
        self.assertEqual(status, 200, data)
        self.assertEqual(data['tool'], 'ruff')
        self.assertIn('ARGS format --stdin-filename', data['text'])
        self.assertIn('A = 1', data['text'])
        with mock.patch('shutil.which', lambda *a, **k: None):
            status, data = await post({'mode': 'cc', 'path': 'x.py', 'text': 'a'})
        self.assertEqual(status, 400)
        self.assertIn('pip install ruff', data['error'])


class LedgerTests(unittest.IsolatedAsyncioTestCase):
    async def test_usage_by_day_project_model(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        bridge = Bridge(temp.name, AsyncMock())
        self.addAsyncCleanup(bridge.close)
        chat = bridge.current_chat('cc')
        bridge.add_usage(chat, {'input_tokens': 1000, 'output_tokens': 200}, 0.30, 'claude-opus-x')
        bridge.add_usage(chat, {'input_tokens': 500, 'cache_read_input_tokens': 500, 'output_tokens': 0}, 0.10, 'claude-haiku-x')
        bridge.add_usage(bridge.current_chat('c'), {'input_tokens': 10, 'output_tokens': 10}, 0.0)
        app = create_app(bridge, 't')
        request = make_mocked_request('GET', '/usage?days=7', headers={'Authorization': 'Bearer t'})
        route = await app.router.resolve(request)
        data = json.loads((await app.middlewares[0](request, route.handler)).text)
        self.assertEqual(len(data['days']), 7)
        self.assertEqual((data['days'][-1]['tokens'], data['days'][-1]['cost']), (2220, 0.4))
        self.assertEqual([m['key'] for m in data['models']], ['claude-opus-x', 'claude-haiku-x', 'default'])
        self.assertEqual({p['key'] for p in data['projects']}, {bridge.projects.current, 'chat'})
        self.assertEqual(data['total']['turns'], 3)


class StatusLineTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_in_state(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        bridge = Bridge(temp.name, AsyncMock())
        self.addAsyncCleanup(bridge.close)
        chat = bridge.current_chat('cc')
        await bridge.command({'mode': 'cc', 'action': 'model', 'text': 'opus'})
        chat.context = 100000
        bridge.add_usage(chat, {'input_tokens': 1200, 'output_tokens': 300}, 0.05)
        bridge.rate_limits['five_hour'] = {'status': 'allowed', 'utilization': 0.42, 'resets_at': None}
        app = create_app(bridge, 't')
        request = make_mocked_request('GET', '/state', headers={'Authorization': 'Bearer t'})
        route = await app.router.resolve(request)
        status = json.loads((await app.middlewares[0](request, route.handler)).text)['status']['cc']
        self.assertEqual((status['model'], status['context'], status['tokens'], status['cost']), ('opus', 50, 1500, 0.05))
        self.assertEqual(status['limits'], {'five_hour': 0.42})
        self.assertFalse(status['running'])


class DoctorTests(unittest.IsolatedAsyncioTestCase):
    async def test_report_rows_and_english(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        bridge = Bridge(temp.name, AsyncMock())
        self.addAsyncCleanup(bridge.close)
        bridge.account.status = AsyncMock(return_value={'account': {'logged_in': False}})
        with mock.patch('shutil.which', lambda name, *a, **k: None):
            await bridge.command({'mode': 'cc', 'action': 'doctor'})
        event = [e for e in bridge.events if e['kind'] == 'doctor'][-1]
        rows = {r['name']: r for r in event['rows']}
        self.assertEqual(rows['Python']['status'], 'ok')
        self.assertEqual(rows['git']['status'], 'fail')
        self.assertEqual(rows['git']['value'], 'not found')          # translated (English default)
        self.assertIn('git-scm.com', rows['git']['hint'])
        self.assertEqual(rows['Claude account']['status'], 'warn')
        self.assertEqual(event['overall'], 'fail')
        self.assertNotIn('token', json.dumps(event).lower())


class ModelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = Bridge(self.temp.name, AsyncMock())

    async def asyncTearDown(self):
        await self.bridge.close()
        self.temp.cleanup()

    async def test_model_and_effort_per_chat(self):
        cmd = lambda a, t: self.bridge.command({'mode': 'cc', 'action': a, 'text': t})
        await cmd('model', 'opus')
        await cmd('effort', 'max')
        chat = self.bridge.current_chat('cc')
        self.assertEqual((chat.model, chat.effort), ('opus', 'max'))
        self.assertEqual(self.bridge.events[-1]['kind'], 'model')
        await cmd('model', 'claude-sonnet-5-5')
        self.assertEqual(chat.model, 'claude-sonnet-5-5')
        with self.assertRaises(ValueError):
            await cmd('model', 'gpt-4')
        with self.assertRaises(ValueError):
            await cmd('effort', 'insane')
        await cmd('model', 'default')
        restored = Bridge(self.temp.name)
        self.assertEqual((restored.chats[chat.id].model, restored.chats[chat.id].effort), (None, 'max'))
        await restored.close()


class ProtectedPathTests(unittest.TestCase):
    def test_top_level_folders_can_be_deleted_but_system_ones_cannot(self):
        from backends.files import _protected
        ws = Path(tempfile.gettempdir()) / 'ws'
        root = Path(Path.home().anchor)
        self.assertTrue(_protected(root, ws))
        self.assertTrue(_protected(Path.home(), ws))
        self.assertTrue(_protected(Path.home().parent, ws))
        self.assertTrue(_protected(ws, ws))
        self.assertFalse(_protected(root / 'sdds', ws))  # e.g. C:\sdds is an ordinary folder
        self.assertFalse(_protected(Path.home() / 'Desktop' / 'old-project', ws))
        if os.name == 'nt' and os.environ.get('SystemRoot'):
            self.assertTrue(_protected(Path(os.environ['SystemRoot']), ws))


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



class GuestTests(unittest.IsolatedAsyncioTestCase):
    """#69: guest tokens see one chat and may only queue messages into it."""
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = Bridge(self.temp.name, AsyncMock())
        self.app = create_app(self.bridge, 't')

    async def asyncTearDown(self):
        await self.bridge.close()
        self.temp.cleanup()

    async def call(self, method, path, token, body=None):
        request = make_mocked_request(method, path, headers={'Authorization': 'Bearer ' + token})
        request.json = AsyncMock(return_value=body or {})
        route = await self.app.router.resolve(request)
        response = await self.app.middlewares[0](request, route.handler)
        return response.status, json.loads(response.text)

    async def test_guest_scopes(self):
        await self.bridge.command({'mode': 'cc', 'action': 'guest', 'text': 'new anna view 2'})
        event = self.bridge.events[-1]
        self.assertEqual(event['kind'], 'guest_token')
        viewer = event['token']
        self.assertNotIn(viewer, (Path(self.temp.name) / 'guests.json').read_text())  # only the hash is stored
        chat = self.bridge.current_chat('cc')
        self.bridge.emit(chat, 'hello', kind='text')
        self.bridge.emit(chat, 'Allow?', kind='permission')
        other = self.bridge.new_chat('cc', 'local', select=False)
        self.bridge.emit(other, 'secret', kind='text')
        status, data = await self.call('GET', '/guest/events', viewer)
        self.assertEqual(status, 200)
        kinds = [e['kind'] for e in data['events']]
        self.assertIn('text', kinds)
        self.assertNotIn('permission', kinds)
        self.assertNotIn('guest_token', kinds)
        self.assertNotIn('secret', [e['text'] for e in data['events']])
        # view cannot write, and no owner endpoint accepts a guest token
        self.assertEqual((await self.call('POST', '/guest/say', viewer, {'text': 'hi'}))[0], 403)
        self.assertEqual((await self.call('POST', '/command', viewer, {'mode': 'cc', 'action': 'q', 'text': 'x'}))[0], 401)
        self.assertEqual((await self.call('GET', '/state', viewer))[0], 401)
        await self.bridge.command({'mode': 'cc', 'action': 'guest', 'text': 'new bob chat'})
        writer = self.bridge.events[-1]['token']
        self.bridge.enqueue = AsyncMock()
        status, _ = await self.call('POST', '/guest/say', writer, {'text': 'try a hashmap'})
        self.assertEqual(status, 200)
        self.bridge.enqueue.assert_awaited_once_with(chat, '[guest bob]: try a hashmap')
        await self.bridge.command({'mode': 'cc', 'action': 'guest', 'text': 'revoke bob'})
        self.assertEqual((await self.call('POST', '/guest/say', writer, {'text': 'x'}))[0], 401)

if __name__ == '__main__':
    unittest.main()
