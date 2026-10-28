"""Slash commands for /cc: built-in ones and Markdown commands like in Claude Code.

Custom commands are `<project>/.claude/commands/**/*.md` and
`~/.claude/commands/**/*.md` (CLAUDE_CONFIG_DIR is respected); a sub-folder gives a
namespace: `frontend/test.md` → `/frontend:test`. Optional YAML-ish frontmatter:
`description`, `argument-hint`, `model`. The body is the prompt; `$ARGUMENTS` is
replaced by everything after the command, `$1`, `$2`… by single words. A project
command wins over a user command of the same name. The bridge expands the prompt
itself, so it works with any backend and with setting_sources=[].
"""
import os
import re
from pathlib import Path

# name -> (description, argument hint, prompt or ('action', bridge action))
BUILTIN = {
    'review': ('Ревью текущих изменений (git diff): ошибки, риски, что улучшить', '[на что смотреть]',
               'Review the uncommitted changes in this repository (run git status and git diff, include '
               'staged changes). Report bugs, risks and concrete improvements, most important first, with '
               'file:line references. Do not edit files. Focus: $ARGUMENTS'),
    'security-review': ('Проверка изменений на уязвимости', '',
                        'Do a security review of the pending changes on this branch (git diff against the '
                        'base branch and uncommitted changes): injection, secrets, path traversal, unsafe '
                        'deserialization, auth. List findings with file:line and severity. Do not edit files.'),
    'init': ('Создать или обновить CLAUDE.md по коду проекта', '',
             'Analyze this codebase and create (or improve, if it exists) a concise CLAUDE.md in the project '
             'root: how to build, test and run it, the architecture in a few lines, conventions and pitfalls '
             'a new contributor needs. Keep it short and specific to this repository.'),
    'explain': ('Объяснить файл, функцию или идею проекта', '<что объяснить>',
                'Explain $ARGUMENTS in this project: what it does, how it fits in, with file:line references.'),
    'test': ('Запустить тесты и разобрать падения', '[какие тесты]',
             'Find how tests are run in this project, run them ($ARGUMENTS), and for each failure explain the '
             'cause and propose a fix (ask before large changes).'),
    'commit-message': ('Сообщение коммита по текущим изменениям', '',
                       'Look at git diff --staged (or the unstaged diff if nothing is staged) and write a '
                       'commit message: a short subject line and a body explaining why. Do not commit.'),
    'compact': ('Сжать историю разговора', '[что сохранить]', ('action', 'compact')),
    'clear': ('Новый чат (история остаётся в списке чатов)', '', ('action', 'new')),
    'cost': ('Токены и стоимость', '', ('action', 'tokens')),
    'context': ('Заполнение контекста', '', ('action', 'context')),
    'model': ('Модель и усилие', '[opus|sonnet|haiku]', ('action', 'model')),
    'doctor': ('Проверка окружения', '', ('action', 'doctor')),
    'undo': ('Откатить правки последнего запроса', '[list]', ('action', 'undo')),
    'agents': ('Агенты', '', ('action', 'agents')),
    'mcp': ('Коннекторы MCP', '', ('action', 'connectors')),
}
NAME = re.compile(r'^[\w][\w.\-:]{0,80}$')


def user_dir():
    return Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude') / 'commands'


def _frontmatter(text):
    meta = {}
    if text.startswith('---'):
        end = text.find('\n---', 3)
        if end > 0:
            for line in text[3:end].splitlines():
                if ':' in line:
                    key, value = line.split(':', 1)
                    meta[key.strip().lower()] = value.strip().strip('"\'')
            text = text[end + 4:].lstrip('\n')
    return meta, text


def _scan(folder, source):
    found = {}
    if not folder.is_dir():
        return found
    for path in sorted(folder.rglob('*.md')):
        rel = path.relative_to(folder).with_suffix('')
        name = ':'.join(rel.parts)
        if not NAME.match(name):
            continue
        try:
            meta, body = _frontmatter(path.read_text(encoding='utf-8', errors='replace')[:100_000])
        except OSError:
            continue
        first = next((l.strip('# ').strip() for l in body.splitlines() if l.strip()), '')
        found[name] = {'name': name, 'source': source, 'path': str(path),
                       'description': meta.get('description') or first[:100],
                       'hint': meta.get('argument-hint', ''), 'model': meta.get('model'), 'body': body}
    return found


def available(project_dir):
    """All commands: built-in, then user, then project (later ones override)."""
    result = {name: {'name': name, 'source': 'builtin', 'description': d, 'hint': h, 'body': p}
              for name, (d, h, p) in BUILTIN.items()}
    result.update(_scan(user_dir(), 'user'))
    if project_dir:
        result.update(_scan(Path(project_dir) / '.claude' / 'commands', 'project'))
    return result


def listing(project_dir):
    return [{k: v for k, v in c.items() if k != 'body'} for c in sorted(available(project_dir).values(),
            key=lambda c: ({'project': 0, 'user': 1, 'builtin': 2}[c['source']], c['name']))]


def expand(project_dir, line):
    """'/name args' → ('prompt', text, model) or ('action', action, args)."""
    line = line.strip().lstrip('/')
    name, _, args = line.partition(' ')
    command = available(project_dir).get(name)
    if command is None:
        raise ValueError(f'Нет команды /{name}. Список: /cc commands')
    body = command['body']
    if isinstance(body, tuple):
        return 'action', body[1], args.strip()
    words = args.split()
    text = body.replace('$ARGUMENTS', args.strip())
    text = re.sub(r'\$(\d)', lambda m: words[int(m.group(1)) - 1] if int(m.group(1)) <= len(words) else '', text)
    if args.strip() and '$ARGUMENTS' not in body and not re.search(r'\$\d', body):
        text += '\n\n' + args.strip()  # like Claude Code: arguments are appended when not referenced
    return 'prompt', text.strip(), command.get('model')
