"""Rewind (#20) and fork (#28) of an SDK conversation at one of its prompts.

The session transcript (the same JSONL Claude Code on the PC uses) is read with
get_session_messages; a branch is made with the SDK's offline fork_session(), cut
right before the chosen prompt (rewind) or right after that prompt's answer (fork).
The original session stays intact, so nothing is lost.

rewind: the chat continues on the shortened branch, and the files Claude changed in
        the dropped requests are restored from the undo checkpoints.
fork:   a NEW chat on a branch that ends after the chosen answer; files untouched.
"""


def _text(message):
    content = (message or {}).get('content')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get('type') == 'tool_result' for b in content):
            return None
        return '\n'.join(b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text')
    return None


def prompts(session_id, directory):
    """[(index, uuid, text, position in messages)] of the player's prompts, plus all messages."""
    from claude_agent_sdk import get_session_messages
    messages = get_session_messages(session_id, directory=directory)
    out = []
    for pos, m in enumerate(messages):
        if m.type != 'user' or m.parent_tool_use_id:
            continue
        text = _text(m.message)
        if text is None or not text.strip() or text.lstrip().startswith('<'):
            continue  # tool results and CLI reminders are not prompts
        out.append((len(out) + 1, m.uuid, text, pos))
    return out, messages


def branch(session_id, directory, number, after_answer):
    """New session id cut at prompt `number` (1-based): before it, or after its answer.

    Returns (new session id or None when nothing is left, dropped prompt texts).
    """
    from claude_agent_sdk import fork_session
    found, messages = prompts(session_id, directory)
    if not 1 <= number <= len(found):
        raise ValueError(f'Нет сообщения №{number} — всего {len(found)}')
    if after_answer:
        nxt = found[number][3] if number < len(found) else len(messages)
        cut = messages[nxt - 1].uuid
        dropped = [t for _, _, t, _ in found[number:]]
    else:
        pos = found[number - 1][3]
        if pos == 0:
            return None, [t for _, _, t, _ in found]  # back to an empty conversation
        cut = messages[pos - 1].uuid
        dropped = [t for _, _, t, _ in found[number - 1:]]
    result = fork_session(session_id, directory=directory, up_to_message_id=cut)
    return result.session_id, dropped
