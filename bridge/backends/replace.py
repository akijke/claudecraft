"""Find & replace across a folder: preview first, then apply exactly what was previewed.

preview() returns every changed line (before → after) and a token: a hash of each
affected file's content. apply() refuses when a file changed since the preview,
keeps line endings and encoding (UTF-8), writes atomically and records the old
files as an undo checkpoint (/cc undo restores them).
"""
import hashlib
import os
import re
from pathlib import Path

SKIP = {'.git', 'node_modules', '.gradle', 'build', 'dist', 'target', '__pycache__', '.venv', 'venv', '.idea'}
MAX_FILE = 2_000_000


def _pattern(query, regex, case):
    if not query:
        raise ValueError('Что искать?')
    try:
        return re.compile(query if regex else re.escape(query), re.MULTILINE | (0 if case else re.IGNORECASE))
    except re.error as e:
        raise ValueError(f'Неверное регулярное выражение: {e}') from None


def _files(root, glob):
    root = Path(root)
    if root.is_file():
        yield root
        return
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for name in names:
            path = Path(folder) / name
            if glob and not Path(name).match(glob) and not path.match(glob):
                continue
            yield path


def _text(path):
    try:
        if path.stat().st_size > MAX_FILE:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b'\0' in raw[:4000]:
        return None
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return None  # only UTF-8 files are changed (others could be damaged)


def _expand(pattern, regex, replacement, match):
    return match.expand(replacement) if regex else replacement


def preview(root, query, replacement, regex=False, case=False, glob=None, limit=500):
    pattern = _pattern(query, regex, case)
    if regex:
        try:
            pattern.sub(replacement, '')  # validates \1 references early
        except (re.error, IndexError) as e:
            raise ValueError(f'Неверная замена: {e}') from None
    changes, files, total = [], {}, 0
    root = Path(root)
    for path in _files(root, glob):
        text = _text(path)
        if text is None or not pattern.search(text):
            continue
        count = 0
        for number, line in enumerate(text.split('\n'), 1):
            new, n = pattern.subn(lambda m: _expand(pattern, regex, replacement, m), line)
            if n:
                count += n
                if len(changes) < limit:
                    rel = path.relative_to(root) if root.is_dir() else Path(path.name)
                    changes.append({'path': str(rel).replace('\\', '/'), 'line': number,
                                    'before': line.strip()[:200], 'after': new.strip()[:200]})
        total += count
        files[str(path)] = hashlib.sha256(text.encode('utf-8')).hexdigest()
    return {'root': str(root), 'query': query, 'replacement': replacement, 'regex': regex, 'case': case,
            'glob': glob, 'changes': changes, 'count': total, 'files': len(files),
            'truncated': len(changes) >= limit, 'token': hashlib.sha256(repr(sorted(files.items())).encode()).hexdigest()[:16],
            'hashes': files}


def apply(root, query, replacement, regex, case, glob, token, remember=None):
    """Applies the previewed replacement. `remember(path)` is called before each write."""
    fresh = preview(root, query, replacement, regex, case, glob, limit=1)
    if fresh['token'] != token:
        raise ValueError('Файлы изменились после превью — сделай превью заново')
    pattern = _pattern(query, regex, case)
    changed = []
    for name in fresh['hashes']:
        path = Path(name)
        text = _text(path)
        new = pattern.sub(lambda m: _expand(pattern, regex, replacement, m), text)
        if new == text:
            continue
        if remember:
            remember(path)
        tmp = path.with_name(path.name + '.claudecraft-tmp')
        with open(tmp, 'w', encoding='utf-8', newline='') as f:
            f.write(new)
        os.replace(tmp, path)
        changed.append(str(path))
    return changed
