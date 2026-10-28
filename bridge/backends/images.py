"""Images for Claude: /cc img <file> [question], /cc shot (game screenshot from the mod).

The queue keeps plain text, so an image travels as a marker line `[[image:<path>]]`.
The SDK backend turns markers into image content blocks of the user message; the CLI
stores them in the session transcript, so the picture is also visible when the same
session is opened in Claude Code on the PC.
"""
import base64
import re
from pathlib import Path

MARK = re.compile(r'\[\[image:(.+?)\]\]\n?')
TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
         '.webp': 'image/webp'}
MAX_BYTES = 5 * 1024 * 1024  # Messages API limit per image
MAX_IMAGES = 8


def check(path):
    """Absolute path of a usable image or ValueError."""
    path = Path(path).expanduser()
    if not path.is_file():
        raise ValueError(f'Нет такого файла: {path}')
    if path.suffix.lower() not in TYPES:
        raise ValueError('Картинка должна быть PNG, JPG, GIF или WebP')
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('Картинка больше 5 МБ — уменьши её')
    return path.resolve()


def split_arg(text, exists=lambda p: Path(p).expanduser().is_file()):
    """'"C:/a b.png" what is this' → ('C:/a b.png', 'what is this')."""
    text = text.strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        if end > 0:
            return text[1:end], text[end + 1:].strip()
    # unquoted: the longest prefix that is an existing file (paths may contain spaces)
    words = text.split(' ')
    for n in range(len(words), 0, -1):
        candidate = ' '.join(words[:n])
        if exists(candidate):
            return candidate, ' '.join(words[n:]).strip()
    return words[0], ' '.join(words[1:]).strip()


def marked(paths, question):
    return ''.join(f'[[image:{p}]]\n' for p in paths) + question


def parse(text):
    """(text without markers, [paths])."""
    paths = MARK.findall(text)
    return MARK.sub('', text).strip(), paths


def display(text):
    """Text for chat lines and titles: markers become a short tag."""
    return MARK.sub(lambda m: '[image ' + Path(m.group(1)).name + '] ', text).strip()


def user_message(text):
    """Plain prompt (for echo matching) and the SDK stream-json user message, or None without images."""
    plain, paths = parse(text)
    if not paths:
        return plain, None
    if not plain:
        plain = 'Посмотри на изображение.'
    content = []
    for raw in paths[:MAX_IMAGES]:
        path = Path(raw)
        try:
            data = base64.b64encode(path.read_bytes()).decode('ascii')
        except OSError as e:
            raise ValueError(f'Не удалось прочитать картинку {path}: {e}')
        content.append({'type': 'image', 'source': {'type': 'base64',
                        'media_type': TYPES.get(path.suffix.lower(), 'image/png'), 'data': data}})
    content.append({'type': 'text', 'text': plain})
    return plain, {'type': 'user', 'message': {'role': 'user', 'content': content}, 'parent_tool_use_id': None}
