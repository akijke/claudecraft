"""Codex app-server JSON-lines transport. No shell, credentials or tokens in logs."""
import asyncio
import json
import os
import shutil


def cli_command(config):
    configured = config.get('codex_path')
    executable = configured or shutil.which('codex')
    if not executable:
        raise ValueError('Codex CLI не найден. Установи Codex CLI или укажи codex_path в bridge-config.json.')
    # npm on Windows installs a .cmd shim. Execute its JS entry without cmd.exe.
    if os.name == 'nt' and str(executable).lower().endswith(('.cmd', '.ps1')):
        from pathlib import Path
        script = Path(executable).parent / 'node_modules/@openai/codex/bin/codex.js'
        node = shutil.which('node')
        if not node or not script.is_file():
            raise ValueError('Укажи путь к codex.exe в codex_path; npm shim не найден.')
        return [node, str(script)]
    return [str(executable)]


class RpcError(ValueError):
    pass


class AppServer:
    def __init__(self, config, notification=None, request=None):
        self.config = config
        self.notification = notification
        self.request_handler = request
        self.process = None
        self.reader = None
        self.stderr = None
        self.pending = {}
        self.workers = set()
        self.counter = 0
        self.write_lock = asyncio.Lock()

    async def start(self):
        self.process = await asyncio.create_subprocess_exec(
            *cli_command(self.config), 'app-server', '--listen', 'stdio://',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=8 * 1024 * 1024)
        self.reader = asyncio.create_task(self._read())
        self.stderr = asyncio.create_task(self._drain_stderr())
        try:
            await self.call('initialize', {'clientInfo': {
                'name': 'codexcraft', 'title': 'Codexcraft', 'version': '1.1.0'}})
            await self.send({'method': 'initialized', 'params': {}})
        except BaseException:
            await self.close()
            raise
        return self

    async def _drain_stderr(self):
        while await self.process.stderr.read(8192):
            pass

    async def send(self, message):
        async with self.write_lock:
            self.process.stdin.write((json.dumps(message, ensure_ascii=False) + '\n').encode('utf-8'))
            await self.process.stdin.drain()

    async def call(self, method, params=None, timeout=45):
        self.counter += 1
        key = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        try:
            await self.send({'id': key, 'method': method, 'params': params or {}})
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(key, None)

    async def _respond(self, message):
        try:
            if not self.request_handler:
                raise RpcError('Unsupported server request: ' + message['method'])
            result = await self.request_handler(message['method'], message.get('params', {}))
            await self.send({'id': message['id'], 'result': result})
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.send({'id': message['id'], 'error': {
                'code': -32601, 'message': 'Request unsupported or expired in Codexcraft'}})

    async def _read(self):
        error = RpcError('Codex app-server disconnected')
        try:
            while raw := await self.process.stdout.readline():
                message = json.loads(raw)
                if 'method' in message:
                    if 'id' in message:
                        task = asyncio.create_task(self._respond(message))
                        self.workers.add(task)
                        task.add_done_callback(self.workers.discard)
                    elif self.notification:
                        self.notification(message['method'], message.get('params', {}))
                else:
                    future = self.pending.get(message.get('id'))
                    if future and not future.done():
                        if 'error' in message:
                            future.set_exception(RpcError(message['error'].get('message', 'Codex RPC error')))
                        else:
                            future.set_result(message.get('result', {}))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = RpcError('Codex protocol error: ' + str(exc))
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(error)
            if self.notification:
                self.notification('codexcraft/disconnected', {'message': str(error)})

    async def close(self):
        tasks = [t for t in [self.reader, self.stderr, *self.workers] if t]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
