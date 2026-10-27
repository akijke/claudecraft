"""Claude account connection for the in-game settings screen.

Login runs the real `claude auth login`: the CLI prints a sign-in URL and waits for
the code from the browser page on stdin. The mod shows the URL and a text field;
the code is written to the CLI's stdin. Credentials are stored by Claude Code
itself (same place as a terminal login); the bridge never sees them.

The API key (for /c new api chats) is kept in secrets.json next to chats.json.
"""
import asyncio
import json
import os
from pathlib import Path
import re
import shutil


URL = re.compile(r'https://\S+')


def cli_path(config):
    """CLI used for chats: config cli_path, else the SDK's bundled binary, else PATH."""
    if config.get('cli_path'):
        return config['cli_path']
    try:
        import claude_agent_sdk
        name = 'claude.exe' if os.name == 'nt' else 'claude'
        bundled = Path(claude_agent_sdk.__file__).parent / '_bundled' / name
        if bundled.exists():
            return str(bundled)
    except ImportError:
        pass
    return shutil.which('claude')


def cli_command(config):
    binary = cli_path(config)
    if not binary:
        raise ValueError('Claude Code CLI не найден: установи claude-agent-sdk или укажи cli_path')
    return [binary]


def mask(key):
    return key[:7] + '…' + key[-4:] if len(key) > 14 else '…'


class Account:
    def __init__(self, bridge):
        self.bridge = bridge
        self.secrets_path = bridge.directory / 'secrets.json'
        self.process = None
        self.reader = None
        self.login_state = 'idle'  # idle | starting | waiting_code | checking | done | failed
        self.login_url = None
        self.login_error = None
        self.output = []
        self.cached = None

    # ---- API key -------------------------------------------------------
    def api_key(self):
        from backends.connectors import load_secrets
        return load_secrets(self.bridge.directory).get('anthropic_api_key') or os.environ.get('ANTHROPIC_API_KEY')

    def set_api_key(self, key):
        """Stored in the shared secrets.json next to connector tokens (merged, not overwritten)."""
        from backends.connectors import load_secrets, store_secrets
        key = (key or '').strip()
        if key and (len(key) > 300 or not re.fullmatch(r'[A-Za-z0-9_\-]+', key)):
            raise ValueError('API-ключ выглядит неверно')
        data = load_secrets(self.bridge.directory)
        if key:
            data['anthropic_api_key'] = key
        else:
            data.pop('anthropic_api_key', None)
        store_secrets(self.bridge.directory, data)

    # ---- CLI helpers ---------------------------------------------------
    async def run_cli(self, *args, timeout=30):
        process = await asyncio.create_subprocess_exec(
            *cli_command(self.bridge.config), *args, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(process.communicate(), timeout)
        except TimeoutError:
            process.kill()
            raise ValueError('Claude CLI не ответил вовремя') from None
        return process.returncode, out.decode('utf-8', 'replace')

    async def status(self):
        """Account info for the settings screen. Never includes secrets."""
        account = {'logged_in': False, 'method': None, 'email': None, 'error': None}
        try:
            code, out = await self.run_cli('auth', 'status', '--json', timeout=20)
            data = json.loads(out[out.find('{'):]) if '{' in out else {}
            account['logged_in'] = bool(data.get('loggedIn'))
            account['method'] = data.get('authMethod')
            account['email'] = data.get('email') or data.get('emailAddress')
            account['subscription'] = data.get('subscriptionType')
        except (ValueError, OSError) as e:
            account['error'] = str(e)[:300]
        key = self.api_key()
        self.cached = account
        return {'account': account,
                'api_key': mask(key) if key else None,
                'login': {'state': self.login_state, 'url': self.login_url,
                          'error': self.login_error}}

    # ---- login flow ----------------------------------------------------
    async def login(self, console=False):
        if self.process and self.process.returncode is None:
            return  # already waiting for a code
        command = cli_command(self.bridge.config)
        args = ['auth', 'login'] + (['--console'] if console else [])
        env = dict(os.environ)
        # Never pop a browser from the bridge: the user opens the link from Minecraft.
        env['BROWSER'] = 'none'
        self.login_state, self.login_url, self.login_error, self.output = 'starting', None, None, []
        self.process = await asyncio.create_subprocess_exec(
            *command, *args, env=env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        self.reader = asyncio.create_task(self._read(self.process))

    async def _read(self, process):
        buffer = ''
        while True:
            chunk = await process.stdout.read(4096)
            if not chunk:
                break
            buffer += chunk.decode('utf-8', 'replace')
            # The URL line ends with a newline; the "Paste code" prompt does not.
            lines = buffer.split('\n')
            buffer = lines.pop()
            for line in lines + ([buffer] if 'code' in buffer.lower() else []):
                self.output.append(line.strip())
                match = URL.search(line)
                if match and not self.login_url:
                    self.login_url = match.group(0)
                    self.login_state = 'waiting_code'
        await process.wait()
        if process.returncode == 0:
            self.login_state = 'done'
        else:
            self.login_state = 'failed'
            tail = [l for l in self.output if l][-3:]
            self.login_error = (' '.join(tail) or f'код выхода {process.returncode}')[:400]

    async def submit_code(self, code):
        code = (code or '').strip()
        if not code or len(code) > 2000 or any(c in code for c in '\r\n'):
            raise ValueError('Вставь код со страницы входа одной строкой')
        if not self.process or self.process.returncode is not None:
            raise ValueError('Вход не запущен: нажми «Войти»')
        self.process.stdin.write((code + '\n').encode())
        await self.process.stdin.drain()
        self.login_state = 'checking'

    async def cancel_login(self):
        if self.process and self.process.returncode is None:
            self.process.kill()
            await self.process.wait()
        if self.reader:
            await asyncio.gather(self.reader, return_exceptions=True)
        self.process = None
        self.login_state, self.login_url = 'idle', None

    async def logout(self):
        await self.cancel_login()
        code, out = await self.run_cli('auth', 'logout')
        if code != 0:
            raise ValueError('Выход не удался: ' + out.strip()[:300])
