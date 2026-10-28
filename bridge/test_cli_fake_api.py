"""Bridge + the real bundled Claude Code CLI + a local fake Messages API.

No credentials and no token spend: the CLI talks to fake_anthropic.FakeAnthropic.
Spawns real CLI processes (a few seconds each), so it is opt-in:
    CLAUDECRAFT_CLI_IT=1 python -m unittest test_cli_fake_api -v
"""
import asyncio
import re
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

    async def test_context_meter_warning_and_compact(self):
        big = text('long answer')
        big['usage'] = {'input_tokens': 2000, 'cache_read_input_tokens': 168000, 'output_tokens': 5}
        await self.start([big, text('Summary of the conversation.'), text('after compact')])
        chat = self.bridge.chats[self.bridge.selected['cc']]
        await self.bridge.enqueue(chat, 'remember a lot')
        await self.until(lambda: chat.task is None)
        self.assertEqual(self.errors(), [])
        done = [e for e in self.bridge.events if e['kind'] == 'done'][-1]
        self.assertEqual((done['context'], done['window']), (170000, 200000))
        warn = [e for e in self.bridge.events if e['kind'] == 'context_warn']
        self.assertEqual(warn[-1]['percent'], 85)
        await self.bridge.command({'mode': 'cc', 'action': 'compact', 'text': 'keep the API decisions'})
        await self.until(lambda: chat.task is None)
        self.assertEqual(self.errors(), [])
        self.assertTrue(any('keep the API decisions' in json.dumps(r['messages']) for r in self.fake.requests[1:]))
        self.assertTrue(any(e['kind'] == 'status' and 'compacted' in e['text'] or 'сжата' in e['text']
                            for e in self.bridge.events), [e['text'] for e in self.bridge.events][-8:])

    async def test_permission_rules_allow_and_deny_without_asking(self):
        await self.start([tool('Bash', {'command': 'echo rule-ok'}), tool('Bash', {'command': 'git push --force'},
                          tool_id='toolu_2'), text('finished')])
        chat = self.bridge.chats[self.bridge.selected['cc']]
        await self.bridge.command({'mode': 'cc', 'action': 'perms', 'text': 'allow Bash(echo:*)'})
        await self.bridge.command({'mode': 'cc', 'action': 'perms', 'text': 'deny Bash(git push:*)'})
        await self.bridge.enqueue(chat, 'run two commands')
        await self.until(lambda: chat.task is None or self.bridge.pending)
        self.assertEqual(self.bridge.pending, {})  # nothing was asked
        await self.until(lambda: chat.task is None)
        results = json.dumps(self.fake.requests[-1]['messages'], ensure_ascii=False)
        self.assertIn('rule-ok', results)
        # Denied either by the CLI itself (it reads .claude/settings.json) or by the bridge's rules
        self.assertTrue('Permission to use Bash with command git push --force has been denied' in results
                        or 'Denied by the permission rule Bash(git push:*)' in results, results[-600:])

    async def answer_plans(self, answers):
        """Answers plan cards in order, like the Minecraft buttons."""
        async def loop():
            queue = list(answers)
            while queue:
                for request, p in list(self.bridge.pending.items()):
                    if p['kind'] == 'plan' and not p['future'].done():
                        await self.bridge.command({'mode': 'cc', 'action': 'reply', 'request': request,
                                                   'text': queue.pop(0)})
                await asyncio.sleep(0.05)
        return asyncio.create_task(loop())

    async def test_plan_mode_revise_then_accept_and_execute(self):
        def write_plan(content, tid):
            # The CLI names the plan file in its plan-mode system prompt; Claude writes it with Write.
            def reply(body):
                found = re.search(r'create your plan at (\S+?\.md)', json.dumps(body, ensure_ascii=False))
                path = found.group(1).replace('\\\\', '\\')
                return tool('Write', {'file_path': path, 'content': content}, tool_id=tid)
            return reply
        await self.start([
            write_plan('1. Read the file\n2. Fix it', 'toolu_1'), tool('ExitPlanMode', {}, tool_id='toolu_2'),
            write_plan('1. Read the file\n2. Fix it\n3. Add a test', 'toolu_3'), tool('ExitPlanMode', {}, tool_id='toolu_4'),
            text('executing the plan')])
        chat = self.bridge.chats[self.bridge.selected['cc']]
        await self.bridge.command({'mode': 'cc', 'action': 'plan', 'text': 'fix the bug'})
        answers = await self.answer_plans(['also add a test', '1'])
        try:
            await self.until(lambda: chat.task is None)
        finally:
            answers.cancel()
        self.assertEqual(self.errors(), [])
        plans = [e for e in self.bridge.events if e['kind'] == 'plan']
        self.assertEqual(len(plans), 2)
        self.assertIn('3. Add a test', plans[1]['text'])
        self.assertIn('executing the plan', self.texts())
        sent = json.dumps([r['messages'] for r in self.fake.requests], ensure_ascii=False)
        self.assertIn('also add a test', sent)               # the revision reached Claude
        self.assertNotIn('[[plan]]', sent)                    # the marker is never sent
        self.assertIn('plan mode', sent.lower())              # the CLI really ran in plan mode
        self.assertEqual(chat.title, '[plan] fix the bug')

    async def test_budget_interrupts_pauses_and_resumes(self):
        heavy = tool('Read', {'file_path': 'x'}, delay=2.0)
        heavy['usage'] = {'input_tokens': 5000, 'output_tokens': 1}
        await self.start([heavy, text('second request done')])
        chat = self.bridge.chats[self.bridge.selected['cc']]
        await self.bridge.command({'mode': 'cc', 'action': 'budget', 'text': 'task 1k'})
        await self.bridge.enqueue(chat, 'expensive')
        await self.bridge.enqueue(chat, 'cheap one')
        await self.until(lambda: chat.task is None)
        hits = [e for e in self.bridge.events if e['kind'] == 'budget_hit']
        self.assertEqual((hits[0]['scope'], hits[0]['limit_kind'], hits[0]['limit']), ('task', 'tokens', 1000))
        self.assertTrue(chat.paused)
        self.assertEqual(self.runs, ['expensive'])            # the queue waits
        self.assertEqual(list(chat.queue), ['cheap one'])
        self.assertEqual(self.errors(), [])                     # no scary error, just the card
        await self.bridge.command({'mode': 'cc', 'action': 'budget', 'text': 'task off'})
        await self.bridge.command({'mode': 'cc', 'action': 'budget', 'text': 'resume'})
        await self.until(lambda: chat.task is None and not chat.queue)
        self.assertEqual(self.runs, ['expensive', 'cheap one'])
        self.assertIn('second request done', self.texts())

    async def test_rewind_history_and_files(self):
        await self.start([])
        ws = self.bridge.workspace
        f = lambda n: os.path.join(ws, f'f{n}.txt')
        self.fake.script = [
            tool('Write', {'file_path': f(1), 'content': 'one'}), text('wrote 1'),
            tool('Write', {'file_path': f(2), 'content': 'two'}, tool_id='toolu_2'), text('wrote 2'),
            tool('Write', {'file_path': f(3), 'content': 'three'}, tool_id='toolu_3'), text('wrote 3'),
            text('after rewind')]
        chat = self.bridge.chats[self.bridge.selected['cc']]
        await self.bridge.command({'mode': 'cc', 'action': 'auto', 'text': 'all'})
        for p in ('PROMPT-ONE', 'PROMPT-TWO', 'PROMPT-THREE'):
            await self.bridge.enqueue(chat, p)
        await self.until(lambda: chat.task is None and not chat.queue)
        self.assertTrue(all(os.path.exists(f(n)) for n in (1, 2, 3)))
        await self.bridge.command({'mode': 'cc', 'action': 'rewind', 'text': ''})
        rows = [e for e in self.bridge.events if e['kind'] == 'rewind_list'][-1]['rows']
        self.assertEqual([r['text'] for r in rows], ['PROMPT-ONE', 'PROMPT-TWO', 'PROMPT-THREE'])
        old = chat.sdk_id
        await self.bridge.command({'mode': 'cc', 'action': 'rewind', 'text': '2'})
        self.assertNotEqual(chat.sdk_id, old)
        self.assertTrue(os.path.exists(f(1)))
        self.assertFalse(os.path.exists(f(2)) or os.path.exists(f(3)))   # files of dropped requests are gone
        await self.bridge.enqueue(chat, 'PROMPT-NEW')
        await self.until(lambda: chat.task is None)
        sent = json.dumps(self.fake.requests[-1]['messages'], ensure_ascii=False)
        self.assertIn('PROMPT-ONE', sent)
        self.assertNotIn('PROMPT-TWO', sent)
        self.assertNotIn('PROMPT-THREE', sent)
        self.assertIn('after rewind', self.texts())

    async def test_claude_md_memory_and_undo_when_settings_allow(self):
        await self.start([])
        ws = self.bridge.workspace
        target = os.path.join(ws, 'kept.txt')
        with open(target, 'w', encoding='utf-8') as f:
            f.write('original')
        self.fake.script = [tool('Write', {'file_path': target, 'content': 'changed'}), text('ok')]
        await self.bridge.command({'mode': 'cc', 'action': 'remember', 'text': 'MEMORY-TOKEN-42 use tabs'})
        os.makedirs(os.path.join(ws, '.claude'), exist_ok=True)
        with open(os.path.join(ws, '.claude', 'settings.json'), 'w', encoding='utf-8') as f:
            json.dump({'permissions': {'allow': ['Write']}}, f)   # the CLI allows it without asking us
        config = os.environ['CLAUDE_CONFIG_DIR']
        os.makedirs(config, exist_ok=True)
        with open(os.path.join(config, '.claude.json'), 'w', encoding='utf-8') as f:   # trusted folder
            json.dump({'projects': {str(ws).replace('\\', '/'): {'hasTrustDialogAccepted': True}}}, f)
        chat = self.bridge.chats[self.bridge.selected['cc']]
        import backends.permrules as rules
        asked = []
        original = rules.decide
        with mock.patch.object(rules, 'decide', lambda *a: asked.append(a) or original(*a)):
            await self.bridge.enqueue(chat, 'change the file')
            await self.until(lambda: chat.task is None)
        self.assertEqual(asked, [])   # the bridge's permission callback never ran: only the hook saw the edit
        self.assertEqual(self.errors(), [])
        self.assertIn('MEMORY-TOKEN-42', json.dumps(self.fake.requests[0], ensure_ascii=False))
        with open(target, encoding='utf-8') as f:
            self.assertEqual(f.read(), 'changed')
        await self.bridge.command({'mode': 'cc', 'action': 'undo'})
        with open(target, encoding='utf-8') as f:
            self.assertEqual(f.read(), 'original')

    async def test_skills_listing_and_switching_off(self):
        await self.start([text('ok')])
        ws = self.bridge.workspace
        for name, desc in (('deploy-helper', 'SKILL-DEPLOY-DESCRIPTION'), ('secret-skill', 'SKILL-HIDDEN-DESCRIPTION')):
            os.makedirs(os.path.join(ws, '.claude', 'skills', name))
            with open(os.path.join(ws, '.claude', 'skills', name, 'SKILL.md'), 'w', encoding='utf-8') as f:
                f.write(f'---\nname: {name}\ndescription: {desc}\n---\nSteps.\n')
        await self.bridge.command({'mode': 'cc', 'action': 'skills', 'text': 'off secret-skill'})
        rows = [e for e in self.bridge.events if e['kind'] == 'skills'][-1]['rows']
        self.assertEqual({r['name']: r['enabled'] for r in rows}, {'deploy-helper': True, 'secret-skill': False})
        chat = self.bridge.chats[self.bridge.selected['cc']]
        await self.bridge.enqueue(chat, 'hello')
        await self.until(lambda: chat.task is None)
        self.assertEqual(self.errors(), [])
        request = json.dumps(self.fake.requests[0], ensure_ascii=False)
        self.assertIn('SKILL-DEPLOY-DESCRIPTION', request)
        self.assertNotIn('SKILL-HIDDEN-DESCRIPTION', request)
        self.assertIn('Skill', [t['name'] for t in self.fake.requests[0].get('tools', [])])

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
