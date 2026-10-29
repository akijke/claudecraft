"""Gemini-specific behaviour on top of Claudecraft's queue/workspace/HTTP core."""
from core import Bridge, ALIASES
from gemini_backend import run

# Commands tied to Claude Code's own features (sessions on disk, plan mode, skills, hooks…)
CLAUDE_ONLY = ('local', 'sync', 'terminal', 'agents', 'agent', 'plan', 'compact', 'rewind', 'fork', 'skills',
               'hooks', 'memory', 'mem', 'style', 'export', 'search', 'open_session')


class GeminiBridge(Bridge):
    async def run_backend(self, chat, text):
        await run(self, chat, text)

    def new_chat(self, mode, backend, project=None, select=True):
        if backend != 'local':
            raise ValueError('Geminicraft использует Gemini CLI. Создай чат: new local.')
        return super().new_chat(mode, backend, project, select)

    async def sync_sessions(self, name=None):
        return 0

    async def command(self, data):
        action = ALIASES.get(data.get('action'), data.get('action'))
        mode = data.get('mode')
        if mode not in ('c', 'cc'):
            raise ValueError('Invalid mode')
        if action in CLAUDE_ONLY:
            raise ValueError('Эта команда относится к Claude Code. Для Gemini есть: new, chats, spawn, tasks, undo, '
                             'img, shot, git, files, pty, watch, crossreview; инструкции — в GEMINI.md проекта.')
        if action == 'model':
            from backends.util import save_json
            value = (data.get('text') or '').strip()
            if value:
                if len(value) > 120 or any(c.isspace() for c in value):
                    raise ValueError('model <gemini-модель> | model default')
                if value == 'default':
                    self.config.pop('gemini_model', None)
                else:
                    self.config['gemini_model'] = value
                save_json(self.directory / 'bridge-config.json', self.config)
            self.emit(self.current_chat(mode), 'Gemini model: ' + self.config.get('gemini_model', 'CLI default'),
                      kind='status')
            return {'ok': True}
        if action == 'apikey':
            self.account.set_api_key(data.get('text', ''))
            self.emit(self.current_chat(mode), 'API-ключ Gemini сохранён', kind='status')
            return {'ok': True}
        return await super().command(data)

    def tokens_report(self, chat):
        self.emit(chat, 'Gemini CLI не сообщает токены через ACP — смотри квоты на aistudio.google.com', kind='status')
