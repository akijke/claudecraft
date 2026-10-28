"""Catalog of ready MCP connectors for Claude Code, switchable from the game.

Each entry is either a local stdio server started with npx (needs Node.js) or a
remote HTTP server. Secrets (tokens, keys, connection strings) are kept in
secrets.json (owner-only) and are never echoed into chat events. Enabled
connectors are merged into the MCP servers of the next /cc request; the CLI
reports per-server status in its init message, shown in /cc connectors.
"""
import json
import os
import shutil
import subprocess
from backends.util import save_json

# vars: required secrets (name -> hint); optional: may be empty.
CATALOG = {
    # --- code & git -------------------------------------------------------
    'github': {'name': 'GitHub', 'group': 'Код и git', 'type': 'http',
               'url': 'https://api.githubcopilot.com/mcp/',
               'headers': {'Authorization': 'Bearer ${GITHUB_TOKEN}'},
               'vars': {'GITHUB_TOKEN': 'github.com/settings/tokens (или берётся из gh auth token)'},
               'about': 'Issues, PR, ревью, Actions, поиск по репозиториям'},
    'gitlab': {'name': 'GitLab', 'group': 'Код и git', 'package': '@modelcontextprotocol/server-gitlab',
               'vars': {'GITLAB_PERSONAL_ACCESS_TOKEN': 'gitlab.com → Preferences → Access tokens'},
               'optional': {'GITLAB_API_URL': 'для своего сервера, напр. https://git.company/api/v4'},
               'about': 'Проекты, merge requests, issues'},
    'sentry': {'name': 'Sentry', 'group': 'Код и git', 'package': '@sentry/mcp-server',
               'vars': {'SENTRY_ACCESS_TOKEN': 'sentry.io → Settings → Auth Tokens'},
               'about': 'Ошибки и трейсы из продакшена'},
    'circleci': {'name': 'CircleCI', 'group': 'Код и git', 'package': '@circleci/mcp-server-circleci',
                 'vars': {'CIRCLECI_TOKEN': 'app.circleci.com → User settings → Personal API Tokens'},
                 'about': 'Логи падений сборок CI'},
    'jetbrains': {'name': 'JetBrains IDE', 'group': 'Код и git', 'package': '@jetbrains/mcp-proxy',
                  'optional': {'IDE_PORT': 'порт встроенного веб-сервера IDE (обычно не нужен)'},
                  'about': 'Связь с открытой IntelliJ IDEA/PyCharm (нужен плагин MCP Server в IDE)'},
    # --- browser & web ----------------------------------------------------
    'playwright': {'name': 'Playwright', 'group': 'Браузер и веб', 'package': '@playwright/mcp@latest',
                   'about': 'Управление браузером: открыть сайт, кликать, заполнять, скриншоты'},
    'chrome-devtools': {'name': 'Chrome DevTools', 'group': 'Браузер и веб',
                        'package': 'chrome-devtools-mcp@latest', 'args': ['--no-usage-statistics'],
                        'about': 'Отладка веб-страниц: консоль, сеть, производительность'},
    'firecrawl': {'name': 'Firecrawl', 'group': 'Браузер и веб', 'package': 'firecrawl-mcp',
                  'vars': {'FIRECRAWL_API_KEY': 'firecrawl.dev → API keys'},
                  'about': 'Скачивание и разбор сайтов целиком'},
    # --- docs & search ----------------------------------------------------
    'context7': {'name': 'Context7', 'group': 'Документация и поиск', 'package': '@upstash/context7-mcp',
                 'optional': {'CONTEXT7_API_KEY': 'context7.com (необязательно, выше лимиты)'},
                 'arg_vars': {'CONTEXT7_API_KEY': '--api-key'},
                 'about': 'Актуальная документация библиотек и фреймворков'},
    'brave-search': {'name': 'Brave Search', 'group': 'Документация и поиск',
                     'package': '@brave/brave-search-mcp-server',
                     'vars': {'BRAVE_API_KEY': 'brave.com/search/api'}, 'about': 'Веб-поиск'},
    'exa': {'name': 'Exa', 'group': 'Документация и поиск', 'package': 'exa-mcp-server',
            'vars': {'EXA_API_KEY': 'dashboard.exa.ai'}, 'about': 'Поиск по коду и статьям для разработчиков'},
    # --- thinking & memory ------------------------------------------------
    'sequential-thinking': {'name': 'Sequential Thinking', 'group': 'Мышление и память',
                            'package': '@modelcontextprotocol/server-sequential-thinking',
                            'about': 'Пошаговое решение сложных задач'},
    'memory': {'name': 'Memory', 'group': 'Мышление и память', 'package': '@modelcontextprotocol/server-memory',
               'env': {'MEMORY_FILE_PATH': '${BRIDGE_DIR}/memory.jsonl'},
               'about': 'Долгая память: факты о проектах между сессиями'},
    # --- databases --------------------------------------------------------
    'postgres': {'name': 'PostgreSQL', 'group': 'Базы данных', 'package': '@modelcontextprotocol/server-postgres',
                 'vars': {'POSTGRES_URL': 'postgresql://user:pass@host:5432/db'},
                 'args': ['${POSTGRES_URL}'], 'about': 'Схема и SELECT-запросы (только чтение)'},
    'mongodb': {'name': 'MongoDB', 'group': 'Базы данных', 'package': 'mongodb-mcp-server',
                'vars': {'MDB_MCP_CONNECTION_STRING': 'mongodb+srv://…'}, 'about': 'Коллекции и запросы'},
    'supabase': {'name': 'Supabase', 'group': 'Базы данных', 'package': '@supabase/mcp-server-supabase',
                 'vars': {'SUPABASE_ACCESS_TOKEN': 'supabase.com/dashboard/account/tokens'},
                 'about': 'Проекты, таблицы, миграции, edge-функции'},
    'neon': {'name': 'Neon', 'group': 'Базы данных', 'package': '@neondatabase/mcp-server-neon',
             'vars': {'NEON_API_KEY': 'console.neon.tech → Account → API keys'},
             'args': ['start', '${NEON_API_KEY}'], 'about': 'Serverless Postgres: базы, ветки, запросы'},
    # --- cloud & devops ---------------------------------------------------
    'kubernetes': {'name': 'Kubernetes', 'group': 'Облака и DevOps', 'package': 'mcp-server-kubernetes',
                   'about': 'Кластеры из ~/.kube/config: поды, логи, деплой'},
    'railway': {'name': 'Railway', 'group': 'Облака и DevOps', 'package': '@railway/mcp-server',
                'about': 'Деплой на Railway (нужен railway login)'},
    'heroku': {'name': 'Heroku', 'group': 'Облака и DevOps', 'package': '@heroku/mcp-server',
               'vars': {'HEROKU_API_KEY': 'heroku authorizations:create'}, 'about': 'Приложения, логи, dyno'},
    'stripe': {'name': 'Stripe', 'group': 'Облака и DevOps', 'package': '@stripe/mcp',
               'vars': {'STRIPE_SECRET_KEY': 'dashboard.stripe.com/apikeys (лучше тестовый ключ)'},
               'args': ['--tools=all', '--api-key=${STRIPE_SECRET_KEY}'], 'about': 'Платежи, клиенты, подписки'},
    # --- browser sign-in (OAuth): no keys, the player logs in on the service's site ---
    'linear': {'name': 'Linear', 'group': 'Вход через браузер', 'type': 'http', 'auth': 'oauth',
               'url': 'https://mcp.linear.app/mcp', 'about': 'Задачи, проекты и циклы Linear'},
    'atlassian': {'name': 'Jira и Confluence', 'group': 'Вход через браузер', 'type': 'sse', 'auth': 'oauth',
                  'url': 'https://mcp.atlassian.com/v1/sse', 'about': 'Задачи Jira, страницы Confluence (Atlassian Cloud)'},
    'notion-oauth': {'name': 'Notion (вход)', 'group': 'Вход через браузер', 'type': 'http', 'auth': 'oauth',
                     'url': 'https://mcp.notion.com/mcp', 'about': 'Notion без токена интеграции'},
    'sentry-oauth': {'name': 'Sentry (вход)', 'group': 'Вход через браузер', 'type': 'http', 'auth': 'oauth',
                     'url': 'https://mcp.sentry.dev/mcp', 'about': 'Ошибки и трейсы, без auth-токена'},
    'asana': {'name': 'Asana', 'group': 'Вход через браузер', 'type': 'sse', 'auth': 'oauth',
              'url': 'https://mcp.asana.com/sse', 'about': 'Задачи и проекты Asana'},
    'vercel': {'name': 'Vercel', 'group': 'Вход через браузер', 'type': 'http', 'auth': 'oauth',
               'url': 'https://mcp.vercel.com', 'about': 'Деплои, логи, проекты Vercel'},
    # --- work tools -------------------------------------------------------
    'notion': {'name': 'Notion', 'group': 'Работа и дизайн', 'package': '@notionhq/notion-mcp-server',
               'vars': {'NOTION_TOKEN': 'notion.so/profile/integrations'}, 'about': 'Страницы и базы Notion'},
    'slack': {'name': 'Slack', 'group': 'Работа и дизайн', 'package': '@modelcontextprotocol/server-slack',
              'vars': {'SLACK_BOT_TOKEN': 'api.slack.com/apps → OAuth tokens', 'SLACK_TEAM_ID': 'ID воркспейса (T…)'},
              'about': 'Каналы, сообщения, треды'},
    'figma': {'name': 'Figma', 'group': 'Работа и дизайн', 'package': 'figma-developer-mcp', 'args': ['--stdio'],
              'vars': {'FIGMA_API_KEY': 'figma.com → Settings → Personal access tokens'},
              'about': 'Макеты Figma для вёрстки'},
}


def load_secrets(directory):
    path = directory / 'secrets.json'
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def store_secrets(directory, data):
    path = directory / 'secrets.json'
    if any(data.values()):
        save_json(path, data)
    elif path.exists():
        path.unlink()


class Connectors:
    def __init__(self, bridge):
        self.bridge = bridge
        self.file = bridge.directory / 'connectors.json'
        data = json.loads(self.file.read_text(encoding='utf-8')) if self.file.exists() else {}
        self.enabled = [c for c in data.get('enabled', []) if c in CATALOG]
        self.status = {}  # name -> status from the CLI init message

    def save(self):
        save_json(self.file, {'enabled': self.enabled})

    def secrets(self, cid):
        return load_secrets(self.bridge.directory).get('connectors', {}).get(cid, {})

    def set_secrets(self, cid, values):
        data = load_secrets(self.bridge.directory)
        mine = data.setdefault('connectors', {}).setdefault(cid, {})
        allowed = set(CATALOG[cid].get('vars', {})) | set(CATALOG[cid].get('optional', {}))
        for key, value in values.items():
            if key not in allowed:
                raise ValueError(f'{cid}: неизвестный параметр {key}')
            value = (value or '').strip()
            if len(value) > 4000 or '\n' in value:
                raise ValueError(f'{key}: слишком длинное или многострочное значение')
            if value:
                mine[key] = value
            else:
                mine.pop(key, None)
        store_secrets(self.bridge.directory, data)

    @staticmethod
    def is_oauth(cid):
        return CATALOG.get(cid, {}).get('auth') == 'oauth'

    def missing(self, cid):
        if self.is_oauth(cid):
            return [] if self.bridge.oauth.has_token(cid) else ['sign-in']
        have = self.secrets(cid)
        return [v for v in CATALOG[cid].get('vars', {}) if not have.get(v)]

    def gh_token(self):
        exe = shutil.which('gh')
        if not exe:
            return None
        try:
            out = subprocess.run([exe, 'auth', 'token'], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        token = out.stdout.strip()
        return token if out.returncode == 0 and token else None

    def connect(self, cid, values=None):
        if cid not in CATALOG:
            raise ValueError(f'Нет коннектора {cid}. Список: /cc connectors')
        if values:
            self.set_secrets(cid, values)
        if cid == 'github' and self.missing(cid):
            token = self.gh_token()
            if token:
                self.set_secrets(cid, {'GITHUB_TOKEN': token})
        missing = self.missing(cid)
        if missing:
            return missing
        if 'package' in CATALOG[cid] and not shutil.which('npx'):
            raise ValueError('Нужен Node.js (npx) — установи с nodejs.org и перезапусти мост')
        if cid not in self.enabled:
            self.enabled.append(cid)
            self.save()
        return []

    def disconnect(self, cid):
        if cid in self.enabled:
            self.enabled.remove(cid)
            self.save()

    def servers(self):
        """MCP server configs for enabled connectors (secrets substituted)."""
        result = {}
        windows = os.name == 'nt'
        for cid in self.enabled:
            spec, values = CATALOG[cid], dict(self.secrets(cid))
            values['BRIDGE_DIR'] = str(self.bridge.directory)
            sub = lambda s: _substitute(s, values)
            if self.is_oauth(cid):
                token = self.bridge.oauth.stored(cid).get('access_token')
                if token:  # not signed in (yet): the server is left out instead of failing
                    result[cid] = {'type': spec['type'], 'url': spec['url'],
                                   'headers': {'Authorization': 'Bearer ' + token}}
                continue
            if spec.get('type') == 'http':
                result[cid] = {'type': 'http', 'url': spec['url'],
                               'headers': {k: sub(v) for k, v in spec.get('headers', {}).items()}}
                continue
            args = ['-y', spec['package']] + [sub(a) for a in spec.get('args', [])]
            for var, flag in spec.get('arg_vars', {}).items():
                if values.get(var):
                    args += [flag, values[var]]
            env = {k: sub(v) for k, v in spec.get('env', {}).items()}
            for var in list(spec.get('vars', {})) + list(spec.get('optional', {})):
                if values.get(var) and var not in spec.get('arg_vars', {}):
                    env[var] = values[var]
            # On Windows npx is a .cmd script: start it through cmd /c.
            command, args = (('cmd', ['/c', 'npx'] + args) if windows else ('npx', args))
            result[cid] = {'command': command, 'args': args, **({'env': env} if env else {})}
        return result

    def rows(self):
        node = bool(shutil.which('npx'))
        rows = []
        for cid, spec in CATALOG.items():
            rows.append({'id': cid, 'name': spec['name'], 'group': spec['group'], 'about': spec['about'],
                         'enabled': cid in self.enabled, 'missing': self.missing(cid),
                         'needs_node': 'package' in spec and not node,
                         'oauth': self.is_oauth(cid),
                         'status': self.status.get(cid)})
        return rows

    def fields(self, cid):
        spec = CATALOG[cid]
        have = self.secrets(cid)
        return [{'var': var, 'hint': hint, 'required': True, 'set': bool(have.get(var))}
                for var, hint in spec.get('vars', {}).items()] + \
               [{'var': var, 'hint': hint, 'required': False, 'set': bool(have.get(var))}
                for var, hint in spec.get('optional', {}).items()]


def _substitute(text, values):
    for key, value in values.items():
        text = text.replace('${' + key + '}', value)
    return text
