"""Run after :codexcraft:build. Uses exactly the Python payload shipped in the JAR."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

PAYLOAD = Path(__file__).resolve().parents[1] / 'build/resources/main/codexcraft/bridge'
sys.dont_write_bytecode = True
sys.path.insert(0, str(PAYLOAD))
from codex_backend import CodexTurn
from codex_bridge import CodexBridge
from codex_rpc import AppServer, RpcError
from backends.sdk import TurnClosed


class FakeRpc:
    scripts = []
    instances = []

    def __init__(self, config, notification=None, request=None):
        self.notify = notification
        self.request = request
        self.calls = []
        self.process = None
        self.closed = False
        self.script = self.scripts.pop(0) if self.scripts else None
        self.instances.append(self)

    async def start(self):
        return self

    async def call(self, method, params=None, **kwargs):
        self.calls.append((method, params))
        if method.startswith('thread/'):
            return {'thread': {'id': params.get('threadId', 'codex-thread')}}
        if method == 'turn/start':
            self.notify('turn/started', {'threadId': 'codex-thread', 'turn': {'id': 'turn-1'}})
            if self.script:
                self.script(self)
            return {'turn': {'id': 'turn-1'}}
        if method == 'turn/steer':
            return {'turnId': 'turn-1'}
        return {}

    async def close(self):
        self.closed = True


def complete(rpc, text='hello'):
    rpc.notify('item/agentMessage/delta', {'itemId': 'a', 'delta': text + '\n'})
    rpc.notify('item/completed', {'item': {'type': 'agentMessage', 'id': 'a', 'text': text}})
    rpc.notify('thread/tokenUsage/updated', {'tokenUsage': {'last': {'inputTokens': 12, 'outputTokens': 3}}})
    rpc.notify('turn/completed', {'turn': {'id': 'turn-1', 'status': 'completed'}})


class CodexTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = CodexBridge(Path(self.temp.name) / 'config/codexcraft')
        self.chat = self.bridge.current_chat('cc')
        FakeRpc.scripts, FakeRpc.instances = [], []
        self.patcher = patch('codex_backend.AppServer', FakeRpc)
        self.patcher.start()

    async def asyncTearDown(self):
        await self.bridge.close()
        self.patcher.stop()
        self.temp.cleanup()

    async def test_thread_persist_resume_and_stream_without_duplicates(self):
        FakeRpc.scripts = [complete, complete]
        await self.bridge.run_backend(self.chat, 'first')
        self.assertEqual(self.chat.sdk_id, 'codex-thread')
        await self.bridge.run_backend(self.chat, 'second')
        self.assertEqual(FakeRpc.instances[1].calls[0][0], 'thread/resume')
        text_events = [e for e in self.bridge.events if e['kind'] in ('text', 'text_part')]
        self.assertEqual([e['text'] for e in text_events], ['hello', 'hello'])
        self.assertEqual(self.chat.usage['input_tokens'], 24)
        other = CodexBridge(self.bridge.directory)
        self.assertEqual(other.current_chat('cc').sdk_id, 'codex-thread')
        await other.close()

    async def test_force_uses_steer_and_never_interrupts(self):
        turn = CodexTurn(self.bridge, self.chat)
        task = asyncio.create_task(turn.drive('first'))
        await turn.ready.wait()
        await turn.inject('extra')
        self.assertEqual(turn.rpc.calls[-1], ('turn/steer', {
            'threadId': 'codex-thread', 'expectedTurnId': 'turn-1',
            'input': [{'type': 'text', 'text': 'extra'}]}))
        complete(turn.rpc)
        await task
        with self.assertRaises(TurnClosed):
            await turn.inject('late')

    async def test_cancel_interrupts_and_closes(self):
        turn = CodexTurn(self.bridge, self.chat)
        class Process:
            returncode = None
        turn.rpc.process = Process()
        task = asyncio.create_task(turn.drive('wait'))
        await turn.ready.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIn('turn/interrupt', [m for m, _ in turn.rpc.calls])
        self.assertTrue(turn.rpc.closed)

    async def test_approval_roundtrip_and_gpt_denies_escalation(self):
        turn = CodexTurn(self.bridge, self.chat)
        self.chat.sdk_id = 'codex-thread'
        params = {'threadId': 'codex-thread', 'command': 'git status'}
        task = asyncio.create_task(turn.request('item/commandExecution/requestApproval', params))
        await asyncio.sleep(0)
        request = next(iter(self.bridge.pending))
        self.bridge.answer(self.chat, request, '1')
        self.assertEqual(await task, {'decision': 'accept'})
        turn.chat = self.bridge.current_chat('c')
        turn.chat.sdk_id = 'codex-thread'
        turn.chat.auto = 'all'
        self.assertEqual(await turn.request('item/commandExecution/requestApproval', params), {'decision': 'decline'})

    async def test_chat_mode_and_project_mode_use_separate_threads_and_policies(self):
        FakeRpc.scripts = [complete, complete]
        gpt = self.bridge.current_chat('c')
        await self.bridge.run_backend(gpt, 'chat')
        self.chat.auto = 'edits'
        await self.bridge.run_backend(self.chat, 'code')
        first = FakeRpc.instances[0].calls[0][1]
        second = FakeRpc.instances[1].calls[0][1]
        self.assertEqual(first['sandbox'], 'read-only')
        self.assertEqual(first['approvalPolicy'], 'never')
        self.assertEqual(second['sandbox'], 'workspace-write')
        self.assertNotEqual(gpt.id, self.chat.id)

    async def test_unsupported_claude_features_do_not_import_claude_sessions(self):
        self.assertEqual(await self.bridge.sync_sessions(), 0)
        with self.assertRaises(ValueError):
            await self.bridge.command({'mode': 'cc', 'action': 'agent', 'text': 'explorer inspect'})
        with self.assertRaises(ValueError):
            self.bridge.new_chat('c', 'api')

    async def test_model_setting_and_config_isolation(self):
        await self.bridge.command({'mode': 'cc', 'action': 'model', 'text': 'chosen-model'})
        config = json.loads((self.bridge.directory / 'bridge-config.json').read_text())
        self.assertEqual(config['codex_model'], 'chosen-model')
        self.assertFalse((self.bridge.directory.parent / 'claude-chat').exists())

    async def test_disconnect_fails_turn_instead_of_hanging(self):
        FakeRpc.scripts = [lambda rpc: rpc.notify('codexcraft/disconnected', {'message': 'lost'})]
        with self.assertRaisesRegex(ValueError, 'lost'):
            await self.bridge.run_backend(self.chat, 'first')
        self.assertIsNone(self.chat.live)

    async def test_codex_device_login_uses_codex_account_protocol(self):
        class AccountRpc(FakeRpc):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.reader = SimpleNamespace(done=lambda: False)

            async def call(self, method, params=None, **kwargs):
                self.calls.append((method, params))
                if method == 'account/login/start':
                    return {'loginId': 'login-1', 'verificationUrl': 'https://auth.openai.com/codex/device',
                            'userCode': 'ABCD-1234'}
                if method == 'account/read':
                    return {'account': {'type': 'chatgpt', 'email': 'test@example.com'}}
                return {}

        account = self.bridge.account
        with patch('backends.account.AppServer', AccountRpc):
            await account.login()
            self.assertEqual(account.rpc.calls[-1], ('account/login/start', {'type': 'chatgptDeviceCode'}))
            self.assertEqual(account.flow['code'], 'ABCD-1234')
            account.notify('account/login/completed', {'loginId': 'login-1', 'success': True})
            self.assertEqual(account.flow['state'], 'done')
            status = await account.status()
            self.assertTrue(status['account']['logged_in'])
            await account.cancel_login()

    async def test_mcp_http_configuration_is_translated_for_codex(self):
        self.bridge.extensions = {'test': {'type': 'http', 'url': 'https://example.com/mcp',
                                           'headers': {'X-Test': 'value'}}}
        FakeRpc.scripts = [complete]
        await self.bridge.run_backend(self.chat, 'test')
        servers = FakeRpc.instances[0].calls[0][1]['config']['mcp_servers']
        self.assertEqual(servers['test']['http_headers'], {'X-Test': 'value'})
        self.assertNotIn('type', servers['test'])


@unittest.skipUnless(os.environ.get('CODEXCRAFT_CLI_IT') == '1', 'opt-in local CLI protocol smoke test')
class RealCliTests(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_and_account_schema_without_model_request(self):
        rpc = await AppServer({}).start()
        try:
            data = await rpc.call('account/read', {'refreshToken': False})
            self.assertIn('account', data)
        finally:
            await rpc.close()


if __name__ == '__main__':
    unittest.main()
