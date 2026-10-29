"""Export / import of settings and projects (#81): move Claudecraft to another PC or profile.

The archive is a zip with only the files below. Never included: secrets.json (API keys,
connector keys), connection.json, bridge-token, chats.json, guests.json, input history —
and from client.json the "remote" token. On import every existing file is copied to
backups/<time>/ first; projects are merged (existing names win), the machine-specific
client.json keys (python, remote) of this PC are kept.
"""
import json
import time
import zipfile
from pathlib import Path

FILES = ('client.json', 'bridge-config.json', 'projects.json', 'agents.json', 'budget.json', 'connectors.json')
LOCAL_KEYS = ('python', 'remote')   # client.json keys that belong to this PC
# bridge-config.json keys taken from an archive. Not taken (they start programs, send keys to
# servers or change prompts/folders — an archive from someone else must not do that):
# mcp_servers, lsp, providers, cli_path, codex_path, gemini_path, gemini_args, styles, workspace.
SAFE_CONFIG = ('api_model', 'load_claude_settings', 'skills_disabled', 'spawn_worktrees')
MAX = 2 * 1024 * 1024


def export(directory, target=None):
    directory = Path(directory)
    out = Path(target) if target else directory / 'exports' / time.strftime('claudecraft-settings-%Y%m%d-%H%M%S.zip')
    out.parent.mkdir(parents=True, exist_ok=True)
    names = []
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in FILES:
            f = directory / name
            if not f.is_file():
                continue
            data = f.read_text(encoding='utf-8')
            if name == 'client.json':
                obj = json.loads(data)
                for key in LOCAL_KEYS:
                    obj.pop(key, None)
                data = json.dumps(obj, ensure_ascii=False, indent=2)
            if name == 'bridge-config.json':
                # only what an import would take: mcp_servers env etc. can hold connection strings
                obj = json.loads(data)
                data = json.dumps({k: v for k, v in obj.items() if k in SAFE_CONFIG}, ensure_ascii=False, indent=2)
            z.writestr(name, data)
            names.append(name)
        z.writestr('claudecraft-export.json', json.dumps({'version': 1, 'created': round(time.time()), 'files': names}))
    return out, names


def read_archive(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f'Нет файла: {path}')
    found = {}
    with zipfile.ZipFile(path) as z:
        if 'claudecraft-export.json' not in z.namelist():
            raise ValueError('Это не экспорт Claudecraft (нет claudecraft-export.json)')
        for info in z.infolist():
            # only known flat names: no paths, no zip-slip, no surprises
            if info.filename in FILES and info.file_size <= MAX:
                found[info.filename] = json.loads(z.read(info).decode('utf-8'))
    return found


def import_(directory, path, projects):
    """Writes the archive's files; returns (written names, merged projects, projects without a folder here, bridge-config keys not imported)."""
    directory = Path(directory)
    found = read_archive(path)
    backup = directory / 'backups' / time.strftime('%Y%m%d-%H%M%S')
    written, merged, missing, skipped = [], [], [], []
    for name, data in found.items():
        target = directory / name
        if target.is_file():
            backup.mkdir(parents=True, exist_ok=True)
            (backup / name).write_bytes(target.read_bytes())
        if name == 'projects.json':
            for pname, item in (data.get('projects') or {}).items():
                if pname in projects.items or not isinstance(item, dict) or not item.get('path'):
                    continue
                projects.items[pname] = {k: v for k, v in item.items() if k != 'builtin'}
                merged.append(pname)
                if not Path(item['path']).is_dir():
                    missing.append(pname)
            projects.save()
            written.append(name)
            continue
        if name == 'bridge-config.json':
            try:
                here = json.loads(target.read_text(encoding='utf-8')) if target.is_file() else {}
            except ValueError:
                here = {}
            skipped += sorted(k for k in data if k not in SAFE_CONFIG)
            data = {**here, **{k: v for k, v in data.items() if k in SAFE_CONFIG}}

        if name == 'client.json':
            try:
                here = json.loads(target.read_text(encoding='utf-8')) if target.is_file() else {}
            except ValueError:
                here = {}
            data = {**data, **{k: here[k] for k in LOCAL_KEYS if k in here}}
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        written.append(name)
    return written, merged, missing, skipped
