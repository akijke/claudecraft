"""Browser sign-in for remote MCP connectors (MCP authorization = OAuth 2.1 + PKCE).

Flow for /cc connect linear:
1. Discovery: an unauthenticated request to the MCP URL answers 401 with
   `WWW-Authenticate: Bearer resource_metadata="…"`; otherwise the well-known paths
   are tried (RFC 9728 protected resource → RFC 8414 authorization server metadata).
2. Dynamic client registration (RFC 7591) with redirect
   http://127.0.0.1:<bridge port>/oauth/callback (a public client, no secret).
3. The player opens the sign-in link (button in chat); the service redirects back
   to the bridge with ?code&state; the code is exchanged with the PKCE verifier.
4. Tokens live in secrets.json (owner-only) and are refreshed before each /cc
   request; the MCP server gets `Authorization: Bearer <token>`.
Nothing here is sent to the game except the sign-in link and statuses.
"""
import base64
import hashlib
import re
import secrets
import time
from urllib.parse import urlencode, urlparse, urlunparse

from backends.connectors import load_secrets, store_secrets

PENDING_TTL = 900


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _origin(url):
    p = urlparse(url)
    return f'{p.scheme}://{p.netloc}'


def _well_known(url, name):
    """RFC 8414/9728: /.well-known/<name> inserted before the path, then plain."""
    p = urlparse(url)
    path = p.path.rstrip('/')
    out = []
    if path:
        out.append(urlunparse((p.scheme, p.netloc, f'/.well-known/{name}{path}', '', '', '')))
    out.append(urlunparse((p.scheme, p.netloc, f'/.well-known/{name}', '', '', '')))
    return out


class OAuth:
    def __init__(self, bridge):
        self.bridge = bridge
        self.pending = {}  # state -> flow data
        self.session = None

    def redirect_uri(self):
        return f'http://127.0.0.1:{self.bridge.port}/oauth/callback'

    async def _http(self):
        import aiohttp
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20),
                                                 headers={'User-Agent': 'Claudecraft'})
        return self.session

    async def close(self):
        if self.session is not None and not self.session.closed:
            await self.session.close()

    async def _json(self, method, url, **kw):
        http = await self._http()
        async with http.request(method, url, **kw) as r:
            if r.status >= 400:
                body = (await r.text())[:300]
                raise ValueError(f'OAuth: {urlparse(url).netloc} ответил {r.status}: {body}')
            return await r.json(content_type=None)

    async def _first(self, urls):
        http = await self._http()
        for url in urls:
            try:
                async with http.get(url, headers={'Accept': 'application/json'}) as r:
                    if r.status == 200:
                        return await r.json(content_type=None)
            except Exception:
                continue
        return None

    async def discover(self, url):
        """Authorization server metadata for an MCP server URL."""
        http = await self._http()
        resource_meta = None
        try:
            async with http.post(url, json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                    'protocolVersion': '2025-06-18', 'capabilities': {},
                    'clientInfo': {'name': 'claudecraft', 'version': '1'}}},
                    headers={'Accept': 'application/json, text/event-stream'}) as r:
                header = r.headers.get('WWW-Authenticate', '')
                m = re.search(r'resource_metadata="([^"]+)"', header)
                if m:
                    resource_meta = await self._first([m.group(1)])
        except Exception:
            pass
        if resource_meta is None:
            resource_meta = await self._first(_well_known(url, 'oauth-protected-resource'))
        issuer = (resource_meta or {}).get('authorization_servers', [None])[0] or _origin(url)
        meta = await self._first(_well_known(issuer, 'oauth-authorization-server')
                                 + _well_known(issuer, 'openid-configuration'))
        if meta is None:  # older servers: default endpoints at the origin
            base = _origin(issuer)
            meta = {'authorization_endpoint': base + '/authorize', 'token_endpoint': base + '/token',
                    'registration_endpoint': base + '/register'}
        meta['scopes'] = (resource_meta or {}).get('scopes_supported') or meta.get('scopes_supported') or []
        meta['resource'] = (resource_meta or {}).get('resource') or url
        return meta

    async def start(self, cid, url):
        """Returns the sign-in URL for the player."""
        for state in [s for s, f in self.pending.items() if f['time'] < time.time() - PENDING_TTL]:
            self.pending.pop(state, None)
        meta = await self.discover(url)
        stored = self.stored(cid)
        client = stored.get('client') if stored.get('token_endpoint') == meta['token_endpoint'] else None
        if not client:
            if not meta.get('registration_endpoint'):
                raise ValueError(f'{cid}: сервер не поддерживает автоматическую регистрацию клиента — нужен ключ')
            reg = await self._json('POST', meta['registration_endpoint'], json={
                'client_name': 'Claudecraft (Minecraft)', 'redirect_uris': [self.redirect_uri()],
                'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code'],
                'token_endpoint_auth_method': 'none'})
            client = {'client_id': reg['client_id'], 'client_secret': reg.get('client_secret')}
        verifier = _b64(secrets.token_bytes(32))
        state = _b64(secrets.token_bytes(18))
        params = {'response_type': 'code', 'client_id': client['client_id'], 'redirect_uri': self.redirect_uri(),
                  'code_challenge': _b64(hashlib.sha256(verifier.encode()).digest()),
                  'code_challenge_method': 'S256', 'state': state, 'resource': meta['resource']}
        if meta['scopes']:
            params['scope'] = ' '.join(meta['scopes'])
        self.pending[state] = {'cid': cid, 'verifier': verifier, 'client': client, 'meta': meta, 'time': time.time()}
        sep = '&' if '?' in meta['authorization_endpoint'] else '?'
        return meta['authorization_endpoint'] + sep + urlencode(params)

    async def callback(self, state, code, error=None):
        """Browser redirect target. Returns the connector id."""
        flow = self.pending.pop(state or '', None)
        if flow is None:
            raise ValueError('Ссылка входа устарела или уже использована — начни заново: /cc connect')
        if error:
            raise ValueError(f'{flow["cid"]}: вход отменён ({error})')
        meta, client = flow['meta'], flow['client']
        form = {'grant_type': 'authorization_code', 'code': code, 'redirect_uri': self.redirect_uri(),
                'client_id': client['client_id'], 'code_verifier': flow['verifier'], 'resource': meta['resource']}
        if client.get('client_secret'):
            form['client_secret'] = client['client_secret']
        tokens = await self._json('POST', meta['token_endpoint'], data=form, headers={'Accept': 'application/json'})
        self._save(flow['cid'], tokens, meta, client)
        return flow['cid']

    def _save(self, cid, tokens, meta, client):
        if not tokens.get('access_token'):
            raise ValueError(f'{cid}: сервер не выдал access_token')
        data = load_secrets(self.bridge.directory)
        old = data.setdefault('oauth', {}).get(cid, {})
        data['oauth'][cid] = {
            'access_token': tokens['access_token'],
            'refresh_token': tokens.get('refresh_token') or old.get('refresh_token'),
            'expires_at': time.time() + float(tokens['expires_in']) if tokens.get('expires_in') else None,
            'token_endpoint': meta['token_endpoint'], 'resource': meta.get('resource'), 'client': client}
        store_secrets(self.bridge.directory, data)

    def stored(self, cid):
        return load_secrets(self.bridge.directory).get('oauth', {}).get(cid, {})

    def has_token(self, cid):
        return bool(self.stored(cid).get('access_token'))

    def forget(self, cid):
        data = load_secrets(self.bridge.directory)
        if data.get('oauth', {}).pop(cid, None) is not None:
            store_secrets(self.bridge.directory, data)

    async def refresh(self, cid, margin=120):
        """Refreshes the token when it expires within `margin` seconds."""
        info = self.stored(cid)
        expires = info.get('expires_at')
        if not info.get('refresh_token') or not expires or expires - time.time() > margin:
            return False
        client = info.get('client') or {}
        form = {'grant_type': 'refresh_token', 'refresh_token': info['refresh_token'],
                'client_id': client.get('client_id', '')}
        if info.get('resource'):
            form['resource'] = info['resource']
        if client.get('client_secret'):
            form['client_secret'] = client['client_secret']
        tokens = await self._json('POST', info['token_endpoint'], data=form, headers={'Accept': 'application/json'})
        self._save(cid, tokens, {'token_endpoint': info['token_endpoint'], 'resource': info.get('resource')}, client)
        return True

    async def refresh_enabled(self):
        """Before a /cc request: refresh tokens of enabled OAuth connectors (failures are reported)."""
        problems = []
        for cid in self.bridge.connectors.enabled:
            if self.bridge.connectors.is_oauth(cid) and self.has_token(cid):
                try:
                    await self.refresh(cid)
                except Exception as e:
                    problems.append(f'{cid}: {e}')
        return problems
