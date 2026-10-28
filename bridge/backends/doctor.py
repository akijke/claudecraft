"""/cc doctor: one report about everything the mod depends on, with advice.

Each row: name, status (ok | warn | fail | info), what was found, and a hint when
something is wrong. Version checks run in parallel with short timeouts; nothing
here changes the system and no secrets are printed.
"""
import asyncio
import os
import platform
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path


def _row(name, status, value, hint=''):
    return {'name': name, 'status': status, 'value': str(value)[:200], 'hint': hint}


async def _version(executable, *args, timeout=15):
    """First output line of `executable args`, or None when it fails."""
    if not executable:
        return None
    command = [executable, *args]
    if os.name == 'nt' and executable.lower().endswith(('.cmd', '.bat')):
        command = ['cmd', '/c', *command]
    try:
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.STDOUT,
                                                       stdin=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(process.communicate(), timeout)
    except (OSError, asyncio.TimeoutError):
        return None
    text = out.decode('utf-8', 'replace').strip()
    return (text.splitlines() or [''])[0] if process.returncode == 0 else None


def _port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(('127.0.0.1', port)) == 0


def _admin():
    try:
        if os.name == 'nt':
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return os.geteuid() == 0
    except Exception:
        return False


def _writable(folder):
    try:
        with tempfile.NamedTemporaryFile(dir=folder, prefix='.claudecraft-doctor-', delete=True):
            return True
    except OSError:
        return False


async def report(bridge):
    rows = []
    py = sys.version_info
    rows.append(_row('Python', 'ok' if py >= (3, 11) else 'fail', f'{platform.python_version()} · {sys.executable}',
                     '' if py >= (3, 11) else 'Нужен Python 3.11+'))
    rows.append(_row('OS', 'info', f'{platform.system()} {platform.release()} · {platform.machine()}'))
    try:
        import claude_agent_sdk
        sdk_dir = Path(claude_agent_sdk.__file__).parent / '_bundled'
        bundled = next((str(p) for p in sdk_dir.glob('claude*') if p.is_file()), None)
        rows.append(_row('claude-agent-sdk', 'ok', claude_agent_sdk.__version__))
    except Exception as e:
        bundled = None
        rows.append(_row('claude-agent-sdk', 'fail', e, 'pip install -r requirements.txt (мост ставит сам при запуске)'))
    try:
        import aiohttp
        rows.append(_row('aiohttp', 'ok', aiohttp.__version__))
    except Exception as e:
        rows.append(_row('aiohttp', 'fail', e, 'pip install aiohttp'))

    cli = bridge.config.get('cli_path') or bundled
    tools = {
        'Claude Code CLI': (cli, ['--version'], 'Встроен в SDK; если пусто — переустанови зависимости моста'),
        'git': (shutil.which('git'), ['--version'], 'Установи git: git-scm.com — нужен для /cc git и worktrees'),
        'gh (GitHub CLI)': (shutil.which('gh'), ['--version'], 'cli.github.com — для PR, issues и входа в GitHub'),
        'node': (shutil.which('node'), ['--version'], 'nodejs.org — нужен коннекторам через npx и Codex CLI'),
        'npx': (shutil.which('npx'), ['--version'], 'Ставится вместе с Node.js'),
        'codex CLI': (bridge.config.get('codex_path') or shutil.which('codex'), ['--version'],
                      'Нужен только для Codexcraft: кнопка «Установить Codex CLI» в его настройках'),
        'docker': (shutil.which('docker'), ['--version'], 'Необязательно: только для работы в контейнере'),
    }
    names = list(tools)
    versions = await asyncio.gather(*(_version(tools[n][0], *tools[n][1]) for n in names))
    optional = {'codex CLI', 'docker', 'gh (GitHub CLI)'}
    for name, version in zip(names, versions):
        path, _, hint = tools[name]
        if version:
            rows.append(_row(name, 'ok', version))
        else:
            rows.append(_row(name, 'warn' if name in optional or name == 'npx' else 'fail',
                             'не найден' if not path else f'не запускается: {path}', hint))

    if shutil.which('gh'):
        auth = await _version(shutil.which('gh'), 'auth', 'status', timeout=15)
        rows.append(_row('gh auth', 'ok' if auth else 'warn', 'вход выполнен' if auth else 'нет входа',
                         '' if auth else 'Войди в GitHub: /claude → GitHub или gh auth login'))
    try:
        account = (await bridge.account.status()).get('account', {})
        ok = account.get('logged_in')
        rows.append(_row('Аккаунт Claude', 'ok' if ok else 'warn',
                         (account.get('method') or '') + ' ' + (account.get('subscription') or '') if ok else 'не выполнен вход',
                         '' if ok else 'Войди: /claude (настройки) → «Войти», либо задай API-ключ'))
    except Exception as e:
        rows.append(_row('Аккаунт Claude', 'warn', e))

    rows.append(_row('Порт моста', 'ok', bridge.port))
    other = 18742 if bridge.port == 18741 else 18741
    rows.append(_row('Соседний мост', 'info', f'{other}: ' + ('занят (работает второй мод)' if _port_in_use(other) else 'свободен')))
    for label, folder in (('Workspace', bridge.workspace), ('Папка моста', bridge.directory)):
        ok = _writable(folder)
        rows.append(_row(label, 'ok' if ok else 'fail', folder, '' if ok else 'Нет прав на запись в эту папку'))
    try:
        project = Path(bridge.projects.path_of_current())
        ok = project.is_dir() and _writable(project)
        rows.append(_row('Проект', 'ok' if ok else 'warn', f'{bridge.projects.current} · {project}',
                         '' if ok else 'Папка проекта недоступна для записи — Claude не сможет править файлы'))
    except Exception as e:
        rows.append(_row('Проект', 'warn', e))
    free = shutil.disk_usage(bridge.workspace).free
    rows.append(_row('Свободно на диске', 'ok' if free > 2 << 30 else 'warn', f'{free / (1 << 30):.1f} ГБ',
                     '' if free > 2 << 30 else 'Меньше 2 ГБ — сборки и npm могут не поместиться'))
    rows.append(_row('Права', 'info', 'администратор' if _admin() else 'обычный пользователь',
                     'Команды Claude выполняются с правами этого пользователя'))
    secrets = bridge.directory / 'secrets.json'
    if secrets.exists() and os.name != 'nt' and secrets.stat().st_mode & 0o077:
        rows.append(_row('secrets.json', 'warn', oct(secrets.stat().st_mode & 0o777), 'chmod 600 secrets.json'))
    for cid in bridge.connectors.enabled:
        status = bridge.connectors.status.get(cid)
        if status and status != 'connected':
            rows.append(_row(f'Коннектор {cid}', 'warn', status, f'/cc connect {cid} — проверь ключ или вход'))
    worst = 'fail' if any(r['status'] == 'fail' for r in rows) else 'warn' if any(r['status'] == 'warn' for r in rows) else 'ok'
    return {'rows': rows, 'overall': worst, 'time': time.time()}
