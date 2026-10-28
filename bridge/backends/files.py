"""File manager for the Minecraft chat: browse the disk without moving Claude.

Each mode (/c, /cc) has its own browsing folder (`fm_cwd`). Claude's working
folder is the folder of the current *project* and is changed only with
`project open`. From here a folder can become a project in one click.
"""
import fnmatch
import os
from pathlib import Path
import shutil
import subprocess
import sys

MAX_LIST = 200
MAX_LINES = 150


class FileManager:
    def __init__(self, bridge):
        self.bridge = bridge
        self.cwd = {}  # mode -> str

    # ---- paths ---------------------------------------------------------
    def here(self, mode):
        folder = Path(self.cwd.get(mode) or self.bridge.projects.path_of_current())
        return folder if folder.is_dir() else self.bridge.workspace

    def resolve(self, mode, arg):
        """`~` = workspace, `@` = current project, relative = from the browsing folder."""
        arg = (arg or '').strip().strip('"')
        base = self.here(mode)
        if not arg or arg == '.':
            return base
        if arg == '~' or arg.startswith(('~/', '~\\')):
            return (self.bridge.workspace / arg[2:]).resolve()
        if arg == '@' or arg.startswith(('@/', '@\\')):
            return (Path(self.bridge.projects.path_of_current()) / arg[2:]).resolve()
        path = Path(os.path.expandvars(arg))
        return (path if path.is_absolute() else base / path).resolve()

    def emit(self, mode, text, **extra):
        self.bridge.emit(self.bridge.current_chat(mode), text, **extra)

    # ---- data for the file window (no chat output) ---------------------------
    def snapshot(self, mode, arg=''):
        """Folder listing for the file manager window; also becomes the browsing folder."""
        folder = self.resolve(mode, arg)
        if not folder.is_dir():
            raise ValueError(f'Нет такой папки: {folder}')
        try:
            entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            raise ValueError(f'Нет доступа: {folder}') from None
        rows = []
        for entry in entries[:2000]:
            try:
                is_dir = entry.is_dir()
                stat = entry.stat()
            except OSError:
                continue
            rows.append({'name': entry.name, 'dir': is_dir, 'size': 0 if is_dir else stat.st_size,
                         'mtime': int(stat.st_mtime), 'hidden': entry.name.startswith('.')})
        self.cwd[mode] = str(folder)
        roots = [str(Path(d)) for d in _drives()]
        return {'path': str(folder), 'parent': str(folder.parent) if folder.parent != folder else None,
                'rows': rows, 'more': max(0, len(entries) - 2000),
                'project': self.bridge.projects.find_by_path(folder), 'git': (folder / '.git').exists(),
                'claude': self.bridge.projects.path_of_current(), 'current_project': self.bridge.projects.current,
                'workspace': str(self.bridge.workspace), 'home': str(Path.home()), 'roots': roots}

    def grep(self, mode, arg, query, case=False, regex=False, limit=300):
        """Search file contents under a folder (the file window's "in contents" search)."""
        import re
        root = self.resolve(mode, arg)
        if not root.is_dir():
            raise ValueError(f'Нет такой папки: {root}')
        query = (query or '').strip()
        if not query:
            raise ValueError('Что искать?')
        flags = 0 if case else re.IGNORECASE
        try:
            pattern = re.compile(query if regex else re.escape(query), flags)
        except re.error as e:
            raise ValueError(f'Неверное регулярное выражение: {e}') from None
        skip = {'.git', 'node_modules', '.gradle', 'build', 'dist', 'target', '__pycache__', '.venv', 'venv', '.idea'}
        found, files, truncated = [], 0, False
        for folder, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in skip]
            for name in names:
                path = Path(folder) / name
                try:
                    if path.stat().st_size > 2_000_000:
                        continue
                    raw = path.read_bytes()
                except OSError:
                    continue
                if b'\0' in raw[:4000]:
                    continue
                files += 1
                for number, line in enumerate(raw.decode('utf-8', 'replace').splitlines(), 1):
                    m = pattern.search(line)
                    if m:
                        found.append({'path': str(path.relative_to(root)).replace('\\', '/'), 'line': number,
                                      'col': m.start(), 'text': line.strip()[:200]})
                        if len(found) >= limit:
                            truncated = True
                            break
                if truncated:
                    break
            if truncated:
                break
        return {'root': str(root), 'query': query, 'matches': found, 'files': files, 'truncated': truncated}

    def read(self, mode, arg, start=1, count=400):
        """Text of a file for the preview pane."""
        path = self.resolve(mode, arg)
        if not path.is_file():
            raise ValueError(f'Нет такого файла: {path}')
        size = path.stat().st_size
        with path.open('rb') as f:
            raw = f.read(4_000_000)
        if b'\0' in raw[:8000]:
            return {'path': str(path), 'binary': True, 'size': size, 'lines': [], 'total': 0, 'start': 1}
        lines = raw.decode('utf-8', 'replace').splitlines()
        start = max(1, start)
        return {'path': str(path), 'binary': False, 'size': size, 'start': start, 'total': len(lines),
                'lines': [line[:400] for line in lines[start - 1:start - 1 + min(count, 2000)]]}

    # ---- browsing ------------------------------------------------------
    def ls(self, mode, arg=''):
        folder = self.resolve(mode, arg)
        if not folder.is_dir():
            raise ValueError(f'Нет такой папки: {folder}')
        try:
            entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            raise ValueError(f'Нет доступа: {folder}') from None
        rows = []
        for entry in entries[:MAX_LIST]:
            try:
                is_dir = entry.is_dir()
                size = 0 if is_dir else entry.stat().st_size
            except OSError:
                continue
            rows.append({'name': entry.name, 'dir': is_dir, 'size': size})
        project = self.bridge.projects.find_by_path(folder)
        self.emit(mode, str(folder), kind='listing', path=str(folder), rows=rows,
                  more=max(0, len(entries) - MAX_LIST), project=project,
                  parent=str(folder.parent) if folder.parent != folder else None,
                  git=(folder / '.git').exists())

    def cd(self, mode, arg=''):
        folder = self.resolve(mode, arg or '@')
        if not folder.is_dir():
            raise ValueError(f'Нет такой папки: {folder}')
        self.cwd[mode] = str(folder)
        self.bridge.persist()
        self.ls(mode, '')

    def pwd(self, mode):
        self.emit(mode, str(self.here(mode)), kind='cwd',
                  claude=self.bridge.projects.path_of_current(),
                  project=self.bridge.projects.current)

    def cat(self, mode, arg):
        parts = (arg or '').rsplit(' ', 1)
        start = 1
        if len(parts) == 2 and '-' in parts[1] and parts[1].replace('-', '').isdigit():
            arg = parts[0]
            a, _, b = parts[1].partition('-')
            start = max(1, int(a or 1))
            stop = int(b) if b else start + MAX_LINES - 1
        else:
            stop = MAX_LINES
        if not (arg or '').strip():
            raise ValueError('Формат: cat <файл> [начало-конец]')
        path = self.resolve(mode, arg)
        if not path.is_file():
            raise ValueError(f'Нет такого файла: {path}')
        with path.open('rb') as f:
            raw = f.read(2_000_000)
        if b'\0' in raw[:4000]:
            raise ValueError('Это двоичный файл — не показываю')
        lines = raw.decode('utf-8', 'replace').splitlines()
        stop = min(stop, start + 299, len(lines))
        chunk = lines[start - 1:stop]
        self.emit(mode, '\n'.join(chunk), kind='file', path=str(path), start=start, total=len(lines),
                  truncated=stop < len(lines))

    def tree(self, mode, arg=''):
        parts = (arg or '').split()
        depth = 2
        if parts and parts[-1].isdigit():
            depth = min(4, int(parts.pop()))
        root = self.resolve(mode, ' '.join(parts))
        if not root.is_dir():
            raise ValueError(f'Нет такой папки: {root}')
        rows, skipped = [], 0
        skip = {'.git', 'node_modules', '.gradle', 'build', '__pycache__', '.venv', 'venv', '.idea', 'target', 'dist'}

        def walk(folder, level):
            nonlocal skipped
            try:
                entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except OSError:
                return
            for entry in entries:
                if len(rows) >= 250:
                    skipped += 1
                    continue
                is_dir = entry.is_dir()
                rows.append({'name': entry.name, 'dir': is_dir, 'level': level,
                             'path': str(entry.relative_to(root)).replace('\\', '/')})
                if is_dir and level < depth and entry.name not in skip:
                    walk(entry, level + 1)

        walk(root, 1)
        self.emit(mode, str(root), kind='tree', path=str(root), rows=rows, more=skipped)

    def find(self, mode, arg):
        pattern = (arg or '').strip()
        if not pattern:
            raise ValueError('Формат: find <шаблон>, например find *.java или find Main')
        if not any(c in pattern for c in '*?['):
            pattern = f'*{pattern}*'
        root = self.here(mode)
        found = []
        skip = {'.git', 'node_modules', '.gradle', '__pycache__', '.venv', 'venv'}
        for folder, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in skip]
            for name in dirs + files:
                if fnmatch.fnmatch(name.lower(), pattern.lower()):
                    full = Path(folder) / name
                    found.append({'path': str(full.relative_to(root)).replace('\\', '/'),
                                  'dir': full.is_dir()})
                    if len(found) >= 100:
                        break
            if len(found) >= 100:
                break
        self.emit(mode, pattern, kind='found', root=str(root), rows=found)

    # ---- changes (all reversible except rm, which asks first) ----------
    def mkdir(self, mode, arg):
        path = self.resolve(mode, arg)
        path.mkdir(parents=True, exist_ok=True)
        self.emit(mode, f'Папка создана: {path}', kind='status')

    def touch(self, mode, arg):
        path = self.resolve(mode, arg)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        self.emit(mode, f'Файл создан: {path}', kind='status')

    def mv(self, mode, arg, copy=False):
        source, target = split_two(arg)
        a, b = self.resolve(mode, source), self.resolve(mode, target)
        if not a.exists():
            raise ValueError(f'Нет такого пути: {a}')
        if b.is_dir():
            b = b / a.name
        if b.exists():
            raise ValueError(f'Уже существует: {b}')
        if copy:
            (shutil.copytree if a.is_dir() else shutil.copy2)(a, b)
        else:
            shutil.move(str(a), str(b))
        self.emit(mode, f'{"Скопировано" if copy else "Перемещено"}: {a} → {b}', kind='status')

    def rm(self, mode, arg):
        confirmed = arg.startswith('--yes ')
        path = self.resolve(mode, arg[6:] if confirmed else arg)
        if not path.exists():
            raise ValueError(f'Нет такого пути: {path}')
        if _protected(path, self.bridge.workspace):
            raise ValueError(f'Слишком опасно удалять {path}')
        if not confirmed:
            count = sum(1 for _ in path.rglob('*')) if path.is_dir() else 1
            self.emit(mode, f'Удалить {path}?' + (f' (внутри {count} объектов)' if path.is_dir() else ''),
                      kind='confirm', command=f'rm --yes {path}', danger=True)
            return
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        self.emit(mode, f'Удалено: {path}', kind='status')

    def open(self, mode, arg, editor=False):
        """Open in the OS file manager / default app, or in VS Code / IDEA with `code`."""
        path = self.resolve(mode, arg)
        if not path.exists():
            raise ValueError(f'Нет такого пути: {path}')
        if editor:
            for tool in ('code', 'cursor', 'idea64', 'idea', 'subl'):
                exe = shutil.which(tool)
                if exe:
                    subprocess.Popen([exe, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     shell=exe.lower().endswith(('.cmd', '.bat')))
                    self.emit(mode, f'Открыто в {tool}: {path}', kind='status')
                    return
            raise ValueError('Не найден редактор (code, cursor, idea, subl) в PATH')
        if sys.platform == 'win32':
            os.startfile(str(path))  # noqa: S606 - user's own explicit request
        else:
            subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(path)])
        self.emit(mode, f'Открыто: {path}', kind='status')


def _protected(path, workspace):
    """Folders rm refuses: disk roots, home and its parent, the OS and program folders, the workspace.
    Ordinary folders anywhere (also right under C:\\) can be deleted."""
    path = Path(path).resolve()
    home = Path.home().resolve()
    danger = {Path(path.anchor), home, home.parent, Path(workspace).resolve()}
    for var in ('SystemRoot', 'ProgramFiles', 'ProgramFiles(x86)', 'ProgramData', 'APPDATA', 'LOCALAPPDATA'):
        if os.environ.get(var):
            danger.add(Path(os.environ[var]).resolve())
    if os.name != 'nt':
        danger |= {Path(p) for p in ('/bin', '/boot', '/dev', '/etc', '/home', '/lib', '/lib64', '/opt',
                                     '/proc', '/root', '/sbin', '/sys', '/usr', '/var', '/Users', '/System',
                                     '/Applications', '/Library')}
    return path in danger


def _drives():
    """Disk roots for quick jumps (C:\\, D:\\ … on Windows; / elsewhere)."""
    if os.name != 'nt':
        return ['/']
    import string
    return [f'{letter}:\\' for letter in string.ascii_uppercase if os.path.exists(f'{letter}:\\')]


def split_two(arg):
    """'a b' or '"a b" c' → (a, b)."""
    arg = (arg or '').strip()
    if arg.startswith('"'):
        end = arg.find('"', 1)
        first, rest = arg[1:end], arg[end + 1:].strip()
    else:
        first, _, rest = arg.partition(' ')
    rest = rest.strip().strip('"')
    if not first or not rest:
        raise ValueError('Нужны два пути: откуда и куда')
    return first, rest
