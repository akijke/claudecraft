"""Guest tokens for pair programming (#69): a friend watches one chat and may suggest messages.

A guest token is bound to ONE chat and has a scope:
  view — read the chat's events (GET /guest/events) and the chat title/state;
  chat — also add messages to that chat's queue (POST /guest/say); they arrive as
         "[guest <name>]: …" so Claude and the owner see who wrote them.
Guests never answer permission prompts, never use f, files, git, terminal, settings or
any other endpoint: everything outside /guest/* is owner-only. Tokens live in
guests.json (0600), are shown once when created, and can be revoked at any time.

What is NOT here yet (needs the owner, see docs/NIGHT_REPORT.md): a way for the guest to
reach the bridge (it listens on 127.0.0.1 only — SSH tunnel, Tailscale or a relay) and a
guest client (a second Minecraft with this mod using /claude remote, or a small web page).
"""
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

SCOPES = ('view', 'chat')
NAME = re.compile(r'^[\w.-]{1,24}$')


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Guests:
    def __init__(self, directory):
        self.file = Path(directory) / 'guests.json'
        self.rows = {}
        try:
            self.rows = json.loads(self.file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass

    def save(self):
        self.file.write_text(json.dumps(self.rows, ensure_ascii=False, indent=2), encoding='utf-8')
        try:
            os.chmod(self.file, 0o600)
        except OSError:
            pass

    def create(self, name, chat_id, scope='view', hours=12):
        if not NAME.match(name or ''):
            raise ValueError('guest: имя — буквы, цифры, . _ - (до 24)')
        if scope not in SCOPES:
            raise ValueError('guest: права view или chat')
        token = 'g_' + secrets.token_urlsafe(24)
        # only the hash is stored: guests.json leaking does not leak working tokens
        self.rows[name] = {'hash': digest(token), 'chat': chat_id, 'scope': scope,
                           'created': round(time.time()), 'expires': round(time.time() + hours * 3600)}
        self.save()
        return token

    def revoke(self, name):
        if self.rows.pop(name, None) is None:
            raise ValueError(f'guest: нет гостя {name}')
        self.save()

    def find(self, header):
        """Authorization header → (name, row) of a live guest, or None."""
        if not header.startswith('Bearer g_'):
            return None
        h = digest(header[7:])
        now = time.time()
        for name, row in self.rows.items():
            if secrets.compare_digest(row['hash'], h) and row['expires'] > now:
                return name, row
        return None

    def list(self):
        now = time.time()
        return [{'name': n, 'chat': r['chat'], 'scope': r['scope'],
                 'left_min': max(0, round((r['expires'] - now) / 60))} for n, r in self.rows.items()]


def visible(event, chat_id):
    """Events a guest may see: only their chat, and never permission/secret-bearing kinds."""
    return event.get('chat') == chat_id and event.get('kind') not in ('permission', 'question', 'account', 'url', 'guest_token', 'history', 'guests')
