"""Full-text search over the project's conversations (/cc search <text>).

Looks through every Claude Code session of the project folder (the same
transcripts the Claude Code app shows, including sessions started on the PC) and
the bridge's Messages-API chats. Case-insensitive; "quoted phrases" are matched
as a whole, other words must all appear in the same message.
"""
import re
import shlex


def _terms(query):
    try:
        parts = shlex.split(query)
    except ValueError:
        parts = query.split()
    return [p.lower() for p in parts if p.strip()]


def _texts(message):
    content = (message or {}).get('content')
    if isinstance(content, str):
        return [content]
    out = []
    for b in content or []:
        if isinstance(b, dict) and b.get('type') == 'text' and b.get('text'):
            out.append(b['text'])
    return out


def _snippet(text, term, width=140):
    i = text.lower().find(term)
    start = max(0, i - width // 3)
    snippet = text[start:start + width].replace('\n', ' ')
    return ('…' if start else '') + snippet + ('…' if start + width < len(text) else '')


def search(query, directory, bridge_chats, limit=40, sessions_limit=200):
    from claude_agent_sdk import get_session_messages, list_sessions
    terms = _terms(query)
    if not terms:
        raise ValueError('Что искать?')
    by_session = {c.sdk_id: c for c in bridge_chats if c.sdk_id}
    results = []

    def check(text):
        low = text.lower()
        return all(t in low for t in terms)

    for info in list_sessions(directory=directory, limit=sessions_limit):
        try:
            messages = get_session_messages(info.session_id, directory=directory)
        except Exception:
            continue
        for number, m in enumerate(messages):
            if m.type not in ('user', 'assistant') or m.parent_tool_use_id:
                continue
            for text in _texts(m.message):
                if text.lstrip().startswith('<') or not check(text):
                    continue
                chat = by_session.get(info.session_id)
                results.append({'session': info.session_id, 'chat': chat.id if chat else None,
                                'title': (chat.title if chat else None) or info.custom_title or info.summary
                                or (info.first_prompt or '')[:60],
                                'role': m.type, 'snippet': _snippet(text, terms[0]),
                                'when': info.last_modified})
                break
            if len(results) >= limit:
                return results
    for chat in bridge_chats:  # Messages-API chats keep their history in the bridge
        if chat.backend != 'api':
            continue
        for m in chat.messages:
            for text in _texts(m):
                if check(text):
                    results.append({'session': None, 'chat': chat.id, 'title': chat.title, 'role': m.get('role'),
                                    'snippet': _snippet(text, terms[0]), 'when': chat.updated})
                    break
    return results[:limit]
