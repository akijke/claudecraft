"""Claude's memory files: CLAUDE.md of the project, CLAUDE.local.md, and ~/.claude/CLAUDE.md.

Claude Code reads them only when the matching setting sources are loaded; the
bridge loads user + project + local settings unless "load_claude_settings" is
false in bridge-config.json (then Claude sees no CLAUDE.md, rules or hooks from
disk — the old behaviour). `/cc # <text>` appends a note to the project's
CLAUDE.md under "## Notes".
"""
import os
from pathlib import Path

NOTES = '## Notes'


def user_file():
    return Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude') / 'CLAUDE.md'


def files(project):
    project = Path(project)
    out = []
    for label, path in (('project', project / 'CLAUDE.md'), ('local', project / 'CLAUDE.local.md'),
                        ('user', user_file())):
        info = {'scope': label, 'path': str(path), 'exists': path.is_file(), 'lines': 0, 'preview': []}
        if path.is_file():
            try:
                text = path.read_text(encoding='utf-8', errors='replace').splitlines()
                info['lines'] = len(text)
                info['preview'] = [l for l in text if l.strip()][:4]
            except OSError:
                pass
        out.append(info)
    return out


def remember(project, note):
    """Appends `- note` under ## Notes of <project>/CLAUDE.md (created when missing)."""
    note = ' '.join(note.split())
    if not note:
        raise ValueError('#: /cc # <что запомнить>')
    if len(note) > 2000:
        raise ValueError('Заметка длиннее 2000 символов — открой CLAUDE.md в редакторе')
    path = Path(project) / 'CLAUDE.md'
    text = path.read_text(encoding='utf-8') if path.is_file() else ''
    eol = '\r\n' if '\r\n' in text else '\n'
    lines = text.split(eol) if text else []
    while lines and not lines[-1].strip():
        lines.pop()
    if NOTES in [l.strip() for l in lines]:
        # insert after the last bullet of the Notes section
        start = [l.strip() for l in lines].index(NOTES)
        end = start + 1
        while end < len(lines) and not lines[end].startswith('#'):
            end += 1
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        lines.insert(end, f'- {note}')
    else:
        if lines:
            lines.append('')
        lines += [NOTES, f'- {note}']
    path.write_text(eol.join(lines) + eol, encoding='utf-8', newline='')
    return str(path)


def ensure(path):
    """Creates an empty memory file so the editor can open it."""
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# ' + ('Instructions for Claude' if path.name == 'CLAUDE.md' else 'Local notes') + '\n',
                        encoding='utf-8')
    return str(path)
