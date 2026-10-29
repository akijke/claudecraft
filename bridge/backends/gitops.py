"""Git for the current project: /cc git [status|diff|log|commit|push|pull|...].

Local commands run inline; network commands (push, pull, fetch, clone, github)
run as background tasks and report back, so the bridge never blocks on them.
Credentials come from the system git credential helper / gh; prompts are off.
"""
import asyncio
import os
from pathlib import Path
import re
import shutil

COMMIT_PROMPT = ('Review the changes in this project (git status, git diff) and make a git commit with a '
                 'clear message (conventional commits, in English). Do not push.')


def https_url(remote):
    """git@github.com:owner/repo.git → https://github.com/owner/repo (for a clickable link)."""
    if not remote:
        return None
    m = re.match(r'git@([^:]+):(.+?)(\.git)?$', remote)
    if m:
        return f'https://{m.group(1)}/{m.group(2)}'
    if remote.startswith('http'):
        return re.sub(r'\.git$', '', re.sub(r'//[^@/]+@', '//', remote))
    return None


class Git:
    def __init__(self, bridge):
        self.bridge = bridge
        self.jobs = set()

    def folder(self):
        return Path(self.bridge.projects.path_of_current())

    async def run(self, *args, cwd=None, timeout=30, tool='git'):
        exe = shutil.which(tool)
        if not exe:
            raise ValueError(f'{tool} не найден в PATH' + (' — установи Git: git-scm.com' if tool == 'git' else
                                                         ' — установи GitHub CLI: cli.github.com'))
        env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GH_PROMPT_DISABLED='1', LC_ALL='C.UTF-8')
        process = await asyncio.create_subprocess_exec(
            exe, *args, cwd=str(cwd or self.folder()), env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(process.communicate(), timeout)
        except TimeoutError:
            process.kill()
            raise ValueError(f'{tool} {args[0]}: не завершилось за {timeout} с') from None
        return process.returncode, out.decode('utf-8', 'replace').rstrip()

    async def ok(self, *args, **kw):
        code, out = await self.run(*args, **kw)
        if code != 0:
            raise ValueError(f'git {args[0]}: ' + (out.strip().splitlines() or ['ошибка'])[-1][:400])
        return out

    def emit(self, mode, text, **extra):
        self.bridge.emit(self.bridge.current_chat(mode), text, **extra)

    def background(self, mode, title, coroutine):
        async def job():
            self.emit(mode, title + '…', kind='status')
            try:
                await coroutine
            except Exception as e:  # reported in chat, never crashes the bridge
                self.emit(mode, f'{title}: {e}', kind='error')
        task = asyncio.create_task(job())
        self.jobs.add(task)
        task.add_done_callback(self.jobs.discard)

    async def is_repo(self, folder=None):
        code, out = await self.run('rev-parse', '--is-inside-work-tree', cwd=folder)
        return code == 0 and out.strip() == 'true'

    # ---- dispatcher ----------------------------------------------------
    async def command(self, mode, arg):
        sub, _, rest = (arg or '').strip().partition(' ')
        sub, rest = sub.lower() or 'status', rest.strip()
        if sub in ('clone',):
            return await self.clone(mode, rest)
        if not self.folder().is_dir():
            raise ValueError(f'Папки проекта нет: {self.folder()}')
        if sub == 'init':
            await self.ok('init')
            self.emit(mode, f'Создан git-репозиторий в {self.folder()}', kind='status')
            return await self.status(mode)
        if not await self.is_repo():
            self.emit(mode, str(self.folder()), kind='git_none', project=self.bridge.projects.current)
            return
        handler = {
            'status': self.status, 'st': self.status, 'diff': self.diff, 'log': self.log,
            'add': self.add, 'commit': self.commit, 'push': self.push, 'pull': self.pull,
            'fetch': self.fetch, 'branch': self.branch, 'branches': self.branch,
            'switch': self.switch, 'checkout': self.switch, 'remote': self.remote,
            'github': self.github, 'stash': self.stash,
            'hunks': self.hunks, 'stage': self.stage_hunk, 'unstage': self.unstage_hunk,
            'graph': self.graph, 'conflicts': self.conflicts, 'pick': self.pick, 'resolve': self.resolve_with_claude,
        }.get(sub)
        if not handler:
            raise ValueError('git: status, diff, hunks, stage, unstage, conflicts, pick, resolve, graph, log, add, commit, push, pull, fetch, branch, '
                             'switch, remote, github, stash, init, clone')
        if handler in (self.status, self.push, self.pull, self.fetch):
            return await handler(mode)
        return await handler(mode, rest)

    # ---- local ---------------------------------------------------------
    async def status(self, mode):
        out = await self.ok('status', '--porcelain=v1', '-b', '-uall')
        lines = out.splitlines()
        head = lines[0][3:] if lines and lines[0].startswith('## ') else ''
        branch, upstream, ahead, behind = head, None, 0, 0
        m = re.match(r'(?:No commits yet on )?([^.\s]+)(?:\.\.\.(\S+))?(?: \[(.*)\])?', head)
        if m:
            branch, upstream = m.group(1), m.group(2)
            for part in (m.group(3) or '').split(','):
                part = part.strip()
                if part.startswith('ahead'):
                    ahead = int(part.split()[1])
                elif part.startswith('behind'):
                    behind = int(part.split()[1])
        files = [{'code': l[:2], 'path': l[3:].strip('"')} for l in lines[1:] if l.strip()][:60]
        code, remote = await self.run('remote', 'get-url', 'origin')
        code_last, last = await self.run('log', '-1', '--format=%h %s (%cr)')
        self.emit(mode, branch, kind='git_status', branch=branch, upstream=upstream, ahead=ahead,
                  behind=behind, files=files, more=max(0, len(lines) - 1 - 60),
                  remote=remote if code == 0 else None, url=https_url(remote) if code == 0 else None,
                  last=last if code_last == 0 else None, project=self.bridge.projects.current,
                  path=str(self.folder()))

    async def diff(self, mode, rest):
        staged = '--staged' in rest.split() or '--cached' in rest.split()
        path = ' '.join(p for p in rest.split() if p not in ('--staged', '--cached'))
        args = ['diff', '--no-color', '--no-ext-diff'] + (['--staged'] if staged else []) + (['--', path] if path else [])
        out = await self.ok(*args)
        if not out.strip() and not staged:
            out = await self.ok('diff', '--no-color', '--staged', *(['--', path] if path else []))
            staged = bool(out.strip())
        lines = out.splitlines()
        self.emit(mode, '\n'.join(lines[:400]), kind='diff', path=path, staged=staged,
                  truncated=len(lines) > 400, empty=not lines)

    # ---- hunks: stage / unstage one piece of a diff ----------------------------
    async def _diff_files(self, staged, path=''):
        from backends import hunks
        args = ['diff', '--no-color', '--no-ext-diff', '-U3'] + (['--cached'] if staged else []) + (['--', path] if path else [])
        return hunks.parse(await self.ok(*args))

    async def hunks(self, mode, rest):
        from backends import hunks
        path = ' '.join(p for p in rest.split() if not p.startswith('--'))
        unstaged = hunks.summary(await self._diff_files(False, path))
        staged = hunks.summary(await self._diff_files(True, path))
        self.emit(mode, path or 'все изменения', kind='git_hunks', path=path, unstaged=unstaged, staged=staged)

    async def _apply(self, hunk_id, staged, reverse):
        from backends import hunks
        import tempfile
        files = await self._diff_files(staged)
        patch, path = hunks.patch_for(files, hunk_id.strip())
        if patch is None:
            raise ValueError('Этот кусок уже изменился или применён — обнови список: /cc git hunks')
        with tempfile.NamedTemporaryFile('w', suffix='.patch', delete=False, encoding='utf-8', newline='\n') as f:
            f.write(patch)
            name = f.name
        try:
            await self.ok('apply', '--cached', '--recount', '--whitespace=nowarn', *(['-R'] if reverse else []), name)
        finally:
            os.unlink(name)
        return path

    async def stage_hunk(self, mode, rest):
        path = await self._apply(rest, staged=False, reverse=False)
        self.emit(mode, f'Кусок {path} добавлен в коммит (staged)', kind='status')
        await self.hunks(mode, '')

    async def unstage_hunk(self, mode, rest):
        path = await self._apply(rest, staged=True, reverse=True)
        self.emit(mode, f'Кусок {path} убран из коммита (unstaged)', kind='status')
        await self.hunks(mode, '')

    # ---- merge conflicts -----------------------------------------------------
    async def conflicted(self):
        out = await self.ok('diff', '--name-only', '--diff-filter=U')
        return [l.strip() for l in out.splitlines() if l.strip()]

    async def conflicts(self, mode, rest=''):
        from backends import conflicts
        files = []
        for name in await self.conflicted():
            path = self.folder() / name
            try:
                text = path.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            blocks = conflicts.parse(text)
            files.append({'path': name, 'blocks': [{k: (v[:12] if isinstance(v, list) else v) for k, v in b.items()}
                                                  for b in blocks]})
        code, merging = await self.run('rev-parse', '-q', '--verify', 'MERGE_HEAD')
        self.emit(mode, 'Конфликты', kind='git_conflicts', files=files, merging=code == 0)

    async def pick(self, mode, rest):
        """git pick <file> <block> ours|theirs|both"""
        from backends import conflicts
        parts = rest.rsplit(' ', 2)
        if len(parts) != 3 or not parts[1].isdigit():
            raise ValueError('git pick <файл> <номер> ours|theirs|both')
        name, index, choice = parts[0].strip().strip('"'), int(parts[1]), parts[2]
        path = (self.folder() / name).resolve()
        if not path.is_file() or self.folder().resolve() not in path.parents:
            raise ValueError(f'Нет такого файла в проекте: {name}')
        raw = path.read_bytes().decode('utf-8', 'replace')
        text = conflicts.resolve(raw, index, choice)
        path.write_text(text, encoding='utf-8', newline='')
        if not conflicts.parse(text):
            await self.ok('add', '--', name)
            self.emit(mode, f'{name}: конфликты разрешены, файл добавлен в коммит', kind='status')
        await self.conflicts(mode)

    async def resolve_with_claude(self, mode, rest=''):
        files = await self.conflicted()
        if not files:
            raise ValueError('Конфликтов нет')
        chat = self.bridge.current_chat('cc')
        await self.bridge.enqueue(chat, (
            'There is a merge conflict in this repository. Resolve the conflict markers in: ' + ', '.join(files)
            + '. For each block understand what both sides wanted and keep both intents (not just one side), '
            'remove all conflict markers, make sure the code still builds/tests pass if you can run them, then '
            '`git add` the resolved files. Do not commit; summarise each decision briefly.'))
        self.emit(mode, 'Claude разрешает конфликты: ' + ', '.join(files), kind='status')

    async def graph(self, mode, rest):
        n = int(rest) if rest.isdigit() else 40
        out = await self.ok('log', '--graph', '--all', f'-n{min(n, 200)}', '--color=never',
                            '--format=%x1f%h%x1f%d%x1f%s%x1f%an%x1f%cr')
        rows = []
        for line in out.splitlines():
            graph, _, rest_ = line.partition('\x1f')
            parts = rest_.split('\x1f') if rest_ else []
            if len(parts) >= 5:
                rows.append({'graph': graph, 'hash': parts[0], 'refs': parts[1].strip(' ()'), 'subject': parts[2],
                             'author': parts[3], 'when': parts[4]})
            else:
                rows.append({'graph': line, 'hash': '', 'refs': '', 'subject': '', 'author': '', 'when': ''})
        self.emit(mode, 'git graph', kind='git_graph', rows=rows)

    async def blame(self, path):
        """Per line of the committed file: hash, author, relative date, summary (for the editor's blame column)."""
        import time
        file = Path(path)
        code, out = await self.run('blame', '--line-porcelain', '--', file.name, cwd=file.parent, timeout=60)
        if code != 0:
            raise ValueError('blame: ' + (out.strip().splitlines() or ['не в git'])[-1][:300])
        lines, current = [], {}
        for line in out.split('\n'):
            if line.startswith('\t'):          # the source line closes one entry
                lines.append(current)
                current = {}
                continue
            key, _, value = line.partition(' ')
            if len(key) == 40 and all(c in '0123456789abcdef' for c in key):
                current['hash'] = key[:8]
            elif key in ('author', 'author-time', 'summary'):
                current[key] = value
        now = time.time()
        def ago(t):
            d = now - int(t or now)
            for unit, size in (('y', 31536000), ('mo', 2592000), ('d', 86400), ('h', 3600), ('m', 60)):
                if d >= size:
                    return f'{int(d // size)}{unit}'
            return 'now'
        uncommitted = '00000000'
        return [{'hash': l.get('hash', ''), 'author': 'not committed' if l.get('hash') == uncommitted else l.get('author', ''),
                 'ago': '' if l.get('hash') == uncommitted else ago(l.get('author-time')), 'summary': l.get('summary', '')}
                for l in lines]

    async def log(self, mode, rest):
        n = int(rest) if rest.isdigit() else 15
        out = await self.ok('log', f'-n{min(n, 50)}', '--format=%h%x09%an%x09%cr%x09%s')
        rows = []
        for line in out.splitlines():
            h, author, when, subject = (line.split('\t') + ['', '', '', ''])[:4]
            rows.append({'hash': h, 'author': author, 'when': when, 'subject': subject})
        self.emit(mode, 'git log', kind='git_log', rows=rows)

    async def add(self, mode, rest):
        await self.ok('add', '--', rest or '.')
        await self.status(mode)

    async def commit(self, mode, message):
        if not message:
            # "Write the message for me": hand it to Claude in the current project chat.
            chat = self.bridge.current_chat('cc')
            await self.bridge.enqueue(chat, self.bridge.prompt(COMMIT_PROMPT))
            self.emit(mode, 'Claude посмотрит изменения и сделает коммит.', kind='status')
            return
        await self.ok('add', '-A')
        out = await self.ok('commit', '-m', message)
        self.emit(mode, out.splitlines()[0] if out else 'Коммит создан', kind='status')
        await self.status(mode)

    async def branch(self, mode, name):
        if name:
            await self.ok('switch', '-c', name)
            self.emit(mode, f'Создана и выбрана ветка {name}', kind='status')
            return await self.status(mode)
        out = await self.ok('branch', '--format=%(HEAD)%09%(refname:short)%09%(upstream:short)')
        rows = []
        for line in out.splitlines():
            head, branch, upstream = (line.split('\t') + ['', '', ''])[:3]
            rows.append({'name': branch, 'current': head.strip() == '*', 'upstream': upstream})
        self.emit(mode, 'Ветки', kind='git_branches', rows=rows)

    async def switch(self, mode, name):
        if not name:
            raise ValueError('Формат: git switch <ветка>')
        await self.ok('switch', name)
        await self.status(mode)

    async def stash(self, mode, rest):
        out = await self.ok('stash', *(rest.split() or ['push']))
        self.emit(mode, out.splitlines()[0] if out else 'stash: готово', kind='status')
        await self.status(mode)

    @staticmethod
    def check_url(url):
        url = url.strip()
        if url.startswith('-') or '::' in url.split('/')[0] or not re.match(r'^(https://|ssh://|git@[\w.-]+:|git://)', url):
            raise ValueError('git: адрес — https://, ssh://, git@host: или git://')
        return url

    async def remote(self, mode, url):
        if url:
            url = self.check_url(url)
            code, _ = await self.run('remote', 'get-url', 'origin')
            await self.ok('remote', 'set-url' if code == 0 else 'add', 'origin', url)
            self.emit(mode, f'origin → {url}', kind='status')
        return await self.status(mode)

    # ---- network (background) ------------------------------------------
    async def push(self, mode):
        async def go():
            code, upstream = await self.run('rev-parse', '--abbrev-ref', '@{u}')
            args = ['push'] if code == 0 else ['push', '-u', 'origin', 'HEAD']
            out = await self.ok(*args, timeout=180)
            self.emit(mode, 'push: ' + (out.splitlines()[-1] if out else 'готово'), kind='status')
            await self.status(mode)
        self.background(mode, 'git push', go())

    async def pull(self, mode):
        async def go():
            out = await self.ok('pull', '--ff-only', timeout=180)
            self.emit(mode, 'pull: ' + (out.splitlines()[-1] if out else 'готово'), kind='status')
            await self.status(mode)
        self.background(mode, 'git pull', go())

    async def fetch(self, mode):
        async def go():
            await self.ok('fetch', '--prune', timeout=180)
            await self.status(mode)
        self.background(mode, 'git fetch', go())

    async def clone(self, mode, rest):
        url, _, name = rest.partition(' ')
        if not url:
            raise ValueError('Формат: git clone <url> [имя проекта]')
        url = self.check_url(url)
        name = (name.strip() or re.sub(r'\.git$', '', url.rstrip('/').split('/')[-1].split(':')[-1]))
        self.bridge.projects.check_name(name)
        target = self.bridge.workspace / name
        if target.exists():
            raise ValueError(f'Папка уже есть: {target}')

        async def go():
            await self.ok('clone', '--', url, str(target), cwd=self.bridge.workspace, timeout=600)
            self.bridge.projects.add(name, target)
            self.bridge.open_project(name)
            self.emit(mode, f'Склонировано в {target}; проект «{name}» открыт.', kind='status')
            await self.status(mode)
        self.background(mode, f'git clone {url}', go())

    async def github(self, mode, rest):
        """Create a GitHub repo for this project with gh and push (private by default)."""
        visibility = '--public' if rest.strip() == 'public' else '--private'
        name = self.bridge.projects.current
        if name == 'workspace':
            raise ValueError('Сначала открой проект: /cc project open <имя>')

        async def go():
            code, _ = await self.run('auth', 'status', tool='gh')
            if code != 0:
                raise ValueError('gh не авторизован: выполни gh auth login в терминале')
            code, _ = await self.run('remote', 'get-url', 'origin')
            if code == 0:
                raise ValueError('У проекта уже есть origin — используй git push')
            code, head = await self.run('rev-parse', 'HEAD')
            push = ['--push'] if code == 0 else []
            out = await self.ok('repo', 'create', name, visibility, '--source=.', '--remote=origin', *push,
                                tool='gh', timeout=180)
            self.emit(mode, 'GitHub: ' + (out.splitlines()[-1] if out else 'репозиторий создан'), kind='status')
            await self.status(mode)
        self.background(mode, f'gh repo create {name}', go())
