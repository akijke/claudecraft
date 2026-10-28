"""Usage ledger for the cost dashboard: one JSON line per model turn in usage.jsonl.

{"t": unix time, "day": "2026-10-29", "project": "...", "mode": "cc", "model": "claude-...",
 "tokens": n, "input": n, "output": n, "cost": usd}
aggregate() sums by day, project and model for the last N days. The file is
trimmed to the last 50 000 lines.
"""
import json
import time
from collections import defaultdict
from pathlib import Path

LIMIT = 50_000


class Ledger:
    def __init__(self, directory):
        self.file = Path(directory) / 'usage.jsonl'
        self.lines = 0

    def add(self, project, mode, model, usage, cost):
        usage = usage or {}
        inp = sum(int(usage.get(k) or 0) for k in ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens'))
        out = int(usage.get('output_tokens') or 0)
        row = {'t': round(time.time()), 'day': time.strftime('%Y-%m-%d'), 'project': project or '', 'mode': mode,
               'model': model or 'default', 'tokens': inp + out, 'input': inp, 'output': out, 'cost': round(float(cost or 0), 6)}
        try:
            with self.file.open('a', encoding='utf-8') as f:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
            self.lines += 1
            if self.lines % 1000 == 0:
                self._trim()
        except OSError:
            pass

    def _trim(self):
        rows = self.file.read_text(encoding='utf-8').splitlines()
        if len(rows) > LIMIT:
            self.file.write_text('\n'.join(rows[-LIMIT:]) + '\n', encoding='utf-8')

    def rows(self, days):
        since = time.time() - days * 86400
        out = []
        try:
            for line in self.file.read_text(encoding='utf-8').splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get('t', 0) >= since:
                    out.append(r)
        except OSError:
            pass
        return out

    def aggregate(self, days=14):
        rows = self.rows(days)
        by_day, by_project, by_model = defaultdict(lambda: [0, 0.0]), defaultdict(lambda: [0, 0.0]), defaultdict(lambda: [0, 0.0])
        for r in rows:
            for table, key in ((by_day, r['day']), (by_project, r['project'] or '—'), (by_model, r['model'])):
                table[key][0] += r['tokens']
                table[key][1] += r['cost']
        today = time.time()
        days_list = [time.strftime('%Y-%m-%d', time.localtime(today - i * 86400)) for i in range(days - 1, -1, -1)]
        pack = lambda t, keys: [{'key': k, 'tokens': t[k][0], 'cost': round(t[k][1], 4)} for k in keys]
        return {'days': pack(by_day, days_list),
                'projects': pack(by_project, sorted(by_project, key=lambda k: -by_project[k][1]))[:10],
                'models': pack(by_model, sorted(by_model, key=lambda k: -by_model[k][1]))[:10],
                'total': {'tokens': sum(r['tokens'] for r in rows), 'cost': round(sum(r['cost'] for r in rows), 4),
                          'turns': len(rows)}}
