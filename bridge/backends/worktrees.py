"""Git worktrees for parallel tasks (/cc spawn): each task edits its own checkout.

spawn in a git project → `git worktree add <project>/../.claudecraft-worktrees/<name>-<id> -b cc/<id>`;
the spawned chat works there (chat.worktree). When it finishes, the player gets
merge / diff / drop buttons:
  merge — commit everything in the worktree, `git merge --no-ff` into the project's
          current branch, then remove the worktree and branch. A conflict aborts the
          merge and leaves everything as it was (the worktree stays).
  diff  — what the task changed (including new files).
  drop  — remove the worktree and its branch without merging.
"""
import re
import uuid
from pathlib import Path


class Worktrees:
    def __init__(self, bridge):
        self.bridge = bridge

    @property
    def git(self):
        return self.bridge.git

    async def create(self, chat, project):
        """Gives `chat` a fresh worktree of `project`; returns its path or None (not a repo / no commit)."""
        project = Path(project)
        if not await self.git.is_repo(project):
            return None
        code, _ = await self.git.run('rev-parse', '--verify', 'HEAD', cwd=project)
        if code != 0:
            return None  # no commit yet: nothing to branch from
        top = Path((await self.git.ok('rev-parse', '--show-toplevel', cwd=project)).strip())
        short = uuid.uuid4().hex[:6]
        branch = f'cc/{re.sub(r"[^a-z0-9]+", "-", chat.title.lower())[:30].strip("-") or "task"}-{short}'
        folder = top.parent / '.claudecraft-worktrees' / f'{top.name}-{short}'
        folder.parent.mkdir(parents=True, exist_ok=True)
        await self.git.ok('worktree', 'add', '-b', branch, str(folder), 'HEAD', cwd=top)
        # The project may be a sub-folder of the repository: keep the same sub-folder.
        inner = folder / project.resolve().relative_to(top.resolve())
        chat.worktree, chat.wt_branch, chat.wt_repo = str(inner), branch, str(top)
        self.bridge.persist()
        return inner

    def owned(self, chat):
        if not getattr(chat, 'worktree', None):
            raise ValueError('У этого чата нет своего worktree')
        return Path(chat.wt_repo), Path(chat.worktree), chat.wt_branch

    def _root(self, inner, repo):
        """Top folder of the worktree (chat.worktree may be a sub-folder)."""
        path = Path(inner)
        while path.parent.name != '.claudecraft-worktrees' and path != path.parent:
            path = path.parent
        return path

    async def _commit_all(self, chat):
        repo, inner, branch = self.owned(chat)
        root = self._root(inner, repo)
        await self.git.ok('add', '-A', cwd=root)
        code, _ = await self.git.run('diff', '--cached', '--quiet', cwd=root)
        if code != 0:
            await self.git.ok('commit', '-m', f'spawn: {chat.title}'[:200], cwd=root)
        return repo, root, branch

    async def diff(self, chat):
        repo, root, branch = await self._commit_all(chat)
        base = (await self.git.ok('merge-base', 'HEAD', branch, cwd=repo)).strip()
        return await self.git.ok('diff', '--no-color', f'{base}..{branch}', cwd=repo)

    async def merge(self, chat):
        repo, root, branch = await self._commit_all(chat)
        code, status = await self.git.run('status', '--porcelain', '--untracked-files=no', cwd=repo)
        if status.strip():
            raise ValueError('В основном проекте есть незакоммиченные изменения — закоммить или спрячь их (git stash) и повтори merge')
        code, out = await self.git.run('merge', '--no-ff', '--no-edit', branch, cwd=repo)
        if code != 0:
            await self.git.run('merge', '--abort', cwd=repo)
            conflicts = re.findall(r'CONFLICT \(.*?\): .*? in (\S+)', out)
            raise ValueError('Конфликт при слиянии — ничего не изменено. Файлы: ' + (', '.join(conflicts) or out[-300:]))
        await self.drop(chat)
        return out.strip().splitlines()[-1] if out.strip() else 'merged'

    async def drop(self, chat):
        repo, inner, branch = self.owned(chat)
        root = self._root(inner, repo)
        await self.git.run('worktree', 'remove', '--force', str(root), cwd=repo)
        await self.git.run('branch', '-D', branch, cwd=repo)
        chat.worktree = chat.wt_branch = chat.wt_repo = None
        self.bridge.persist()
