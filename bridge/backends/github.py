"""GitHub sign-in from the game, through the official GitHub CLI (gh).

`gh auth login --web` (device flow) prints a one-time code and github.com/login/device;
the mod shows both, the user confirms in the browser, gh stores the token in the
system keyring. Afterwards `gh auth setup-git` lets git push/pull use it, and the
GitHub MCP connector is switched on with the same token. Without gh, a personal
access token can be given instead (used by the connector only).
"""
import asyncio
import json
import os
import re
import shutil

CODE = re.compile(r'code\s*\(?([A-Z0-9]{4}-[A-Z0-9]{4})\)?', re.I)
URL = re.compile(r'https://github\.com/login/device\S*')


class GitHub:
    def __init__(self, bridge):
        self.bridge = bridge
        self.process = None
        self.reader = None
        self.state = 'idle'  # idle | waiting | done | failed
        self.code = None
        self.url = None
        self.error = None

    def gh(self):
        return shutil.which('gh')

    async def run(self, *args, timeout=30):
        process = await asyncio.create_subprocess_exec(
            self.gh(), *args, stdin=asyncio.subprocess.DEVNULL, env=self.env(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(process.communicate(), timeout)
        except TimeoutError:
            process.kill()
            return 1, ''
        return process.returncode, out.decode('utf-8', 'replace')

    @staticmethod
    def env():
        # Never pop a browser from the bridge: the link is opened from the game.
        return dict(os.environ, GH_BROWSER='echo', BROWSER='echo', GH_PROMPT_DISABLED='1', NO_COLOR='1')

    async def status(self):
        info = {'installed': bool(self.gh()), 'login': None, 'scopes': None,
                'connector': 'github' in self.bridge.connectors.enabled,
                'token': bool(self.bridge.connectors.secrets('github').get('GITHUB_TOKEN')),
                'flow': {'state': self.state, 'code': self.code, 'url': self.url, 'error': self.error}}
        if info['installed']:
            code, out = await self.run('auth', 'status', '--hostname', 'github.com', '--json', 'hosts')
            try:
                accounts = json.loads(out[out.find('{'):]).get('hosts', {}).get('github.com', [])
                active = next((a for a in accounts if a.get('active') and a.get('state') == 'success'), None)
                if active:
                    info['login'], info['scopes'] = active.get('login'), active.get('scopes')
            except ValueError:
                pass
        return info

    async def login(self):
        if not self.gh():
            raise ValueError('Нужен GitHub CLI: установи с cli.github.com и перезапусти мост')
        if self.process and self.process.returncode is None:
            return
        self.state, self.code, self.url, self.error = 'waiting', None, None, None
        self.process = await asyncio.create_subprocess_exec(
            self.gh(), 'auth', 'login', '--hostname', 'github.com', '--git-protocol', 'https', '--web',
            '--skip-ssh-key', '--scopes', 'repo,workflow,read:org,gist',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env=self.env())
        self.reader = asyncio.create_task(self._read(self.process))

    async def _read(self, process):
        tail = []
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            text = line.decode('utf-8', 'replace').strip()
            tail = (tail + [text])[-4:]
            if (m := CODE.search(text)) and not self.code:
                self.code = m.group(1)
            if (m := URL.search(text)) and not self.url:
                self.url = m.group(0)
            if 'press enter' in text.lower() and process.stdin:
                process.stdin.write(b'\n')
        await process.wait()
        if process.returncode == 0:
            await self.run('auth', 'setup-git', '--hostname', 'github.com')
            self.bridge.connectors.connect('github')  # token comes from gh auth token
            self.bridge.reload_extensions()
            self.state = 'done'
        else:
            self.state, self.error = 'failed', (' '.join(t for t in tail if t) or 'gh auth login failed')[:300]

    async def cancel(self):
        if self.process and self.process.returncode is None:
            self.process.kill()
            await self.process.wait()
        if self.reader:
            await asyncio.gather(self.reader, return_exceptions=True)
        self.process, self.state, self.code, self.url = None, 'idle', None, None

    async def logout(self):
        await self.cancel()
        info = await self.status()
        if info['login']:
            code, out = await self.run('auth', 'logout', '--hostname', 'github.com', '--user', info['login'])
            if code != 0:
                raise ValueError('gh auth logout: ' + out.strip()[:200])
        self.bridge.connectors.disconnect('github')
        self.bridge.reload_extensions()

    def set_token(self, token):
        token = (token or '').strip()
        if not re.fullmatch(r'(gh[pousr]_[A-Za-z0-9_]{20,255}|github_pat_[A-Za-z0-9_]{20,255})', token):
            raise ValueError('Это не похоже на токен GitHub (ghp_… или github_pat_…)')
        self.bridge.connectors.connect('github', {'GITHUB_TOKEN': token})
        self.bridge.reload_extensions()

    def connector(self, on):
        if on:
            missing = self.bridge.connectors.connect('github')
            if missing:
                raise ValueError('Сначала войди в GitHub или задай токен')
        else:
            self.bridge.connectors.disconnect('github')
        self.bridge.reload_extensions()
