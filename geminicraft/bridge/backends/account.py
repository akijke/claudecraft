"""Gemini CLI account: Google sign-in through ACP authenticate, or an API key; CLI install.

Signing in with Google: the bridge starts `gemini --experimental-acp` and calls
`authenticate` with the agent's Google method (oauth-personal) — Gemini CLI opens
the browser on this PC and stores its own credentials (~/.gemini). An API key
(aistudio.google.com/apikey) is kept in secrets.json and passed as GEMINI_API_KEY.
"""
import asyncio
import os
import shutil
from collections import deque
from pathlib import Path

from gemini_acp import Agent, AcpError, cli_command
from backends.connectors import load_secrets, store_secrets

PACKAGE = '@google/gemini-cli'


def credentials_file():
    return Path(os.environ.get('GEMINI_CLI_HOME') or Path.home()) / '.gemini' / 'oauth_creds.json'


class Account:
    def __init__(self, bridge):
        self.bridge = bridge
        self.flow = {'state': 'idle', 'url': None, 'code': None, 'error': None}
        self.install = {'state': 'idle', 'log': [], 'error': None}
        self.install_task = None
        self.login_task = None

    def api_key(self):
        return load_secrets(self.bridge.directory).get('gemini_api_key') or os.environ.get('GEMINI_API_KEY')

    def cli_info(self):
        try:
            path = cli_command(self.bridge.config)[0]
            return {'installed': True, 'path': path, 'install': self._install_view()}
        except ValueError:
            return {'installed': False, 'path': None, 'npm': bool(shutil.which('npm')), 'install': self._install_view()}

    def _install_view(self):
        error = self.install['error']
        return {'state': self.install['state'], 'log': list(self.install['log'])[-6:],
                'error': self.bridge.tr(error) if error else None}

    async def status(self):
        google = credentials_file().is_file()
        key = self.api_key()
        logged = google or bool(key)
        return {'account': {'logged_in': logged, 'method': 'Google' if google else 'API key' if key else None, 'email': None},
                'login': dict(self.flow), 'api_key': ('…' + key[-4:]) if key else None, 'cli': self.cli_info()}

    async def login(self, console=False):
        if self.login_task and not self.login_task.done():
            return
        self.flow = {'state': 'waiting_code', 'url': None, 'code': None, 'error': None}
        self.login_task = asyncio.create_task(self._login())

    async def _login(self):
        agent = Agent(self.bridge.config, cwd=str(self.bridge.workspace))
        try:
            await agent.start()
            methods = [m.get('id') for m in agent.auth_methods]
            method = next((m for m in methods if m and 'oauth' in m), methods[0] if methods else 'oauth-personal')
            await agent.call('authenticate', {'methodId': method}, timeout=600)   # the browser opens on this PC
            self.flow.update(state='done')
        except (AcpError, ValueError, OSError, TimeoutError) as e:
            self.flow.update(state='failed', error=str(e)[:300])
        finally:
            await agent.close()

    async def submit_code(self, text):
        raise ValueError('Вход в Google завершается в браузере — вернись в игру после него')

    async def cancel_login(self):
        if self.login_task and not self.login_task.done():
            self.login_task.cancel()
        self.flow = {'state': 'idle', 'url': None, 'code': None, 'error': None}

    async def logout(self):
        creds = credentials_file()
        if creds.is_file():
            creds.unlink()
        self.set_api_key('')

    def set_api_key(self, key):
        data = load_secrets(self.bridge.directory)
        key = (key or '').strip()
        if key:
            data['gemini_api_key'] = key
        else:
            data.pop('gemini_api_key', None)
        store_secrets(self.bridge.directory, data)

    async def install_cli(self):
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
            process = await asyncio.create_subprocess_exec(*command, 'install', '-g', PACKAGE, '--no-fund', '--no-audit',
                                                           stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                if line.strip():
                    log.append(line.decode('utf-8', 'replace').strip()[:200])
            code = await process.wait()
            if code != 0:
                raise ValueError(f'npm install завершился с кодом {code}')
            self.install['state'] = 'done'
        except Exception as e:
            self.install.update(state='failed', error=str(e))
