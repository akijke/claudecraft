"""Codex-specific behavior on top of Claudecraft's queue/workspace/HTTP core."""
from core import Bridge, ALIASES
from codex_backend import run


class CodexBridge(Bridge):
    async def run_backend(self, chat, text):
        await run(self, chat, text)

    def new_chat(self, mode, backend, project=None, select=True):
        if backend != 'local':
            raise ValueError('Codexcraft использует Codex CLI. Создай чат: new local.')
        return super().new_chat(mode, backend, project, select)

    async def sync_sessions(self, name=None):
        # Claude session IDs and Codex thread IDs are unrelated.
        return 0

    async def command(self, data):
        action = ALIASES.get(data.get('action'), data.get('action'))
        mode = data.get('mode')
        if mode not in ('c', 'cc'):
            raise ValueError('Invalid mode')
        if action in ('local', 'sync', 'terminal', 'agents', 'agent'):
            raise ValueError('Эта команда относится к Claude. Для Codex используй new, chats, spawn и tasks; '
                             'настройки агентов — в AGENTS.md проекта.')
        if action == 'spawn' and data.get('text', '').startswith('@'):
            raise ValueError('Именованные агенты Claude не поддерживаются. Используй spawn <задача>.')
        if action == 'crash':
            from backends.sdk import read_game_logs
            await self.enqueue(self.current_chat(mode),
                'Investigate this Minecraft log and suggest a fix:\n' + read_game_logs(self.game_dir, 'latest', 300))
            return {'ok': True}
        if action == 'model':
            from backends.util import save_json
            value = data.get('text', '').strip()
            if value:
                if len(value) > 160 or any(c.isspace() for c in value):
                    raise ValueError('model <идентификатор модели> | model default')
                if value == 'default':
                    self.config.pop('codex_model', None)
                else:
                    self.config['codex_model'] = value
                save_json(self.directory / 'bridge-config.json', self.config)
            self.emit(self.current_chat(mode), 'Codex model: ' + self.config.get('codex_model', 'CLI default'), kind='status')
            return {'ok': True}
        if action == 'auto':
            value = data.get('text', '').strip()
            chat = self.current_chat(mode)
            if value:
                if value not in ('off', 'edits', 'all'):
                    raise ValueError('auto: off | edits | all')
                chat.auto = value
                self.persist()
            notes = {
                'off': 'Codex: read-only sandbox; выход за ограничения запрашивает разрешение.',
                'edits': 'Codex: запись в папку проекта; дополнительные разрешения запрашиваются.',
                'all': 'Codex: запись в папку проекта; запросы команд и файлов подтверждаются автоматически. '
                       'Ограничения sandbox продолжают действовать.'}
            note = 'GPT: read-only; запросы повышения доступа отклоняются.' if mode == 'c' else notes[chat.auto]
            self.emit(chat, note + ' Настройка применяется со следующего запроса.', kind='auto',
                      mode_value=chat.auto, allowed=[])
            return {'ok': True}
        return await super().command(data)

    def tokens_report(self, chat):
        self.emit(chat, f"Codex: input {chat.usage.get('input_tokens', 0)}, "
                       f"output {chat.usage.get('output_tokens', 0)}. Стоимость не рассчитывается.", kind='status')
