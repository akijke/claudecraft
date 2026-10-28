"""Hunks of `git diff` for staging/unstaging one piece at a time.

A hunk id is a hash of its file, header and lines, so a click on an old card fails
cleanly ("the diff changed") instead of staging something else.
"""
import hashlib


def parse(diff_text):
    """[{'path', 'head': [...file header lines], 'hunks': [{'id', 'header', 'lines'}]}]"""
    files, current, hunk = [], None, None
    for line in diff_text.split('\n'):
        if line.startswith('diff --git '):
            current = {'path': line.split(' b/', 1)[-1], 'head': [line], 'hunks': [], 'binary': False}
            files.append(current)
            hunk = None
        elif current is None:
            continue
        elif line.startswith('@@'):
            hunk = {'header': line, 'lines': []}
            current['hunks'].append(hunk)
        elif hunk is None:
            current['head'].append(line)
            if line.startswith('+++ b/'):
                current['path'] = line[6:]
            if line.startswith('Binary files'):
                current['binary'] = True
        elif line == '' and hunk is not None:
            continue  # the empty last line of the output
        else:
            hunk['lines'].append(line)
    for f in files:
        for h in f['hunks']:
            h['id'] = hashlib.sha1('\n'.join([f['path'], h['header']] + h['lines']).encode()).hexdigest()[:10]
    return files


def patch_for(files, hunk_id):
    """A patch with only this hunk (file header + hunk), or None."""
    for f in files:
        for h in f['hunks']:
            if h['id'] == hunk_id:
                return '\n'.join(f['head'] + [h['header']] + h['lines']) + '\n', f['path']
    return None, None


def summary(files, limit_lines=30):
    out = []
    for f in files:
        for h in f['hunks']:
            added = sum(1 for l in h['lines'] if l.startswith('+'))
            removed = sum(1 for l in h['lines'] if l.startswith('-'))
            out.append({'id': h['id'], 'path': f['path'], 'header': h['header'], 'added': added, 'removed': removed,
                        'lines': h['lines'][:limit_lines], 'more': max(0, len(h['lines']) - limit_lines)})
    return out
