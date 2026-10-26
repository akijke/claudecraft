import asyncio
import json
from pathlib import Path
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
        self.turn = SdkTurn(self.bridge, self.chat, self.client, sdk, ack_timeout=1)

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
        response = await call('GET', '/events?after=0', headers)
        self.assertGreater(len(json.loads(response.text)['events']), 0)
        response = await call('POST', '/command', headers, {'mode':'bad','action':'q'})
        self.assertEqual(response.status, 400)

    async def test_restart_preserves_session(self):
        self.chat.sdk_id = 'resume-me'
        await self.bridge.command({'mode':'cc', 'action':'restart'})
        self.assertEqual(self.chat.sdk_id, 'resume-me')


if __name__ == '__main__':
    unittest.main()
