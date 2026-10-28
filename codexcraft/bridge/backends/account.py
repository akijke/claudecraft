"""Authentication through Codex's own account store, never Anthropic credentials.

Also installs the Codex CLI in one click (npm install -g @openai/codex) when it is
missing; needs Node.js. The installed path is saved as codex_path in bridge-config.json.
"""
import asyncio
import os
import shutil
from collections import deque
from pathlib import Path

from codex_rpc import AppServer, cli_command

PACKAGE = '@openai/codex'


def cli_path(config):
    return cli_command(config)[0]


class Account:
    def __init__(self, bridge):
        self.bridge = bridge
        self.rpc = None
        self.login_id = None
        self.flow = {'state': 'idle', 'url': None, 'code': None, 'error': None}
        self.install = {'state': 'idle', 'log': [], 'error': None}
        self.install_task = None

    def cli_info(self):
        try:
            path = cli_command(self.bridge.config)[-1]
            return {'installed': True, 'path': path, 'install': self._install_view()}
        except ValueError:
            return {'installed': False, 'path': None, 'npm': bool(shutil.which('npm')),
                    'install': self._install_view()}

    def _install_view(self):
        error = self.install['error']
        return {'state': self.install['state'], 'log': list(self.install['log'])[-6:],
                'error': self.bridge.tr(error) if error else None}

    async def install_cli(self):
        """npm install -g @openai/codex in the background; progress shows in /account."""
        if self.install_task and not self.install_task.done():
            return
        npm = shutil.which('npm')
        if not npm:
            raise ValueError('Нужен Node.js (npm): установи с nodejs.org, перезапусти мост и нажми ещё раз')
        self.install = {'state': 'running', 'log': deque(maxlen=40), 'error': None}
        self.install_task = asyncio.create_task(self._install(npm))

    async def _install(self, npm):
        log = self.install['log']
        command = ['cmd', '/c', npm] if os.name == 'nt' else [npm]
        try:
            process = await asyncio.create_subprocess_exec(
                *command, 'install', '-g', PACKAGE, '--no-fund', '--no-audit',
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                text = line.decode('utf-8', 'replace').strip()
                if text:
                    log.append(text[:200])
            code = await process.wait()
            if code != 0:
                raise ValueError(f'npm install завершился с кодом {code}' + (': ' + log[-1] if log else ''))
            path = await self._find_installed(npm)
            if not path:
                raise ValueError('Codex установлен, но codex не найден в PATH — укажи codex_path в bridge-config.json')
            from backends.util import save_json
            self.bridge.config['codex_path'] = path
            save_json(self.bridge.directory / 'bridge-config.json', self.bridge.config)
            self.install['state'] = 'done'
            log.append('codex: ' + path)
        except Exception as e:
            self.install.update(state='failed', error=str(e))

    async def _find_installed(self, npm):
        found = shutil.which('codex')
        if found:
            return found
        command = ['cmd', '/c', npm] if os.name == 'nt' else [npm]
        process = await asyncio.create_subprocess_exec(*command, 'prefix', '-g', stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.DEVNULL)
        prefix = (await process.communicate())[0].decode('utf-8', 'replace').strip()
        if not prefix:
            return None
        for candidate in (Path(prefix) / 'codex.cmd', Path(prefix) / 'bin' / 'codex', Path(prefix) / 'codex'):
            if candidate.is_file():
                return str(candidate)
        return None

    def notify(self, method, params):
        if method == 'account/login/completed':
            self.flow.update(state='done' if params.get('success') else 'failed', error=params.get('error'))
            self.login_id = None
        elif method == 'codexcraft/disconnected' and self.flow['state'] == 'waiting_code':
            self.flow.update(state='failed', error='Codex disconnected')

    async def connection(self):
        if not self.rpc or self.rpc.reader.done():
            if self.rpc:
                await self.rpc.close()
            self.rpc = await AppServer(self.bridge.config, self.notify).start()
        return self.rpc

    async def status(self):
        try:
            rpc = await self.connection()
            data = await rpc.call('account/read', {'refreshToken': False})
            account = data.get('account') or {}
            return {'account': {'logged_in': bool(account), 'email': account.get('email'),
                                'method': account.get('type')}, 'login': dict(self.flow), 'api_key': None,
                    'cli': self.cli_info()}
        except (ValueError, OSError, TimeoutError) as exc:
            return {'account': {'logged_in': False}, 'login': dict(self.flow), 'error': str(exc),
                    'cli': self.cli_info()}

    async def login(self, console=False):
        rpc = await self.connection()
        if self.login_id:
            return
        data = await rpc.call('account/login/start', {'type': 'chatgptDeviceCode'})
        self.login_id = data.get('loginId')
        self.flow = {'state': 'waiting_code', 'url': data.get('verificationUrl'),
                     'code': data.get('userCode'), 'error': None}

    async def submit_code(self, text):
        raise ValueError('Введи код на странице входа ChatGPT, затем вернись в игру.')

    async def cancel_login(self):
        if self.rpc:
            try:
                if self.login_id and not self.rpc.reader.done():
                    await self.rpc.call('account/login/cancel', {'loginId': self.login_id}, timeout=5)
            finally:
                await self.rpc.close()
                self.rpc = None
        self.login_id = None
        self.flow = {'state': 'idle', 'url': None, 'code': None, 'error': None}

    async def logout(self):
        rpc = await self.connection()
        await rpc.call('account/logout')
        await self.cancel_login()

    def set_api_key(self, key):
        raise ValueError('Для API-ключа используй codex login --with-api-key в терминале.')

    def api_key(self):
        return None
