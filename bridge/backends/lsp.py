"""A small Language Server Protocol client for the in-game editor.

Diagnostics (errors/warnings under the code), go to definition (F12) and
completion (Ctrl+Space) from language servers that are already installed:
pyright / pylsp / jedi (Python), typescript-language-server, jdtls, gopls,
rust-analyzer, clangd — or any command set in bridge-config.json → "lsp":
{".ext": ["command", "args"…]}. One server per (command, project folder), started
on first use and kept while the bridge runs.
"""
import asyncio
import json
import os
import shutil
from pathlib import Path

SERVERS = {
    '.py': [['pyright-langserver', '--stdio'], ['pylsp'], ['jedi-language-server']],
    '.ts': [['typescript-language-server', '--stdio']], '.tsx': [['typescript-language-server', '--stdio']],
    '.js': [['typescript-language-server', '--stdio']], '.jsx': [['typescript-language-server', '--stdio']],
    '.java': [['jdtls']], '.go': [['gopls']], '.rs': [['rust-analyzer']],
    '.c': [['clangd']], '.cpp': [['clangd']], '.h': [['clangd']], '.hpp': [['clangd']],
}
LANGUAGE_IDS = {'.py': 'python', '.ts': 'typescript', '.tsx': 'typescriptreact', '.js': 'javascript',
                '.jsx': 'javascriptreact', '.java': 'java', '.go': 'go', '.rs': 'rust', '.c': 'c', '.cpp': 'cpp',
                '.h': 'c', '.hpp': 'cpp'}
HINTS = {'.py': 'npm i -g pyright  или  pip install python-lsp-server', '.ts': 'npm i -g typescript-language-server typescript',
         '.java': 'jdtls (Eclipse JDT LS)', '.go': 'go install golang.org/x/tools/gopls@latest',
         '.rs': 'rustup component add rust-analyzer', '.c': 'clangd (LLVM)'}


def uri(path):
    return Path(path).resolve().as_uri()


def path_of(u):
    from urllib.parse import unquote, urlparse
    p = unquote(urlparse(u).path)
    if os.name == 'nt' and p.startswith('/') and len(p) > 2 and p[2] == ':':
        p = p[1:]
    return str(Path(p))


class Server:
    def __init__(self, command, root):
        self.command, self.root = command, root
        self.process = None
        self.counter = 0
        self.pending = {}
        self.diagnostics = {}            # uri -> list
        self.diag_events = {}            # uri -> asyncio.Event
        self.versions = {}               # uri -> version
        self.reader = None

    async def start(self):
        exe = shutil.which(self.command[0])
        command = [exe] + self.command[1:]
        if os.name == 'nt' and exe.lower().endswith(('.cmd', '.bat')):
            command = ['cmd', '/c'] + command
        self.process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
                                                            stdout=asyncio.subprocess.PIPE,
                                                            stderr=asyncio.subprocess.DEVNULL, cwd=self.root)
        self.reader = asyncio.create_task(self._read())
        await self.request('initialize', {'processId': os.getpid(), 'rootUri': uri(self.root),
                                          'workspaceFolders': [{'uri': uri(self.root), 'name': Path(self.root).name}],
                                          'capabilities': {'textDocument': {
                                              'publishDiagnostics': {}, 'definition': {},
                                              'completion': {'completionItem': {'snippetSupport': False}},
                                              'synchronization': {'didSave': True}}}}, timeout=60)
        await self.notify('initialized', {})
        return self

    async def _send(self, message):
        body = json.dumps(message).encode('utf-8')
        self.process.stdin.write(b'Content-Length: %d\r\n\r\n' % len(body) + body)
        await self.process.stdin.drain()

    async def request(self, method, params, timeout=10):
        self.counter += 1
        rid = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[rid] = future
        await self._send({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params})
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(rid, None)

    async def notify(self, method, params):
        await self._send({'jsonrpc': '2.0', 'method': method, 'params': params})

    async def _read(self):
        stream = self.process.stdout
        try:
            while True:
                length = None
                while True:
                    line = await stream.readline()
                    if not line:
                        return
                    line = line.strip()
                    if not line:
                        break
                    if line.lower().startswith(b'content-length:'):
                        length = int(line.split(b':', 1)[1])
                if length is None:
                    continue
                message = json.loads(await stream.readexactly(length))
                if 'id' in message and ('result' in message or 'error' in message):
                    future = self.pending.get(message['id'])
                    if future and not future.done():
                        if 'error' in message:
                            future.set_exception(ValueError(message['error'].get('message', 'LSP error')))
                        else:
                            future.set_result(message.get('result'))
                elif message.get('method') == 'textDocument/publishDiagnostics':
                    p = message.get('params') or {}
                    self.diagnostics[p.get('uri')] = p.get('diagnostics') or []
                    self.diag_events.setdefault(p.get('uri'), asyncio.Event()).set()
                elif 'id' in message and 'method' in message:  # server → client requests: answer politely
                    await self._send({'jsonrpc': '2.0', 'id': message['id'], 'result': None})
        except (asyncio.IncompleteReadError, ValueError, ConnectionError):
            return

    async def sync(self, path, text, ext):
        u = uri(path)
        event = self.diag_events.setdefault(u, asyncio.Event())
        event.clear()
        if u not in self.versions:
            self.versions[u] = 1
            await self.notify('textDocument/didOpen', {'textDocument': {
                'uri': u, 'languageId': LANGUAGE_IDS.get(ext, ext.lstrip('.')), 'version': 1, 'text': text}})
        else:
            self.versions[u] += 1
            await self.notify('textDocument/didChange', {'textDocument': {'uri': u, 'version': self.versions[u]},
                                                         'contentChanges': [{'text': text}]})
        try:
            await asyncio.wait_for(event.wait(), 3)
        except TimeoutError:
            pass  # some servers publish later; the next sync shows them
        return self.diagnostics.get(u, [])

    async def close(self):
        try:
            await asyncio.wait_for(self.request('shutdown', None), 3)
            await self.notify('exit', None)
        except Exception:
            pass
        if self.process and self.process.returncode is None:
            self.process.kill()
        if self.reader:
            self.reader.cancel()


class Lsp:
    def __init__(self, bridge):
        self.bridge = bridge
        self.servers = {}

    def command_for(self, ext):
        custom = (self.bridge.config.get('lsp') or {}).get(ext)
        for command in ([custom] if custom else []) + SERVERS.get(ext, []):
            if command and shutil.which(command[0]):
                return command
        return None

    async def server(self, path, root):
        ext = Path(path).suffix.lower()
        command = self.command_for(ext)
        if not command:
            raise ValueError('Нет языкового сервера для ' + (ext or 'этого файла')
                             + (f' — установи: {HINTS[ext]}' if ext in HINTS else ''))
        key = (tuple(command), str(root))
        server = self.servers.get(key)
        if server is None or server.process.returncode is not None:
            server = await Server(command, str(root)).start()
            self.servers[key] = server
        return server, ext

    @staticmethod
    def _diag(d):
        r = d.get('range', {}).get('start', {})
        e = d.get('range', {}).get('end', {})
        return {'line': r.get('line', 0), 'col': r.get('character', 0), 'end_line': e.get('line', 0),
                'end_col': e.get('character', 0), 'severity': d.get('severity', 1),
                'message': str(d.get('message', ''))[:500], 'source': d.get('source', '')}

    async def sync(self, path, text, root):
        server, ext = await self.server(path, root)
        diags = await server.sync(path, text, ext)
        return {'server': Path(server.command[0]).name, 'diagnostics': [self._diag(d) for d in diags][:200]}

    async def definition(self, path, text, line, col, root):
        server, ext = await self.server(path, root)
        await server.sync(path, text, ext)
        result = await server.request('textDocument/definition', {'textDocument': {'uri': uri(path)},
                                                                    'position': {'line': line, 'character': col}})
        items = result if isinstance(result, list) else [result] if result else []
        out = []
        for loc in items:
            target = loc.get('uri') or loc.get('targetUri')
            rng = loc.get('range') or loc.get('targetSelectionRange') or {}
            if target:
                out.append({'path': path_of(target), 'line': rng.get('start', {}).get('line', 0) + 1})
        return {'locations': out}

    async def complete(self, path, text, line, col, root):
        server, ext = await self.server(path, root)
        await server.sync(path, text, ext)
        result = await server.request('textDocument/completion', {'textDocument': {'uri': uri(path)},
                                                                    'position': {'line': line, 'character': col}})
        items = result.get('items', []) if isinstance(result, dict) else result or []
        out = []
        for it in items[:60]:
            edit = it.get('textEdit') or {}
            out.append({'label': it.get('label', ''), 'insert': edit.get('newText') or it.get('insertText') or it.get('label', ''),
                        'detail': str(it.get('detail') or '')[:80]})
        return {'items': out}

    async def close(self):
        for server in list(self.servers.values()):
            await server.close()
        self.servers.clear()
