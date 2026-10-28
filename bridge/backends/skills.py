"""Claude Code skills: <project>/.claude/skills/<name>/SKILL.md and ~/.claude/skills/<name>/SKILL.md.

The bridge lists them, lets the player switch single skills off (bridge-config.json
→ skills_disabled) and passes the enabled ones to the SDK (`skills=`), which hides
the others from Claude. A project skill overrides a user skill of the same name.
"""
import os
import re
from pathlib import Path


def user_dir():
    return Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude') / 'skills'


def _meta(path):
    try:
        text = path.read_text(encoding='utf-8', errors='replace')[:20000]
    except OSError:
        return {}, ''
    meta = {}
    if text.startswith('---'):
        end = text.find('\n---', 3)
        if end > 0:
            for line in text[3:end].splitlines():
                if ':' in line and not line.startswith(' '):
                    key, value = line.split(':', 1)
                    meta[key.strip().lower()] = value.strip().strip('"\'')
            text = text[end + 4:]
    return meta, text


def discover(project):
    found = {}
    for source, folder in (('user', user_dir()), ('project', Path(project) / '.claude' / 'skills' if project else None)):
        if folder is None or not folder.is_dir():
            continue
        for skill in sorted(folder.glob('*/SKILL.md')):
            meta, body = _meta(skill)
            name = meta.get('name') or skill.parent.name
            if not re.fullmatch(r'[\w.\-]{1,64}', name):
                continue
            found[name] = {'name': name, 'source': source, 'path': str(skill),
                           'description': meta.get('description', '')[:300],
                           'lines': body.count('\n') + 1}
    return found


def rows(project, disabled):
    return [{**s, 'enabled': s['name'] not in disabled} for s in sorted(discover(project).values(),
            key=lambda s: (s['source'] != 'project', s['name']))]


def sdk_option(project, disabled):
    """Value for ClaudeAgentOptions.skills: 'all', the enabled names, or None when there are no skills."""
    names = list(discover(project))
    if not names:
        return None
    if not disabled:
        return 'all'
    return [n for n in names if n not in disabled]
