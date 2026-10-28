"""Output styles and an extra system-prompt line per project (/cc style).

Built-in styles plus Claude Code's own `.claude/output-styles/*.md` (project) and
`~/.claude/output-styles/*.md` (user): frontmatter `name`, `description`, body =
instructions. The chosen style and the free-form addition are stored per project
in bridge-config.json → styles, and appended to the system prompt of every
request in that project.
"""
import os
from pathlib import Path

BUILTIN = {
    'default': ('Обычные ответы Claude Code', ''),
    'concise': ('Коротко: только суть, без вступлений', 'Answer as briefly as possible: the result first, no preamble, '
                'no summaries of what you did unless asked. Use short bullet points.'),
    'explanatory': ('Объясняет «почему» и решения по ходу работы', 'While working, briefly explain why you choose an '
                    'approach and what trade-offs exist, as short "Insight" notes, so the user learns the codebase.'),
    'learning': ('Учит: оставляет пользователю небольшие куски кода', 'Teach while you work: for small, instructive pieces '
                 '(about 5-15 lines) leave a TODO(human) with a clear description instead of writing them, explain what '
                 'is needed, and continue after the user has written it.'),
    'reviewer': ('Строгий ревьюер: риски и ошибки прежде всего', 'Act as a strict senior reviewer: point out bugs, edge '
                 'cases and risks first, then suggestions. Do not praise.'),
}


def _scan(folder, source):
    found = {}
    if not folder.is_dir():
        return found
    for path in sorted(folder.glob('*.md')):
        text = path.read_text(encoding='utf-8', errors='replace')[:20000]
        meta = {}
        if text.startswith('---'):
            end = text.find('\n---', 3)
            if end > 0:
                for line in text[3:end].splitlines():
                    if ':' in line:
                        k, v = line.split(':', 1)
                        meta[k.strip().lower()] = v.strip().strip('"\'')
                text = text[end + 4:]
        name = meta.get('name') or path.stem
        found[name] = {'name': name, 'description': meta.get('description', ''), 'prompt': text.strip(),
                       'source': source, 'path': str(path)}
    return found


def available(project):
    out = {n: {'name': n, 'description': d, 'prompt': p, 'source': 'builtin', 'path': ''} for n, (d, p) in BUILTIN.items()}
    home = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')
    out.update(_scan(home / 'output-styles', 'user'))
    if project:
        out.update(_scan(Path(project) / '.claude' / 'output-styles', 'project'))
    return out


def settings(config, project_name):
    return (config.get('styles') or {}).get(project_name, {})


def system_addition(config, project_name, project_dir):
    """Text appended to the system prompt for this project ('' when nothing is set)."""
    s = settings(config, project_name)
    parts = []
    style = available(project_dir).get(s.get('style') or 'default')
    if style and style['prompt']:
        parts.append(f'Output style "{style["name"]}": {style["prompt"]}')
    if s.get('append'):
        parts.append('Project instructions from the user: ' + s['append'])
    return ('\n\n' + '\n\n'.join(parts)) if parts else ''
