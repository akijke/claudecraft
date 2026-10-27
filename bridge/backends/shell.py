"""Shell from the Minecraft chat: /sh, /ps, /cmd, /bash <command>.

Commands run on the bridge's PC with the bridge user's rights, in the file manager's
current folder (`cd` moves that folder, so it persists between commands). Output is
streamed back in batches of lines while the command runs; /sh kill stops it. Commands
are not interactive (stdin is closed): editors, password prompts and REPLs won't work.
"""
import asyncio
import os
import re
import shutil
import time

MAX_LINES = 3000
ANSI = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07')


def shell_args(kind, command):
    """argv for the chosen shell; output forced to UTF-8 so Cyrillic etc. survive."""
    windows = os.name == 'nt'
    if kind == 'auto':
        kind = 'ps' if windows else 'bash'
    if kind == 'ps':
        exe = shutil.which('pwsh') or shutil.which('powershell') or 'powershell'
        prefix = '[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8; '
        return 'PowerShell', [exe, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', prefix + command]
    if kind == 'cmd':
        if not windows:
            raise ValueError('cmd есть только в Windows — используй /sh или /bash')
        return 'cmd', ['cmd', '/d', '/s', '/c', 'chcp 65001>nul & ' + command]
    exe = shutil.which('bash')
    if not exe:
        raise ValueError('bash не найден — в Windows поставь Git for Windows или используй /ps')
    return 'bash', [exe, '-lc', command]


class Shell:
    def __init__(self, bridge):
        self.bridge = bridge
        self.jobs = {}  # id -> {'process', 'task', 'command'}
        self.counter = 0

    async def run(self, mode, kind, command):
        command = (command or '').strip()
        if not command:
            raise ValueError('Формат: /sh <команда>  (а также /ps, /cmd, /bash)')
        # `cd` alone must change the folder for the NEXT commands, so it is handled here.
        m = re.fullmatch(r'cd(?:\s+(.*))?', command, re.I)
        if m and not re.search(r'[;&|]', command):
            self.bridge.files.cd(mode, (m.group(1) or '').strip().strip('"\''))
            return
        name, argv = shell_args(kind, command)
        folder = self.bridge.files.here(mode)
        chat = self.bridge.current_chat(mode)
        self.counter += 1
        job = str(self.counter)
        env = dict(os.environ, PYTHONIOENCODING='utf-8', TERM='dumb', NO_COLOR='1', GIT_TERMINAL_PROMPT='0')
        process = await asyncio.create_subprocess_exec(
            *argv, cwd=str(folder), env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        self.bridge.emit(chat, command, kind='shell_start', job=job, shell=name, cwd=str(folder))
        task = asyncio.create_task(self._pump(chat, job, process, command))
        self.jobs[job] = {'process': process, 'task': task, 'command': command, 'mode': mode}
        task.add_done_callback(lambda _: self.jobs.pop(job, None))

    async def _pump(self, chat, job, process, command):
        started, lines, sent, pending = time.monotonic(), 0, 0, []
        last_flush = time.monotonic()

        def flush():
            nonlocal pending, last_flush, sent
            if pending:
                self.bridge.emit(chat, '\n'.join(pending), kind='shell_out', job=job)
                sent += len(pending)
                pending = []
            last_flush = time.monotonic()

        try:
            while True:
                try:
                    raw = await asyncio.wait_for(process.stdout.readline(), 0.3)
                except TimeoutError:
                    flush()
                    continue
                if not raw:
                    break
                lines += 1
                if lines <= MAX_LINES:
                    pending.append(ANSI.sub('', raw.decode('utf-8', 'replace').rstrip('\r\n'))[:500])
                if len(pending) >= 25 or time.monotonic() - last_flush > 0.3:
                    flush()
            code = await process.wait()
            flush()
            self.bridge.emit(chat, command, kind='shell_end', job=job, code=code,
                             seconds=round(time.monotonic() - started, 1), cut=max(0, lines - MAX_LINES))
        except asyncio.CancelledError:
            if process.returncode is None:
                process.kill()
            flush()
            self.bridge.emit(chat, command, kind='shell_end', job=job, code=None, killed=True,
                             seconds=round(time.monotonic() - started, 1), cut=0)
            raise

    async def kill(self, job=None):
        targets = [job] if job else list(self.jobs)
        if job and job not in self.jobs:
            raise ValueError(f'Нет команды {job}')
        for key in targets:
            entry = self.jobs.get(key)
            if not entry:
                continue
            process = entry['process']
            if process.returncode is None:
                _kill_tree(process.pid)
            entry['task'].cancel()

    def running(self):
        return [{'job': k, 'command': v['command']} for k, v in self.jobs.items()]

    async def close(self):
        await self.kill()


def _kill_tree(pid):
    """Stop the shell and everything it started (e.g. gradle daemons stay, child tools go)."""
    if os.name == 'nt':
        import subprocess
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(pid)], capture_output=True)
    else:
        import signal
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except OSError:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
