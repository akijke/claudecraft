"""Agent Client Protocol (ACP) transport for Gemini CLI: `gemini --experimental-acp`.

JSON-RPC 2.0, one JSON message per line over stdio. The client (this bridge) sends
initialize / authenticate / session/new|load / session/prompt / session/cancel; the
agent sends session/update notifications and asks the client for
session/request_permission and fs/read_text_file / fs/write_text_file.
"""
import asyncio
import json
import os
import shutil
from pathlib import Path

PROTOCOL_VERSION = 1


def cli_command(config):
    configured = config.get('gemini_path')
    executable = configured or shutil.which('gemini')
    if not executable:
        raise ValueError('Gemini CLI не найден. Установи его кнопкой в настройках (npm i -g @google/gemini-cli) '
                         'или укажи gemini_path в bridge-config.json.')
    args = config.get('gemini_args') or ['--experimental-acp']
    if os.name == 'nt' and str(executable).lower().endswith(('.cmd', '.ps1')):
        # npm shim on Windows: run its JS entry with node, without cmd.exe
        script = Path(executable).parent / 'node_modules/@google/gemini-cli/dist/index.js'
        node = shutil.which('node')
        if node and script.is_file():
            return [node, str(script), *args]
        return ['cmd', '/c', str(executable), *args]
    return [str(executable), *args]


class AcpError(ValueError):
    pass


class Agent:
    def __init__(self, config, notification=None, request=None, env=None, cwd=None):
        self.config = config
        self.notification = notification   # (method, params) for session/update
        self.request_handler = request     # async (method, params) -> result
        self.env, self.cwd = env, cwd
        self.process = None
        self.reader = None
        self.pending = {}
        self.counter = 0
        self.write_lock = asyncio.Lock()
        self.capabilities = {}
        self.auth_methods = []

    async def start(self):
        env = dict(os.environ, **(self.env or {}))
        self.process = await asyncio.create_subprocess_exec(
            *cli_command(self.config), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=env, cwd=self.cwd, limit=8 * 1024 * 1024)
        self.reader = asyncio.create_task(self._read())
        try:
            result = await self.call('initialize', {
                'protocolVersion': PROTOCOL_VERSION,
                'clientCapabilities': {'fs': {'readTextFile': True, 'writeTextFile': True}, 'terminal': False}},
                timeout=60)
        except BaseException:
            await self.close()
            raise
        self.capabilities = result.get('agentCapabilities') or {}
        self.auth_methods = result.get('authMethods') or []
        return self

    async def send(self, message):
        async with self.write_lock:
            self.process.stdin.write((json.dumps(message, ensure_ascii=False) + '\n').encode('utf-8'))
            await self.process.stdin.drain()

    async def call(self, method, params, timeout=None):
        self.counter += 1
        rid = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[rid] = future
        await self.send({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params})
        try:
            return await (asyncio.wait_for(future, timeout) if timeout else future)
        finally:
            self.pending.pop(rid, None)

    async def notify(self, method, params):
        await self.send({'jsonrpc': '2.0', 'method': method, 'params': params})

    async def _read(self):
        try:
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line or not line.startswith(b'{'):
                    continue  # stray log output
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if 'id' in message and 'method' not in message:
                    future = self.pending.get(message['id'])
                    if future and not future.done():
                        if 'error' in message:
                            future.set_exception(AcpError((message['error'] or {}).get('message', 'ACP error')))
                        else:
                            future.set_result(message.get('result'))
                elif 'id' in message:
                    asyncio.create_task(self._answer(message))
                elif self.notification:
                    self.notification(message.get('method'), message.get('params') or {})
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(AcpError('Gemini CLI завершился'))
            if self.notification:
                self.notification('geminicraft/disconnected', {})

    async def _answer(self, message):
        try:
            result = await self.request_handler(message['method'], message.get('params') or {})
            await self.send({'jsonrpc': '2.0', 'id': message['id'], 'result': result})
        except Exception as e:
            await self.send({'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32603, 'message': str(e)[:500]}})

    async def close(self):
        if self.process and self.process.returncode is None:
            try:
                self.process.stdin.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except TimeoutError:
                self.process.kill()
        if self.reader:
            self.reader.cancel()
