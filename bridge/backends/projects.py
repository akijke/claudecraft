"""Projects: named working folders for Claude Code.

A project is a folder Claude works in. Every /cc chat belongs to one project and
its Claude session lives in that folder (Claude Code stores sessions per folder).
Switching project = switching to that project's chats, so a session never moves
between folders. The built-in project "workspace" is the configured workspace.
"""
import json
import os
from pathlib import Path
import re
import time
from backends.util import save_json

NAME = re.compile(r'[\w.\-]{1,40}', re.UNICODE)
DEFAULT = 'workspace'


class Projects:
    def __init__(self, bridge):
        self.bridge = bridge
        self.file = bridge.directory / 'projects.json'
        self.items = {}
        self.current = DEFAULT
        if self.file.exists():
            data = json.loads(self.file.read_text(encoding='utf-8'))
            self.items = data.get('projects', {})
            self.current = data.get('current', DEFAULT)
        self.items[DEFAULT] = {'path': str(bridge.workspace), 'builtin': True,
                               **{k: v for k, v in self.items.get(DEFAULT, {}).items() if k not in ('path', 'builtin')}}
        if self.current not in self.items:
            self.current = DEFAULT

    def save(self):
        save_json(self.file, {'projects': self.items, 'current': self.current})

    def path_of(self, name):
        return self.items.get(name or DEFAULT, self.items[DEFAULT])['path']

    def path_of_current(self):
        return self.path_of(self.current)

    def find_by_path(self, folder):
        folder = Path(folder).resolve()
        for name, item in self.items.items():
            try:
                if Path(item['path']).resolve() == folder:
                    return name
            except OSError:
                continue
        return None

    def check_name(self, name):
        if not name or not NAME.fullmatch(name):
            raise ValueError('Имя проекта: 1–40 символов, буквы, цифры, . _ -')
        return name

    def add(self, name, path, trusted=True):
        self.check_name(name)
        folder = Path(path).expanduser().resolve()
        if not folder.is_dir():
            raise ValueError(f'Нет такой папки: {folder}')
        if name in self.items:
            raise ValueError(f'Проект {name} уже есть')
        existing = self.find_by_path(folder)
        if existing:
            raise ValueError(f'Эта папка уже проект «{existing}»')
        self.items[name] = {'path': str(folder), 'created': int(time.time())}
        self.save()
        trust = getattr(self.bridge, 'trust', None)
        if trusted and trust is not None:
            trust.add(folder)  # the player chose this folder: like accepting Claude Code's trust question
        return name

    def remove(self, name):
        if name == DEFAULT or name not in self.items:
            raise ValueError('Этот проект нельзя убрать' if name == DEFAULT else f'Нет проекта {name}')
        del self.items[name]
        if self.current == name:
            self.current = DEFAULT
        self.save()

    def rename(self, old, new):
        if old == DEFAULT or old not in self.items:
            raise ValueError(f'Нет проекта {old}' if old not in self.items else 'workspace не переименовывается')
        self.check_name(new)
        if new in self.items:
            raise ValueError(f'Проект {new} уже есть')
        self.items[new] = self.items.pop(old)
        if self.current == old:
            self.current = new
        self.save()

    def touch(self, name):
        if name in self.items:
            self.items[name]['opened'] = int(time.time())
            self.save()

    def rows(self, chats):
        rows = []
        for name, item in sorted(self.items.items(), key=lambda kv: (kv[0] != DEFAULT, -kv[1].get('opened', 0))):
            folder = Path(item['path'])
            own = [c for c in chats.values() if c.mode == 'cc' and (c.project or DEFAULT) == name]
            rows.append({'name': name, 'path': item['path'], 'exists': folder.is_dir(),
                         'git': (folder / '.git').exists(), 'chats': len(own),
                         'running': sum(1 for c in own if c.task), 'current': name == self.current})
        return rows
