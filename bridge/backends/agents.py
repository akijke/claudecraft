"""Subagents Claude Code can delegate to (Task/Agent tool), plus custom ones.

Sources, later wins: built-in presets < custom (agents.json, made in-game)
< project files <project>/.claude/agents/*.md (the standard Claude Code format,
so agents written for the terminal work here too). Disabled names are skipped.
"""
import json
from pathlib import Path
import re
from backends.util import save_json

READ_ONLY = ['Read', 'Glob', 'Grep']
PRESETS = {
    'code-reviewer': {
        'description': 'Reviews recent changes for bugs, security issues and readability. Use after writing code.',
        'prompt': 'You are a senior code reviewer. Inspect the relevant diff and files. Report concrete '
                  'problems ranked by severity with file:line, why it matters and a fix. No praise, no nitpicks.',
        'tools': READ_ONLY + ['Bash']},
    'test-writer': {
        'description': 'Writes and runs tests for the given code. Use when tests are missing or failing.',
        'prompt': 'You write focused, deterministic tests in the project\'s existing framework and style, '
                  'run them, and fix the tests (not the product code) until they pass or reveal a real bug.',
        'tools': READ_ONLY + ['Write', 'Edit', 'Bash']},
    'debugger': {
        'description': 'Finds the root cause of an error, crash or failing test and proposes a minimal fix.',
        'prompt': 'You are a debugging specialist. Reproduce the problem, read logs and stack traces, form '
                  'hypotheses, verify them, and report the root cause with the smallest correct fix.',
        'tools': READ_ONLY + ['Bash', 'Edit']},
    'explorer': {
        'description': 'Fast read-only search of the codebase: where things are defined and how they connect.',
        'prompt': 'You explore code read-only. Answer precisely with file paths and short excerpts. '
                  'Never modify files.',
        'tools': READ_ONLY},
    'refactorer': {
        'description': 'Simplifies and restructures code without changing behaviour.',
        'prompt': 'You refactor for clarity and simplicity while preserving behaviour exactly. Keep changes '
                  'small and run the tests after each step.',
        'tools': READ_ONLY + ['Edit', 'Write', 'Bash']},
    'docs-writer': {
        'description': 'Writes or updates README, docstrings and usage docs for the code.',
        'prompt': 'You write clear, accurate, concise documentation that matches the code as it is. '
                  'Prefer examples over prose.',
        'tools': READ_ONLY + ['Edit', 'Write']},
    'security-auditor': {
        'description': 'Audits code and dependencies for vulnerabilities and leaked secrets.',
        'prompt': 'You are an application security auditor. Look for injection, auth flaws, unsafe '
                  'deserialization, path traversal, secrets in code, risky dependencies. Report with severity.',
        'tools': READ_ONLY + ['Bash']},
    'planner': {
        'description': 'Breaks a large task into a concrete step-by-step implementation plan.',
        'prompt': 'You design implementation plans: files to touch, order of steps, risks, how to verify. '
                  'Read the code first; do not edit anything.',
        'tools': READ_ONLY},
    'minecraft-modder': {
        'description': 'Fabric/NeoForge/Paper modding expert: mappings, mixins, crash reports, datapacks.',
        'prompt': 'You are an expert Minecraft mod and plugin developer (Fabric, NeoForge, Paper, datapacks, '
                  'resource packs). Read crash reports and latest.log via the minecraft tools when relevant, '
                  'explain the cause and fix it in the project.',
        'tools': READ_ONLY + ['Edit', 'Write', 'Bash', 'mcp__minecraft__minecraft_logs']},
}
NAME = re.compile(r'[a-z][a-z0-9-]{0,39}')


def parse_markdown_agent(path):
    """Claude Code agent file: YAML-ish frontmatter (name, description, tools, model) + prompt body."""
    text = path.read_text(encoding='utf-8')
    if not text.startswith('---'):
        return None
    _, front, body = text.split('---', 2)
    meta = {}
    for line in front.splitlines():
        key, sep, value = line.partition(':')
        if sep:
            meta[key.strip()] = value.strip().strip('"\'')
    name = meta.get('name') or path.stem
    tools = [t.strip() for t in meta.get('tools', '').split(',') if t.strip()] or None
    return name, {'description': meta.get('description', name), 'prompt': body.strip(),
                  'tools': tools, 'model': meta.get('model') or None}


class Agents:
    def __init__(self, bridge):
        self.bridge = bridge
        self.file = bridge.directory / 'agents.json'
        data = json.loads(self.file.read_text(encoding='utf-8')) if self.file.exists() else {}
        self.custom = data.get('custom', {})
        self.disabled = set(data.get('disabled', []))

    def save(self):
        save_json(self.file, {'custom': self.custom, 'disabled': sorted(self.disabled)})

    def all(self, project_path=None):
        """name -> (definition, source)."""
        result = {name: (d, 'встроенный') for name, d in PRESETS.items()}
        result.update({name: (d, 'свой') for name, d in self.custom.items()})
        folder = Path(project_path or self.bridge.projects.path_of_current()) / '.claude' / 'agents'
        if folder.is_dir():
            for path in sorted(folder.glob('*.md')):
                try:
                    parsed = parse_markdown_agent(path)
                except (OSError, ValueError):
                    parsed = None
                if parsed:
                    result[parsed[0]] = (parsed[1], 'проект')
        return result

    def enabled(self, project_path=None):
        return {name: d for name, (d, _) in self.all(project_path).items() if name not in self.disabled}

    def definitions(self, project_path=None):
        """For ClaudeAgentOptions(agents=...)."""
        from claude_agent_sdk import AgentDefinition
        return {name: AgentDefinition(description=d['description'], prompt=d['prompt'],
                                      tools=d.get('tools'), model=d.get('model'))
                for name, d in self.enabled(project_path).items()}

    def rows(self):
        return [{'name': name, 'description': d['description'], 'source': source,
                 'enabled': name not in self.disabled, 'tools': d.get('tools') or ['все']}
                for name, (d, source) in sorted(self.all().items())]

    def create(self, name, description):
        if not NAME.fullmatch(name or ''):
            raise ValueError('Имя агента: латиница в нижнем регистре, цифры, дефис (например my-helper)')
        if not description:
            raise ValueError('Формат: agent new <имя> <чем занимается агент>')
        self.custom[name] = {'description': description[:300],
                             'prompt': f'You are a specialised assistant. Your job: {description}. '
                                       'Work carefully inside the current project and report results concisely.',
                             'tools': None}
        self.disabled.discard(name)
        self.save()

    def remove(self, name):
        if name not in self.custom:
            raise ValueError('Удалить можно только своего агента; встроенный — выключить: agent off <имя>')
        del self.custom[name]
        self.save()

    def toggle(self, name, on):
        if name not in self.all():
            raise ValueError(f'Нет агента {name}')
        (self.disabled.discard if on else self.disabled.add)(name)
        self.save()
