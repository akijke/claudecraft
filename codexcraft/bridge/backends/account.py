"""Authentication through Codex's own account store, never Anthropic credentials."""
from codex_rpc import AppServer, cli_command


def cli_path(config):
    return cli_command(config)[0]


class Account:
    def __init__(self, bridge):
        self.bridge = bridge
        self.rpc = None
        self.login_id = None
        self.flow = {'state': 'idle', 'url': None, 'code': None, 'error': None}

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
                                'method': account.get('type')}, 'login': dict(self.flow), 'api_key': None}
        except (ValueError, OSError, TimeoutError) as exc:
            return {'account': {'logged_in': False}, 'login': dict(self.flow), 'error': str(exc)}

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
