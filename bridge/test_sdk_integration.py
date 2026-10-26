"""Real Claude Agent SDK check for `f` (streaming input). Spends tokens.

Run only on purpose, after `claude auth login` or with ANTHROPIC_API_KEY:
    CLAUDECRAFT_SDK_IT=1 python -m unittest test_sdk_integration -v
"""
import asyncio
import os
import tempfile
import unittest
from bridge import Bridge


@unittest.skipUnless(os.environ.get('CLAUDECRAFT_SDK_IT') == '1', 'set CLAUDECRAFT_SDK_IT=1')
class SdkIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_force_reaches_live_session(self):
        with tempfile.TemporaryDirectory() as temp:
            bridge = Bridge(temp)
            chat = bridge.chats[bridge.selected['c']]
            await bridge.enqueue(chat, 'Напиши подробный рассказ про кота примерно на 400 слов.')
            async with asyncio.timeout(120):
                while chat.live is None or not chat.live.ready.is_set():
                    await asyncio.sleep(0.05)
            live = chat.live
            self.assertTrue(live.replay, 'CLI did not echo prompts; f cannot be delivered safely')
            task = chat.task
            await bridge.force(chat, 'Дополнение: последней строкой ответа напиши слово БАНАН.')
            async with asyncio.timeout(300):
                while chat.task is not None:
                    await asyncio.sleep(0.2)
            self.assertFalse(task.cancelled())
            kinds = [e['kind'] for e in bridge.events]
            self.assertNotIn('error', kinds, list(bridge.events))
            text = '\n'.join(e['text'] for e in bridge.events if e['kind'] == 'text')
            self.assertIn('БАНАН', text.upper())
            self.assertGreaterEqual(live.acked, 2)
            await bridge.close()


if __name__ == '__main__':
    unittest.main()
