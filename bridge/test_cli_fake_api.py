"""Bridge + the real bundled Claude Code CLI + a local fake Messages API.

No credentials and no token spend: the CLI talks to fake_anthropic.FakeAnthropic.
Spawns real CLI processes (a few seconds each), so it is opt-in:
    CLAUDECRAFT_CLI_IT=1 python -m unittest test_cli_fake_api -v
"""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from bridge import Bridge
from fake_anthropic import FakeAnthropic, text, tool


@unittest.skipUnless(os.environ.get('CLAUDECRAFT_CLI_IT') == '1', 'set CLAUDECRAFT_CLI_IT=1')
class CliWithFakeApi(unittest.IsolatedAsyncioTestCase):
    async def start(self, script):
        self.temp = tempfile.TemporaryDirectory()
        self.fake = await FakeAnthropic(script).start()
        env = {'ANTHROPIC_BASE_URL': self.fake.url, 'ANTHROPIC_API_KEY': 'fake-key-for-local-test',
               'CLAUDE_CONFIG_DIR': os.path.join(self.temp.name, 'claude-config'),
               'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'DISABLE_TELEMETRY': '1'}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bridge = Bridge(os.path.join(self.temp.name, 'bridge'))
        self.runs = []
        original = self.bridge.runner
        async def counting(chat, question):
            self.runs.append(question)
            await original(chat, question)
        self.bridge.runner = counting

    async def asyncTearDown(self):
        await self.bridge.close()
        await self.fake.stop()
        self.temp.cleanup()

    async def until(self, predicate, timeout=60):
        try:
            async with asyncio.timeout(timeout):
                while not predicate():
                    await asyncio.sleep(0.05)
        except TimeoutError:
            # Make CLI-side hangs debuggable: what the bridge saw and what reached the API.
            print('\nEVENTS:', [(e['kind'], e['text'][:120]) for e in self.bridge.events])
            print('API requests:', len(self.fake.requests), 'other:', self.fake.unknown[:5])
            raise

    def texts(self):
        # Answers stream line by line (text_part); whole blocks (text) only without streaming.
        return [e['text'] for e in self.bridge.events if e['kind'] in ('text', 'text_part')]

    def stream_kinds(self):
        return {e['kind'] for e in self.bridge.events if e['kind'] in ('text', 'text_part')}

    def errors(self):
        return [e['text'] for e in self.bridge.events if e['kind'] == 'error']

    async def test_f_mid_tool_turn_reaches_same_run(self):
        await self.start([])
        note = os.path.join(self.bridge.workspace, 'note.txt')
        with open(note, 'w') as f:
            f.write('hello')
        self.fake.script = [tool('Read', {'file_path': note}, delay=1.5), text('done with both')]
        chat = self.bridge.chats[self.bridge.selected['cc']]
        await self.bridge.enqueue(chat, 'read the note')
        await self.until(lambda: chat.live is not None and chat.live.client is not None)
        await self.bridge.force(chat, 'EXTRA: also say hi')
        # If the CLI asks for permission, answer like the Minecraft button would.
        async def approve():
            while True:
                for request in list(self.bridge.pending):
                    await self.bridge.command({'mode': 'cc', 'action': 'reply',
                                               'request': request, 'text': '1'})
                await asyncio.sleep(0.05)
        approver = asyncio.create_task(approve())
        try:
            await self.until(lambda: chat.task is None)
        finally:
            approver.cancel()
        self.assertEqual(self.errors(), [])
        self.assertEqual(self.runs, ['read the note'])
        self.assertIn('done with both', self.texts())
        self.assertEqual(len(self.fake.requests), 2)
        # Depending on timing the CLI merges f into the first prompt or into the next tool
        # result; either way it reaches the model within this one run.
        self.assertIn('EXTRA: also say hi', json.dumps(self.fake.requests[-1]['messages'], ensure_ascii=False))

    async def test_f_during_text_reply_gets_own_answer_in_same_process(self):
        await self.start([text('first answer', delay=2.0), text('answer to extra')])
        chat = self.bridge.chats[self.bridge.selected['c']]
        await self.bridge.enqueue(chat, 'tell a story')
        await self.until(lambda: chat.live is not None and chat.live.client is not None)
        await asyncio.sleep(0.5)
        await self.bridge.force(chat, 'EXTRA question')
        await self.until(lambda: chat.task is None)
        self.assertEqual(self.errors(), [])
        self.assertEqual(self.texts(), ['first answer', 'answer to extra'])
        self.assertEqual(self.stream_kinds(), {'text_part'})  # shown while generated, no duplicates
        self.assertEqual(self.runs, ['tell a story'])
        self.assertIn('EXTRA question', FakeAnthropic.user_texts(self.fake.requests[1]))

    async def test_queue_resumes_session_in_new_process(self):
        await self.start([text('one'), text('two')])
        chat = self.bridge.chats[self.bridge.selected['c']]
        await self.bridge.enqueue(chat, 'first question')
        await self.bridge.enqueue(chat, 'second question')
        await self.until(lambda: chat.task is None)
        self.assertEqual(self.errors(), [])
        self.assertEqual(self.texts(), ['one', 'two'])
        self.assertEqual(self.runs, ['first question', 'second question'])
        second = FakeAnthropic.user_texts(self.fake.requests[1])
        self.assertIn('first question', second)
        self.assertIn('second question', second)
        self.assertTrue(chat.sdk_id)

    async def test_model_choice_reaches_the_api(self):
        await self.start([text('hi from haiku')])
        chat = self.bridge.chats[self.bridge.selected['c']]
        await self.bridge.command({'mode': 'c', 'action': 'model', 'text': 'haiku'})
        await self.bridge.command({'mode': 'c', 'action': 'effort', 'text': 'low'})
        await self.bridge.enqueue(chat, 'hello')
        await self.until(lambda: chat.task is None)
        self.assertEqual(self.errors(), [])
        self.assertIn('haiku', self.fake.requests[0]['model'])

    async def test_edits_show_diff_and_undo_restores(self):
        await self.start([])
        src = os.path.join(self.bridge.workspace, 'Main.java')
        made = os.path.join(self.bridge.workspace, 'New.txt')
        with open(src, 'w', encoding='utf-8') as f:
            f.write('class Main {\n    int x = 1;\n}\n')
        self.fake.script = [
            tool('Read', {'file_path': src}),
            tool('Edit', {'file_path': src, 'old_string': 'int x = 1;', 'new_string': 'int x = 2;'}, tool_id='toolu_2'),
            tool('Write', {'file_path': made, 'content': 'fresh\n'}, tool_id='toolu_3'),
            text('edited')]
        chat = self.bridge.chats[self.bridge.selected['cc']]
        await self.bridge.command({'mode': 'cc', 'action': 'auto', 'text': 'all'})
        await self.bridge.enqueue(chat, 'change x')
        await self.until(lambda: chat.task is None)
        self.assertEqual(self.errors(), [])
        with open(src, encoding='utf-8') as f:
            self.assertIn('int x = 2;', f.read())
        self.assertTrue(os.path.exists(made))
        diffs = [e for e in self.bridge.events if e['kind'] == 'edit_diff']
        self.assertEqual(len(diffs), 2)
        self.assertIn('-    int x = 1;', diffs[0]['lines'])
        self.assertIn('+    int x = 2;', diffs[0]['lines'])
        self.assertTrue(diffs[1]['new'])
        await self.bridge.command({'mode': 'cc', 'action': 'undo'})
        with open(src, encoding='utf-8') as f:
            self.assertIn('int x = 1;', f.read())
        self.assertFalse(os.path.exists(made))
        with self.assertRaises(ValueError):
            await self.bridge.command({'mode': 'cc', 'action': 'undo'})

    async def test_image_reaches_api_and_session_file(self):
        await self.start([text('a red pixel')])
        png = os.path.join(self.bridge.workspace, 'shot one.png')
        with open(png, 'wb') as f:  # 1x1 PNG
            f.write(bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010806000000'
                                  '1f15c4890000000d49444154789c63f8cfc0f01f0005000201a5f0b5c50000000049454e44ae426082'))
        chat = self.bridge.chats[self.bridge.selected['cc']]
        await self.bridge.command({'mode': 'cc', 'action': 'img', 'text': png + ' what is on it?'})
        await self.until(lambda: chat.task is None)
        self.assertEqual(self.errors(), [])
        self.assertIn('a red pixel', self.texts())
        content = [b for m in self.fake.requests[0]['messages'] if m['role'] == 'user'
                   and isinstance(m['content'], list) for b in m['content']]
        kinds = [b.get('type') for b in content if isinstance(b, dict)]
        self.assertIn('image', kinds)
        self.assertIn('what is on it?', json.dumps(content))
        self.assertEqual(chat.title, '[image shot one.png] what is on it?')
        # The session transcript (what Claude Code on the PC opens) keeps the picture.
        stored = []
        for root, _, names in os.walk(os.path.join(self.temp.name, 'claude-config', 'projects')):
            stored += [Path(root, n).read_text(encoding='utf-8') for n in names if n.endswith('.jsonl')]
        self.assertTrue(any('"type":"image"' in s.replace(' ', '') for s in stored))

    async def test_cancel_stops_cli(self):
        await self.start([text('never shown', delay=20)])
        chat = self.bridge.chats[self.bridge.selected['c']]
        await self.bridge.enqueue(chat, 'slow')
        await self.until(lambda: self.fake.requests)
        await self.bridge.command({'mode': 'c', 'action': 'cancel'})
        await self.until(lambda: chat.task is None, timeout=15)
        self.assertNotIn('never shown', self.texts())


if __name__ == '__main__':
    unittest.main()
