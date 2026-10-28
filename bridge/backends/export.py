"""Export a chat to Markdown or HTML (/cc export [md|html]).

Claude Code chats are read from their session transcript (prompts, answers, tool
calls with short inputs, tool errors); Messages API chats from the bridge's own
history. Files go to <project>/claude-exports/<title>-<date>.<ext>.
"""
import html
import json
import re
import time
from pathlib import Path


def _blocks(content):
    if isinstance(content, str):
        return [{'type': 'text', 'text': content}]
    return [b for b in content or [] if isinstance(b, dict)]


def entries_from_session(session_id, directory):
    from claude_agent_sdk import get_session_messages
    out = []
    for m in get_session_messages(session_id, directory=directory):
        msg = m.message or {}
        for b in _blocks(msg.get('content')):
            kind = b.get('type')
            if kind == 'text' and b.get('text', '').strip():
                text = b['text']
                if m.type == 'user' and text.lstrip().startswith('<'):
                    continue  # CLI reminders, not the player's words
                out.append(('user' if m.type == 'user' else 'assistant', text))
            elif kind == 'tool_use':
                args = json.dumps(b.get('input', {}), ensure_ascii=False)
                out.append(('tool', f"{b.get('name')} {args[:300]}"))
            elif kind == 'tool_result' and b.get('is_error'):
                body = b.get('content')
                out.append(('error', body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)[:500]))
    return out


def entries_from_messages(messages):
    out = []
    for m in messages or []:
        for b in _blocks(m.get('content')):
            if b.get('type') == 'text':
                out.append(('user' if m.get('role') == 'user' else 'assistant', b['text']))
    return out


def render_md(title, entries):
    lines = [f'# {title}', '', f'_Exported from Claudecraft, {time.strftime("%Y-%m-%d %H:%M")}_', '']
    for role, text in entries:
        if role == 'user':
            lines += ['## 🧑 You', '', text, '']
        elif role == 'assistant':
            lines += ['## ✦ Claude', '', text, '']
        elif role == 'tool':
            lines += [f'> 🔧 `{text}`', '']
        else:
            lines += [f'> ✖ {text}', '']
    return '\n'.join(lines)


def render_html(title, entries):
    css = ('body{font-family:system-ui,sans-serif;max-width:860px;margin:2em auto;padding:0 1em;background:#1e1e22;'
           'color:#ddd}.u{background:#2a2f3a;border-radius:8px;padding:.6em 1em;margin:1em 0}.a{padding:.2em 0}'
           '.t{color:#8a8a8a;font-family:monospace;font-size:.9em}.e{color:#f0626a}pre{background:#111;padding:.8em;'
           'overflow:auto;border-radius:6px}h1{color:#d97757}')
    body = []
    for role, text in entries:
        safe = html.escape(text)
        safe = re.sub(r'```(\w*)\n(.*?)```', lambda m: f'<pre>{m.group(2)}</pre>', safe, flags=re.S)
        safe = safe.replace('\n', '<br>') if role in ('user', 'assistant') else safe
        cls = {'user': 'u', 'assistant': 'a', 'tool': 't', 'error': 'e'}[role]
        body.append(f'<div class="{cls}">{safe}</div>')
    return (f'<!doctype html><meta charset="utf-8"><title>{html.escape(title)}</title><style>{css}</style>'
            f'<h1>{html.escape(title)}</h1>' + '\n'.join(body))


def write(folder, title, entries, fmt):
    folder = Path(folder) / 'claude-exports'
    folder.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r'[^\w\-]+', '-', title, flags=re.U).strip('-')[:50] or 'chat'
    path = folder / f'{slug}-{time.strftime("%Y%m%d-%H%M%S")}.{fmt}'
    text = render_html(title, entries) if fmt == 'html' else render_md(title, entries)
    path.write_text(text, encoding='utf-8')
    return path
