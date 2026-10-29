"""Run after :geminicraft:build. Uses the Python payload shipped in the JAR and a fake ACP agent."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PAYLOAD = Path(__file__).resolve().parents[1] / 'build/resources/main/geminicraft/bridge'
sys.dont_write_bytecode = True
sys.path.insert(0, str(PAYLOAD))
from gemini_bridge import GeminiBridge  # noqa: E402

AGENT = Path(__file__).with_name('fake_acp_agent.py')


class GeminiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = GeminiBridge(Path(self.temp.name) / 'config/geminicraft')
        self.bridge.config['gemini_path'] = sys.executable
        self.bridge.config['gemini_args'] = [str(AGENT)]
        Path(AGENT.with_name('fake_sessions.json')).unlink(missing_ok=True)
        self.chat = self.bridge.current_chat('cc')

    async def asyncTearDown(self):
        await self.bridge.close()
        AGENT.with_name('fake_sessions.json').unlink(missing_ok=True)
        self.temp.cleanup()

    async def run_prompt(self, text, chat=None):
        chat = chat or self.chat
        await self.bridge.enqueue(chat, text)
        for _ in range(400):
            if chat.task is None:
                return
            await asyncio.sleep(0.02)
        self.fail('timeout: ' + str([(e['kind'], e['text']) for e in self.bridge.events][-6:]))

    def texts(self):
        return [e['text'] for e in self.bridge.events if e['kind'] == 'text_part']

    async def test_stream_session_resume(self):
        await self.run_prompt('hello')
        self.assertEqual(self.texts(), ['Gemini says: hello (turn 1)'])
        first = self.chat.sdk_id
        self.assertTrue(first.startswith('sess-'))
        await self.run_prompt('again')                 # a new process loads the same session
        self.assertEqual(self.chat.sdk_id, first)
        self.assertEqual(self.texts()[-1], 'Gemini says: again (turn 2)')
        self.assertEqual([e for e in self.bridge.events if e['kind'] == 'error'], [])

    async def test_permission_write_through_client_and_undo(self):
        target = Path(self.bridge.chat_cwd(self.chat)) / 'note.txt'
        target.write_text('original', encoding='utf-8')
        self.chat.auto = 'off'
        async def approve():
            while True:
                for request, p in list(self.bridge.pending.items()):
                    await self.bridge.command({'mode': 'cc', 'action': 'reply', 'request': request, 'text': '1'})
                await asyncio.sleep(0.02)
        approver = asyncio.create_task(approve())
        try:
            await self.run_prompt(f'write {target} changed by gemini')
        finally:
            approver.cancel()
        self.assertEqual(target.read_text(encoding='utf-8'), 'changed by gemini')
        self.assertTrue(any(e['kind'] == 'permission' for e in self.bridge.events))
        await self.bridge.command({'mode': 'cc', 'action': 'undo'})
        self.assertEqual(target.read_text(encoding='utf-8'), 'original')

    async def test_gem_chat_never_writes_and_plans_render(self):
        chat = self.bridge.current_chat('c')
        target = Path(self.bridge.workspace) / 'x.txt'
        await self.run_prompt(f'write {target} nope', chat)
        self.assertFalse(target.exists())
        self.assertTrue(any(e['kind'] == 'tool_error' and 'rejected' in e['text'] for e in self.bridge.events))
        await self.run_prompt('plan')
        todo = [e for e in self.bridge.events if e['kind'] == 'todo'][-1]
        self.assertEqual([r['status'] for r in todo['rows']], ['completed', 'pending'])

    async def test_claude_only_commands_and_model(self):
        with self.assertRaises(ValueError):
            await self.bridge.command({'mode': 'cc', 'action': 'plan', 'text': 'x'})
        await self.bridge.command({'mode': 'cc', 'action': 'model', 'text': 'gemini-2.5-pro'})
        self.assertEqual(self.bridge.config['gemini_model'], 'gemini-2.5-pro')

    async def test_sign_in_calls_authenticate(self):
        await self.bridge.account.login()
        await asyncio.wait_for(self.bridge.account.login_task, 20)
        self.assertEqual(self.bridge.account.flow['state'], 'done')
        self.bridge.account.set_api_key('AIza-test-key-1234')
        status = await self.bridge.account.status()
        self.assertTrue(status['account']['logged_in'])
        self.assertEqual(status['api_key'], '…1234')


if __name__ == '__main__':
    unittest.main()
