"""Pull requests and issues through GitHub CLI (gh), from the project folder.

Reading (lists, CI checks, review comments) is done by the bridge with `gh … --json`.
Writing on GitHub (creating a PR, replying to reviews, pushing a branch for an issue)
is handed to Claude as a task, so every gh/git push command still goes through the
in-game permission card — nothing is published without the player's click.
"""
import json
import shutil

PR_FIELDS = 'number,title,headRefName,baseRefName,state,isDraft,url,author,reviewDecision,statusCheckRollup,updatedAt'


def summarize_checks(rollup):
    """CI checks → {'state': 'passing'|'failing'|'pending'|'none', 'failed': [...], 'total': n}."""
    rollup = rollup or []
    failed, pending = [], 0
    for c in rollup:
        conclusion = (c.get('conclusion') or c.get('state') or '').upper()
        status = (c.get('status') or '').upper()
        name = c.get('name') or c.get('context') or '?'
        if conclusion in ('FAILURE', 'ERROR', 'TIMED_OUT', 'CANCELLED', 'ACTION_REQUIRED'):
            failed.append(name)
        elif status in ('IN_PROGRESS', 'QUEUED', 'PENDING', 'WAITING') or conclusion in ('PENDING', 'EXPECTED', ''):
            pending += 1
    state = 'none' if not rollup else 'failing' if failed else 'pending' if pending else 'passing'
    return {'state': state, 'failed': failed[:10], 'total': len(rollup)}


class Pulls:
    def __init__(self, bridge):
        self.bridge = bridge

    async def gh_json(self, *args, cwd=None):
        if not shutil.which('gh'):
            raise ValueError('Нужен GitHub CLI (gh): cli.github.com, затем вход — /claude → GitHub')
        code, out = await self.bridge.git.run(*args, cwd=cwd, timeout=60, tool='gh')
        if code != 0:
            raise ValueError('gh: ' + (out.strip().splitlines() or ['ошибка'])[-1][:300])
        try:
            return json.loads(out or 'null')
        except ValueError:
            raise ValueError('gh вернул не JSON: ' + out[:200]) from None

    async def list_prs(self, folder):
        rows = await self.gh_json('pr', 'list', '--state', 'open', '--limit', '30', '--json', PR_FIELDS, cwd=folder)
        out = []
        for r in rows or []:
            out.append({'number': r['number'], 'title': r.get('title', ''), 'branch': r.get('headRefName', ''),
                        'base': r.get('baseRefName', ''), 'draft': r.get('isDraft', False), 'url': r.get('url', ''),
                        'author': (r.get('author') or {}).get('login', ''), 'review': r.get('reviewDecision') or '',
                        'checks': summarize_checks(r.get('statusCheckRollup'))})
        return out

    async def view_pr(self, folder, number):
        pr = await self.gh_json('pr', 'view', str(number), '--json',
                                PR_FIELDS + ',body,reviews,comments', cwd=folder)
        # inline review comments are only in the REST API
        try:
            inline = await self.gh_json('api', f'repos/{{owner}}/{{repo}}/pulls/{number}/comments', cwd=folder)
        except ValueError:
            inline = []
        comments = [{'author': (c.get('author') or {}).get('login', ''), 'body': c.get('body', '')[:500], 'where': ''}
                    for c in pr.get('comments') or []]
        comments += [{'author': (r.get('author') or {}).get('login', ''), 'body': r.get('body', '')[:500],
                      'where': r.get('state', '')} for r in pr.get('reviews') or [] if r.get('body')]
        comments += [{'author': (c.get('user') or {}).get('login', ''), 'body': c.get('body', '')[:500],
                      'where': f"{c.get('path', '')}:{c.get('line') or c.get('original_line') or ''}"} for c in inline or []]
        return {'number': pr['number'], 'title': pr.get('title', ''), 'url': pr.get('url', ''), 'state': pr.get('state', ''),
                'branch': pr.get('headRefName', ''), 'base': pr.get('baseRefName', ''), 'draft': pr.get('isDraft', False),
                'review': pr.get('reviewDecision') or '', 'body': (pr.get('body') or '')[:1500],
                'checks': summarize_checks(pr.get('statusCheckRollup')), 'comments': comments[:40]}

    async def list_issues(self, folder):
        rows = await self.gh_json('issue', 'list', '--state', 'open', '--limit', '30',
                                  '--json', 'number,title,labels,assignees,url,updatedAt', cwd=folder)
        return [{'number': r['number'], 'title': r.get('title', ''), 'url': r.get('url', ''),
                 'labels': [l.get('name', '') for l in r.get('labels') or []]} for r in rows or []]

    async def view_issue(self, folder, number):
        r = await self.gh_json('issue', 'view', str(number), '--json', 'number,title,body,labels,url,comments', cwd=folder)
        return {'number': r['number'], 'title': r.get('title', ''), 'url': r.get('url', ''),
                'body': (r.get('body') or '')[:4000],
                'comments': [(c.get('author') or {}).get('login', '') + ': ' + (c.get('body') or '')[:400]
                             for c in r.get('comments') or []][:10]}


def pr_create_prompt(base):
    return ('Prepare a pull request for the current branch' + (f' into {base}' if base else '') + '. '
            'Look at `git log` and `git diff` against the base branch, write a clear title and a description '
            '(what changed, why, how it was tested, risks), push the branch if it has no upstream, then run '
            '`gh pr create --draft` with that title and body (use --body-file with a temporary file). '
            'Report the PR URL.')


def pr_reply_prompt(pr):
    lines = '\n'.join(f"- {c['author']}{' (' + c['where'] + ')' if c['where'] else ''}: {c['body']}" for c in pr['comments'])
    return (f"Address the review comments on pull request #{pr['number']} \"{pr['title']}\" (branch {pr['branch']}).\n"
            f"Comments:\n{lines or '(no comments)'}\n\n"
            'Check out the branch if needed, make the requested changes, run the tests, commit with a message that '
            'references the comments and push. Then reply on the PR with `gh pr comment` summarising what you changed '
            'for each comment. Ask me with AskUserQuestion when a comment is unclear or you disagree.')


def issue_prompt(issue):
    comments = '\n'.join('- ' + c for c in issue['comments'])
    return (f"Work on GitHub issue #{issue['number']}: {issue['title']}\n\n{issue['body']}\n"
            + (f"\nComments:\n{comments}\n" if comments else '') +
            f"\nCreate a branch named issue-{issue['number']}-<short-slug> from the default branch, implement it, "
            'add or update tests, run them, commit, push the branch and open a draft PR with '
            f"`gh pr create --draft` whose body says \"Closes #{issue['number']}\". Report the PR URL.")
