"""Trusted folders, like Claude Code's "Do you trust the files in this folder?".

A folder's own .claude settings (allow rules, hooks, settings.local.json) and CLAUDE.md
are loaded only when the folder is trusted: otherwise a cloned repository could ship
"allow": ["Bash"] or a hook and run commands without a card in the game.

Trusted: the bridge workspace, folders the player created or picked as a project, and
folders marked with /cc perms trust. Not trusted: repositories from /cc git clone until
the player trusts them. Deny and ask rules of an untrusted folder still apply.
trust.json is not part of /claude export.
"""
import json
from pathlib import Path

from backends.util import save_json


class Trust:
    def __init__(self, directory, workspace, projects=None):
        self.file = Path(directory) / 'trust.json'
        self.workspace = Path(workspace)
        if self.file.exists():
            try:
                self.folders = list(json.loads(self.file.read_text(encoding='utf-8')).get('folders', []))
            except (OSError, ValueError):
                self.folders = []
        else:
            # first run with trust: projects the player already had were added by hand
            self.folders = [item['path'] for item in (projects or {}).values() if item.get('path')]
            self.save()

    def save(self):
        save_json(self.file, {'folders': self.folders})

    @staticmethod
    def _norm(folder):
        try:
            return Path(folder).expanduser().resolve()
        except OSError:
            return Path(folder)

    def trusted(self, folder):
        target = self._norm(folder)
        for base in [self.workspace] + [Path(f) for f in self.folders]:
            base = self._norm(base)
            if target == base or base in target.parents:
                return True
        return False

    def add(self, folder):
        path = str(self._norm(folder))
        if path not in self.folders:
            self.folders.append(path)
            self.save()

    def remove(self, folder):
        path = str(self._norm(folder))
        if path in self.folders:
            self.folders.remove(path)
            self.save()
