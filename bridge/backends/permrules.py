"""Permission rules in Claude Code's format: allow / deny / ask lists of patterns.

Stored in <project>/.claude/settings.json → "permissions" (written by the bridge,
other keys are kept); ~/.claude/settings.json rules are read too. Order: deny wins,
then ask, then allow; no match → the chat's auto mode and the usual question.

Patterns:
  Bash                 every shell command
  Bash(git status)     exactly this command
  Bash(npm run test:*) prefix: everything starting with "npm run test"
  Bash(git *)          glob (* = anything)
  Edit(src/**)         gitignore-like path glob relative to the project; Edit rules
                       cover Write/MultiEdit/NotebookEdit too; Read(...) covers Glob/Grep
  WebFetch(domain:example.com)
  mcp__github          every tool of an MCP server; mcp__github__create_issue — one tool
"""
import fnmatch
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from backends.util import save_json

KINDS = ('allow', 'deny', 'ask')
EDIT_FAMILY = {'Edit', 'Write', 'MultiEdit', 'NotebookEdit'}
READ_FAMILY = {'Read', 'Glob', 'Grep', 'LS'}
RULE = re.compile(r'^([A-Za-z_][\w\-]*)(?:\((.*)\))?$', re.S)


def user_settings():
    return Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude') / 'settings.json'


def project_settings(project):
    return Path(project) / '.claude' / 'settings.json'


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def rules(project):
    """{'allow': [(rule, source)], ...} from project and user settings."""
    out = {k: [] for k in KINDS}
    for source, path in (('project', project_settings(project)), ('user', user_settings())):
        perms = _load(path).get('permissions') or {}
        for kind in KINDS:
            for rule in perms.get(kind) or []:
                if isinstance(rule, str) and rule.strip():
                    out[kind].append((rule.strip(), source))
    return out


def _glob(pattern, path, project):
    """gitignore-ish: relative to the project, ** spans folders, //abs and ~/ allowed."""
    if pattern.startswith('//'):
        base, pattern = Path('/'), pattern[2:]
        target = Path(path)
    elif pattern.startswith('~/'):
        target, pattern = Path(path), str(Path.home() / pattern[2:])
    else:
        try:
            target = Path(path).resolve().relative_to(Path(project).resolve())
        except ValueError:
            return False  # outside the project: relative rules don't apply
    text = str(target).replace('\\', '/')
    pattern = pattern.replace('\\', '/')
    regex = re.escape(pattern).replace(r'\*\*/', '(?:.*/)?').replace(r'\*\*', '.*').replace(r'\*', '[^/]*').replace(r'\?', '[^/]')
    if pattern.endswith('/'):
        regex += '.*'
    return re.fullmatch(regex, text, re.I if os.name == 'nt' else 0) is not None or \
        (not '/' in pattern and fnmatch.fnmatch(Path(text).name, pattern))


def matches(rule, name, data, project):
    m = RULE.match(rule.strip())
    if not m:
        return False
    tool, spec = m.group(1), m.group(2)
    if tool.startswith('mcp__'):
        return name == tool or name.startswith(tool + '__')
    family = EDIT_FAMILY if tool == 'Edit' else READ_FAMILY if tool == 'Read' else {tool}
    if name not in family:
        return False
    if spec is None or spec.strip() in ('', '*'):
        return True
    spec = spec.strip()
    if tool == 'Bash':
        command = ' '.join(str(data.get('command', '')).split())
        if spec.endswith(':*'):
            prefix = spec[:-2].strip()
            return command == prefix or command.startswith(prefix + ' ') or command.startswith(prefix)
        return fnmatch.fnmatchcase(command, spec)
    if tool == 'WebFetch' and spec.startswith('domain:'):
        host = (urlparse(str(data.get('url', ''))).hostname or '').lower()
        domain = spec[7:].lower()
        return host == domain or host.endswith('.' + domain)
    if tool in ('Edit', 'Read') or name in EDIT_FAMILY | READ_FAMILY:
        path = data.get('file_path') or data.get('notebook_path') or data.get('path')
        if not path:
            return False
        path = path if Path(path).is_absolute() else str(Path(project) / path)
        return _glob(spec, path, project)
    return fnmatch.fnmatchcase(json.dumps(data, ensure_ascii=False), '*' + spec + '*')


def decide(name, data, project):
    """('deny'|'ask'|'allow', rule) or (None, None)."""
    table = rules(project)
    for kind in ('deny', 'ask', 'allow'):
        for rule, _ in table[kind]:
            if matches(rule, name, data, project):
                return kind, rule
    return None, None


def suggest(name, data, project):
    """A sensible 'always allow' rule for this call."""
    if name == 'Bash':
        words = str(data.get('command', '')).split()
        if not words:
            return 'Bash'
        head = words[0] if words[0] not in ('git', 'npm', 'npx', 'pnpm', 'yarn', 'cargo', 'gradle', './gradlew',
                                            'gradlew', 'python', 'py', 'pip', 'docker', 'go', 'dotnet', 'uv') \
            or len(words) == 1 else ' '.join(words[:2])
        return f'Bash({head}:*)'
    if name in EDIT_FAMILY | READ_FAMILY:
        path = data.get('file_path') or data.get('notebook_path') or data.get('path')
        if path:
            try:
                rel = Path(path if Path(path).is_absolute() else Path(project) / path).resolve().relative_to(
                    Path(project).resolve())
                folder = rel.parent.as_posix()
                tool = 'Edit' if name in EDIT_FAMILY else 'Read'
                return f'{tool}({folder}/**)' if folder != '.' else f'{tool}(*)'
            except ValueError:
                pass
        return 'Edit' if name in EDIT_FAMILY else 'Read'
    if name == 'WebFetch' and data.get('url'):
        return f'WebFetch(domain:{urlparse(data["url"]).hostname})'
    return name


def add(project, kind, rule):
    if kind not in KINDS:
        raise ValueError('Тип правила: allow | deny | ask')
    rule = rule.strip()
    if not RULE.match(rule) or len(rule) > 300:
        raise ValueError(f'Неверное правило: {rule} (пример: Bash(git *), Edit(src/**))')
    path = project_settings(project)
    data = _load(path)
    perms = data.setdefault('permissions', {})
    for other in KINDS:  # one rule lives in one list
        if other != kind and rule in (perms.get(other) or []):
            perms[other].remove(rule)
    perms.setdefault(kind, [])
    if rule not in perms[kind]:
        perms[kind].append(rule)
    path.parent.mkdir(parents=True, exist_ok=True)
    save_json(path, data)


def remove(project, rule):
    path = project_settings(project)
    data = _load(path)
    perms = data.get('permissions') or {}
    found = False
    for kind in KINDS:
        if rule in (perms.get(kind) or []):
            perms[kind].remove(rule)
            found = True
    if not found:
        raise ValueError(f'Нет такого правила в настройках проекта: {rule}')
    save_json(path, data)
