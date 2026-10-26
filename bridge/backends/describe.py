"""Human-readable progress lines for Claude Code tool calls (shown in Minecraft chat).

describe(name, input, cwd) -> (icon, text). Icons are BMP symbols that the
Minecraft font (unifont fallback) can draw; no colour emoji.
"""
from pathlib import Path

# Tools that only read or edit files inside the project: auto mode "edits" allows them.
EDIT_TOOLS = {'Read', 'Write', 'Edit', 'MultiEdit', 'NotebookEdit', 'Glob', 'Grep', 'LS', 'TodoWrite'}


def short_path(value, cwd):
    if not value:
        return '?'
    try:
        path = Path(value)
        if cwd and path.is_absolute():
            relative = path.resolve().relative_to(Path(cwd).resolve())
            return str(relative).replace('\\', '/') or '.'
    except (ValueError, OSError):
        pass
    return str(value).replace('\\', '/')


def clip(text, limit=140):
    text = ' '.join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def describe(name, data, cwd=None):
    data = data or {}
    p = lambda key='file_path': short_path(data.get(key) or data.get('path'), cwd)
    if name == 'Read':
        return '📄', f'Читаю {p()}'
    if name == 'Write':
        return '✚', f'Создаю {p()}'
    if name in ('Edit', 'MultiEdit'):
        return '✎', f'Редактирую {p()}'
    if name == 'NotebookEdit':
        return '✎', f'Редактирую блокнот {p("notebook_path")}'
    if name == 'Bash':
        command = clip(data.get('command', ''), 160)
        about = data.get('description')
        return '$', f'{command}' + (f'  — {clip(about, 80)}' if about else '')
    if name == 'Glob':
        return '🔍', f'Ищу файлы {data.get("pattern", "")}' + (f' в {p("path")}' if data.get('path') else '')
    if name == 'Grep':
        return '🔍', f'Ищу «{clip(data.get("pattern", ""), 60)}»' + (f' в {p("path")}' if data.get('path') else '')
    if name == 'LS':
        return '📁', f'Смотрю папку {p("path")}'
    if name == 'WebSearch':
        return '◎', f'Ищу в интернете: {clip(data.get("query", ""), 100)}'
    if name == 'WebFetch':
        return '◎', f'Открываю {clip(data.get("url", ""), 100)}'
    if name == 'TodoWrite':
        return '☰', 'Обновляю план'
    if name in ('Task', 'Agent'):
        return '◆', f'Запускаю помощника: {clip(data.get("description") or data.get("prompt", ""), 100)}'
    if name == 'AskUserQuestion':
        return '?', 'Задаю вопрос'
    if name.startswith('mcp__'):
        parts = name.split('__')
        server, tool = (parts[1], parts[2]) if len(parts) >= 3 else ('mcp', name)
        if server == 'minecraft' and tool == 'open_browser':
            return '◎', f'Предлагаю открыть {clip(data.get("url", ""), 100)}'
        if server == 'minecraft' and tool == 'reload_extensions':
            return '⟳', 'Перезагружаю расширения'
        return '⚙', f'{server}.{tool}'
    return '⚙', name


def todo_rows(data):
    """TodoWrite input -> [{'text', 'status'}] for a compact checklist."""
    rows = []
    for item in (data or {}).get('todos', [])[:15]:
        status = item.get('status', 'pending')
        text = item.get('activeForm') if status == 'in_progress' and item.get('activeForm') else item.get('content', '')
        rows.append({'text': clip(text, 100), 'status': status})
    return rows
