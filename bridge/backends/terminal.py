"""Continue a game chat in Claude Code on the PC (and back).

The bridge and Claude Code on the PC share the same session store (~/.claude/projects/
<folder>), so a session is the same thing in both places: the game resumes the newest
state of a session continued in a terminal, and vice versa. Do not drive one session
from both places at the same moment.
"""
import os
import shlex
import shutil
import subprocess
import sys
from backends.account import cli_path


def claude_command(config):
    """Prefer the user's own `claude` (their version/settings); fall back to the bundled CLI."""
    return shutil.which('claude') or cli_path(config)


def open_terminal(folder, config, session_id=None):
    """Open a new terminal window in `folder` running `claude [--resume <id>]`. Returns the command line."""
    exe = claude_command(config)
    if not exe:
        raise ValueError('Claude Code CLI не найден')
    args = [exe] + (['--resume', session_id] if session_id else [])
    if sys.platform == 'win32':
        line = subprocess.list2cmdline(args)
        # `start` opens a separate console window; /k keeps it open after Claude exits.
        subprocess.Popen(f'start "Claude Code" cmd /k {line}', shell=True, cwd=folder)
    elif sys.platform == 'darwin':
        script = f'cd {shlex.quote(str(folder))} && {shlex.join(args)}'
        subprocess.Popen(['osascript', '-e', f'tell application "Terminal" to do script {json_string(script)}'])
    else:
        terminal = shutil.which('x-terminal-emulator') or shutil.which('gnome-terminal') or shutil.which('konsole')
        if not terminal:
            raise ValueError('Не найден терминал (x-terminal-emulator, gnome-terminal, konsole)')
        subprocess.Popen([terminal, '-e', 'bash', '-lc', shlex.join(args) + '; exec bash'], cwd=folder)
    return ' '.join(['claude'] + (['--resume', session_id] if session_id else []))


def json_string(text):
    import json
    return json.dumps(text)


def session_title(session):
    """A readable chat title for an imported PC session."""
    for value in (session.custom_title, session.summary, session.first_prompt):
        if value and value.strip():
            return ' '.join(value.split())[:60]
    return session.session_id[:8]


__all__ = ['open_terminal', 'session_title', 'claude_command', 'os']
