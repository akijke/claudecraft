"""Code formatting for the editor (Ctrl+Shift+F) with tools that are already installed.

The text goes through the formatter's stdin and comes back formatted, so the
editor can show it as an undoable change before saving. Nothing is installed or
downloaded automatically; when no tool is found the error says what to install.
"""
import asyncio
import os
import shutil
from pathlib import Path

# extension -> [(tool, args with {file} = the file name for config lookup)]
TOOLS = {
    '.py': [('ruff', ['format', '--stdin-filename', '{file}', '-']), ('black', ['-q', '--stdin-filename', '{file}', '-'])],
    '.java': [('google-java-format', ['-'])],
    '.go': [('gofmt', [])],
    '.rs': [('rustfmt', ['--emit', 'stdout', '--quiet'])],
    '.c': [('clang-format', ['--assume-filename={file}'])], '.h': [('clang-format', ['--assume-filename={file}'])],
    '.cpp': [('clang-format', ['--assume-filename={file}'])], '.hpp': [('clang-format', ['--assume-filename={file}'])],
    '.cs': [('clang-format', ['--assume-filename={file}'])],
}
PRETTIER = {'.js', '.jsx', '.ts', '.tsx', '.json', '.css', '.scss', '.less', '.html', '.vue', '.md', '.yaml', '.yml',
            '.graphql'}
HINTS = {'.py': 'pip install ruff', '.java': 'google-java-format (github.com/google/google-java-format) в PATH',
         '.go': 'Go (gofmt)', '.rs': 'rustup component add rustfmt', '.c': 'clang-format (LLVM)'}


def _find_prettier(path):
    """Project-local prettier (node_modules/.bin) first, then a global one."""
    folder = Path(path).resolve().parent
    for parent in [folder, *folder.parents]:
        local = parent / 'node_modules' / '.bin' / ('prettier.cmd' if os.name == 'nt' else 'prettier')
        if local.is_file():
            return str(local)
    return shutil.which('prettier')


def candidates(path):
    ext = Path(path).suffix.lower()
    out = [(shutil.which(tool), args) for tool, args in TOOLS.get(ext, [])]
    if ext in PRETTIER:
        out.append((_find_prettier(path), ['--stdin-filepath', '{file}']))
    return [(exe, args) for exe, args in out if exe]


async def format_text(path, text, timeout=30):
    """(formatted text, tool name); ValueError when no formatter or it fails."""
    tools = candidates(path)
    if not tools:
        ext = Path(path).suffix.lower()
        hint = HINTS.get(ext, 'prettier (npm i -D prettier)' if ext in PRETTIER else '')
        raise ValueError('Нет форматтера для ' + (ext or 'этого файла') + (f' — установи: {hint}' if hint else ''))
    exe, args = tools[0]
    command = [exe] + [a.replace('{file}', str(path)) for a in args]
    if os.name == 'nt' and exe.lower().endswith(('.cmd', '.bat')):
        command = ['cmd', '/c'] + command
    process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                   cwd=str(Path(path).parent) if Path(path).parent.is_dir() else None)
    try:
        out, err = await asyncio.wait_for(process.communicate(text.encode('utf-8')), timeout)
    except TimeoutError:
        process.kill()
        raise ValueError(f'{Path(exe).stem}: не уложился в {timeout} с') from None
    if process.returncode != 0:
        message = (err or out).decode('utf-8', 'replace').strip().splitlines()
        raise ValueError(f'{Path(exe).stem}: ' + (message[-1] if message else f'код {process.returncode}')[:300])
    return out.decode('utf-8', 'replace').replace('\r\n', '\n'), Path(exe).stem
