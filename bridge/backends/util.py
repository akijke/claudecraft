import json
import os


def save_json(path, data):
    """Atomic write, readable only by the owner (state may contain secrets)."""
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as f:
        os.chmod(temp, 0o600)
        json.dump(data, f, ensure_ascii=False, indent=2)
    temp.replace(path)
