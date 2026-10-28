"""Spending limits: per request (queue item), per chat and per day, in $ and/or tokens.

/cc budget                      — limits and what is spent
/cc budget task $0.50           — one request may cost up to $0.50
/cc budget chat 2M              — a chat may use up to 2 million tokens (k/M suffixes)
/cc budget day $5 · day off     — daily limit for all chats; "off" removes a limit
/cc budget resume               — continue a paused chat (once over the limit)

When a limit is reached the running request is interrupted (the session and the
history stay), the chat's queue is paused and the player gets a card with
"continue anyway" and "raise the limit". Costs are Claude Code's own estimates at
API prices; on a subscription the limit is spent, not money. Tokens count input
(incl. cache) + output, like /cc tokens.
"""
import json
import re
import time
from pathlib import Path

SCOPES = ('task', 'chat', 'day')


def parse_amount(text):
    """'$0.5' → ('usd', 0.5); '200k' → ('tokens', 200000); 'off' → (None, None)."""
    text = text.strip().lower().replace(',', '.')
    if text in ('off', 'none', '0', 'нет'):
        return None, None
    m = re.fullmatch(r'\$?\s*(\d+(?:\.\d+)?)\s*(\$|usd)?', text)
    if m and (text.startswith('$') or m.group(2)):
        return 'usd', float(m.group(1))
    m = re.fullmatch(r'(\d+(?:\.\d+)?)\s*([km]?)\s*(tokens?|токен\w*)?', text)
    if m:
        return 'tokens', int(float(m.group(1)) * {'': 1, 'k': 1_000, 'm': 1_000_000}[m.group(2)])
    raise ValueError('Лимит: $0.50, 200k, 2M или off')


def tokens_of(usage):
    return sum(int(usage.get(k) or 0) for k in ('input_tokens', 'cache_read_input_tokens',
                                                'cache_creation_input_tokens', 'output_tokens'))


class Budget:
    def __init__(self, bridge):
        self.bridge = bridge
        self.file = Path(bridge.directory) / 'budget.json'
        try:
            data = json.loads(self.file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
        self.limits = data.get('limits', {})          # scope -> {'usd': x, 'tokens': n}
        self.day = data.get('day', {'date': '', 'usd': 0.0, 'tokens': 0})

    def save(self):
        from backends.util import save_json
        save_json(self.file, {'limits': self.limits, 'day': self.day})

    def _today(self):
        today = time.strftime('%Y-%m-%d')
        if self.day.get('date') != today:
            self.day = {'date': today, 'usd': 0.0, 'tokens': 0}
        return self.day

    def set(self, scope, amount):
        if scope not in SCOPES:
            raise ValueError('budget: task | chat | day <лимит> · resume')
        kind, value = parse_amount(amount)
        if kind is None:
            self.limits.pop(scope, None)
        else:
            self.limits.setdefault(scope, {})[kind] = value
        self.save()

    def add(self, usd, tokens):
        day = self._today()
        day['usd'] = round(day['usd'] + float(usd or 0), 6)
        day['tokens'] = day['tokens'] + int(tokens or 0)
        self.save()

    def spent(self, chat, live_tokens=0):
        run = chat.run_usage or {}
        return {
            'task': {'usd': run.get('cost_usd', 0.0), 'tokens': max(tokens_of(run), live_tokens)},
            'chat': {'usd': chat.usage.get('cost_usd', 0.0), 'tokens': tokens_of(chat.usage) + max(0, live_tokens - tokens_of(run))},
            'day': {'usd': self._today()['usd'], 'tokens': self._today()['tokens'] + max(0, live_tokens - tokens_of(run))},
        }

    def exceeded(self, chat, live_tokens=0, scopes=SCOPES):
        """(scope, kind, spent, limit) of the first limit reached, or None."""
        if getattr(chat, 'budget_override', False):
            return None
        spent = self.spent(chat, live_tokens)
        for scope in scopes:
            for kind, limit in (self.limits.get(scope) or {}).items():
                if spent[scope][kind] >= limit:
                    return scope, kind, spent[scope][kind], limit
        return None

    def report(self, chat):
        return {'limits': self.limits, 'spent': self.spent(chat), 'paused': bool(getattr(chat, 'paused', False))}
