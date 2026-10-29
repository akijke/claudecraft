"""Watch mode (/cc watch <command>): rerun tests or the build when project files change.

The bridge polls file modification times (build/vendor folders skipped) every
2 seconds; after a change it waits for the edits to settle, runs the command in the
project folder and reports: failure → a card with the end of the output and a
"Claude, fix it" button (nothing is sent to Claude without that click); fixed
again → a short green line. One watcher per project; /cc watch stop ends it.
"""
import asyncio

from backends.shell import _kill_tree as kill_tree
import os
import time
from pathlib import Path

SKIP = {'.git', 'node_modules', '.gradle', 'build', 'dist', 'target', '__pycache__', '.venv', 'venv', '.idea', 'out',
        'run', '.claudecraft-worktrees', 'claude-exports'}


def snapshot(root, limit=20000):
    state = {}
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith('.')]
        for name in names:
            path = os.path.join(folder, name)
            try:
                state[path] = os.stat(path).st_mtime_ns
            except OSError:
                continue
            if len(state) >= limit:
                return state
    return state


class Watcher:
    def __init__(self, bridge, chat_id, root, command, interval=2.0, settle=1.5):
        self.bridge, self.chat_id, self.root, self.command = bridge, chat_id, str(root), command
        self.interval, self.settle = interval, settle
        self.task = None
        self.runs = 0
        self.last = None        # 'passed' | 'failed'
        self.last_output = ''

    def start(self):
        self.task = asyncio.create_task(self._loop())
        return self

    def stop(self):
        if self.task:
            self.task.cancel()

    async def run_once(self):
        self.runs += 1
        started = time.monotonic()
        process = await asyncio.create_subprocess_shell(self.command, cwd=self.root, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(process.communicate(), 900)
        except TimeoutError:
            kill_tree(process.pid)
            out = b'(timed out after 15 min)'
        except asyncio.CancelledError:
            kill_tree(process.pid)  # /cc watch stop: the tests it started stop too
            raise
        text = out.decode('utf-8', 'replace')
        ok = process.returncode == 0
        chat = self.bridge.chats.get(self.chat_id)
        if chat is None:
            return ok
        seconds = round(time.monotonic() - started, 1)
        if not ok:
            self.last_output = '\n'.join(text.strip().splitlines()[-60:])
            self.bridge.emit(chat, f'{self.command} — упало (код {process.returncode}, {seconds} с)', kind='watch_failed',
                             command=self.command, code=process.returncode, output=self.last_output, root=self.root)
        elif self.last == 'failed':
            self.bridge.emit(chat, f'{self.command} — снова проходит ({seconds} с)', kind='watch_ok', command=self.command)
        self.last = 'passed' if ok else 'failed'
        return ok

    async def _loop(self):
        before = await asyncio.to_thread(snapshot, self.root)
        await self.run_once()
        while True:
            await asyncio.sleep(self.interval)
            now = await asyncio.to_thread(snapshot, self.root)
            if now == before:
                continue
            # wait until the edits settle (Claude or an editor may write several files)
            while True:
                await asyncio.sleep(self.settle)
                again = await asyncio.to_thread(snapshot, self.root)
                if again == now:
                    break
                now = again
            before = now
            await self.run_once()
            before = await asyncio.to_thread(snapshot, self.root)  # the command itself may write files


def fix_prompt(command, output):
    return (f'The watched command `{command}` fails. Its output (end):\n```\n{output[-6000:]}\n```\n'
            'Find the cause and fix it with the smallest sensible change; then run the command again to confirm. '
            'If the failure is not in our code (environment, network), explain instead of changing code.')
