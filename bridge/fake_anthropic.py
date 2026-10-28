"""Scriptable stand-in for the Anthropic Messages API (SSE streaming).

Lets tests drive the real Claude Code CLI without credentials or token spend:
point it at the server with ANTHROPIC_BASE_URL + a dummy ANTHROPIC_API_KEY.
Each POST /v1/messages pops the next scripted reply; every request body is kept
in `requests` so tests can see which user messages reached the model and when.
"""
import asyncio
import json
from aiohttp import web


def text(value, delay=0.0):
    return {'blocks': [{'type': 'text', 'text': value}], 'delay': delay}


def tool(name, payload, delay=0.0, tool_id='toolu_1'):
    return {'blocks': [{'type': 'tool_use', 'id': tool_id, 'name': name, 'input': payload}],
            'delay': delay}


class FakeAnthropic:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.unknown = []
        self.runner = None
        self.url = None

    @staticmethod
    def user_texts(body):
        """Plain user prompts (not tool results) in a request, in order."""
        out = []
        for m in body.get('messages', []):
            if m['role'] != 'user':
                continue
            content = m['content']
            if isinstance(content, str):
                out.append(content)
                continue
            for b in content:
                if b.get('type') == 'text':
                    out.append(b['text'])
        return out

    async def messages(self, request):
        body = await request.json()
        if not body.get('stream'):
            # Side requests (titles, classifiers): answer briefly, keep them out of the script.
            self.unknown.append(body)
            return web.json_response({
                'id': 'msg_side', 'type': 'message', 'role': 'assistant', 'model': body.get('model', 'm'),
                'content': [{'type': 'text', 'text': 'ok'}], 'stop_reason': 'end_turn',
                'stop_sequence': None, 'usage': {'input_tokens': 1, 'output_tokens': 1}})
        self.requests.append(body)
        reply = self.script.pop(0) if self.script else text('(script exhausted)')
        if callable(reply):  # replies that depend on the request (e.g. a path from the system prompt)
            reply = reply(body)
        response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
        await response.prepare(request)

        async def send(event, data):
            try:
                await response.write(f'event: {event}\ndata: {json.dumps(data)}\n\n'.encode())
            except ConnectionError:
                pass  # the CLI hung up (interrupt/cancel): nothing to tell it any more

        await send('message_start', {'type': 'message_start', 'message': {
            'id': f'msg_{len(self.requests)}', 'type': 'message', 'role': 'assistant',
            'model': body.get('model', 'm'), 'content': [], 'stop_reason': None,
            'stop_sequence': None, 'usage': reply.get('usage') or {'input_tokens': 1, 'output_tokens': 1}}})
        stop = 'end_turn'
        for i, block in enumerate(reply['blocks']):
            if block['type'] == 'text':
                await send('content_block_start', {'type': 'content_block_start', 'index': i,
                           'content_block': {'type': 'text', 'text': ''}})
                await asyncio.sleep(reply['delay'])
                await send('content_block_delta', {'type': 'content_block_delta', 'index': i,
                           'delta': {'type': 'text_delta', 'text': block['text']}})
            else:
                stop = 'tool_use'
                await send('content_block_start', {'type': 'content_block_start', 'index': i,
                           'content_block': {'type': 'tool_use', 'id': block['id'],
                                             'name': block['name'], 'input': {}}})
                await asyncio.sleep(reply['delay'])
                await send('content_block_delta', {'type': 'content_block_delta', 'index': i,
                           'delta': {'type': 'input_json_delta',
                                     'partial_json': json.dumps(block['input'])}})
            await send('content_block_stop', {'type': 'content_block_stop', 'index': i})
        await send('message_delta', {'type': 'message_delta',
                   'delta': {'stop_reason': stop, 'stop_sequence': None},
                   'usage': {'output_tokens': 1}})
        await send('message_stop', {'type': 'message_stop'})
        return response

    async def other(self, request):
        self.unknown.append(request.method + ' ' + request.path)
        if request.path.endswith('/count_tokens'):
            return web.json_response({'input_tokens': 1})
        return web.json_response({}, status=404)

    async def start(self):
        app = web.Application()
        app.router.add_post('/v1/messages', self.messages)
        app.router.add_route('*', '/{tail:.*}', self.other)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f'http://127.0.0.1:{port}'
        return self

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
