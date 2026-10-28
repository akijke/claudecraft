"""Diffs for Claude's file edits and undo of whole requests.

Before an Edit/Write/MultiEdit/NotebookEdit runs (the permission callback sees every
tool call, auto-approved or not), the bridge saves the file as it was. One queue item
of a chat = one checkpoint; /cc undo restores the files of the newest checkpoint
(files Claude created are removed). Shell commands (Bash) are not tracked.
Checkpoints live in config/claude-chat/undo/<chat>.json, the last MAX_TURNS per chat.
"""
import base64
import difflib
import json
import os
import time
from pathlib import Path

FILE_TOOLS = {'Write', 'Edit', 'MultiEdit', 'NotebookEdit'}
MAX_TURNS = 20
MAX_FILE = 2 * 1024 * 1024
DIFF_LINES = 400


def target(name, data, cwd):
    """Absolute path a file tool writes to, or None."""
    if name not in FILE_TOOLS or not isinstance(data, dict):
        return None
    raw = data.get('file_path') or data.get('notebook_path')
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path(cwd) / path
    return Path(os.path.normpath(path))


def _read_text(path):
    try:
        return path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None


def proposed(name, data, path):
    """(before, after) text of the file if the tool call succeeds; None when unknown."""
    before = _read_text(path) if path.is_file() else ''
    if before is None:
        return None
    if name == 'Write':
        return before, str(data.get('content', ''))
    if name == 'Edit':
        edits = [data]
    elif name == 'MultiEdit':
        edits = data.get('edits') or []
    else:
        return None  # notebooks: JSON cells, not worth a text diff
    after = before
    for edit in edits:
        old, new = str(edit.get('old_string', '')), str(edit.get('new_string', ''))
        if old and old not in after:
            return None
        after = after.replace(old, new) if edit.get('replace_all') else after.replace(old, new, 1)
    return before, after


def diff(name, data, path, context=2):
    """Unified diff lines (without the ---/+++ header) and +/- counts."""
    pair = proposed(name, data, path) if path else None
    if pair is None:
        return None
    before, after = pair
    lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm='', n=context))[2:]
    added = sum(1 for l in lines if l.startswith('+'))
    removed = sum(1 for l in lines if l.startswith('-'))
    more = max(0, len(lines) - DIFF_LINES)
    return {'lines': lines[:DIFF_LINES], 'added': added, 'removed': removed, 'more': more,
            'new': not path.exists()}


class Checkpoints:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.current = {}  # chat id -> checkpoint being filled by the running request

    def _file(self, chat_id):
        return self.folder / f'{chat_id}.json'

    def load(self, chat_id):
        try:
            return json.loads(self._file(chat_id).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return []

    def _save(self, chat_id, turns):
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self._file(chat_id).with_suffix('.tmp')
        tmp.write_text(json.dumps(turns[-MAX_TURNS:], ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, self._file(chat_id))

    def begin(self, chat_id, prompt):
        self.current[chat_id] = {'time': time.time(), 'prompt': prompt[:200], 'files': {}}

    def remember(self, chat_id, path):
        """Saves the file before its first change in the running request."""
        turn = self.current.get(chat_id)
        if turn is None or str(path) in turn['files']:
            return
        entry = {'existed': path.is_file()}
        if entry['existed']:
            try:
                if path.stat().st_size > MAX_FILE:
                    entry['skipped'] = 'too large'
                else:
                    entry['data'] = base64.b64encode(path.read_bytes()).decode('ascii')
            except OSError as e:
                entry['skipped'] = str(e)
        turn['files'][str(path)] = entry
        turns = self.load(chat_id)
        if turns and turns[-1].get('open'):
            turns[-1] = {**turn, 'open': True}
        else:
            turns.append({**turn, 'open': True})
        self._save(chat_id, turns)  # on disk at once: a crash mid-request still can be undone

    def finish(self, chat_id):
        turn = self.current.pop(chat_id, None)
        if not turn or not turn['files']:
            return
        turns = self.load(chat_id)
        if turns and turns[-1].get('open'):
            turns[-1].pop('open', None)
            self._save(chat_id, turns)

    def history(self, chat_id):
        return [{'time': t['time'], 'prompt': t['prompt'], 'files': list(t['files'])} for t in self.load(chat_id)]

    def undo(self, chat_id):
        """Restores the newest checkpoint. Returns (restored, removed, skipped) path lists."""
        if chat_id in self.current and self.current[chat_id]['files']:
            raise ValueError('Claude ещё работает над этим запросом — дождись конца или /cc cancel')
        turns = self.load(chat_id)
        if not turns:
            raise ValueError('Нечего откатывать: Claude ещё не менял файлы в этом чате')
        turn = turns.pop()
        restored, removed, skipped = [], [], []
        for name, entry in turn['files'].items():
            path = Path(name)
            try:
                if entry.get('skipped'):
                    skipped.append(name)
                elif entry['existed']:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(base64.b64decode(entry['data']))
                    restored.append(name)
                elif path.is_file():
                    path.unlink()
                    removed.append(name)
            except OSError:
                skipped.append(name)
        self._save(chat_id, turns)
        return restored, removed, skipped, turn['prompt']
