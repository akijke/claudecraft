"""Language of bridge messages (English by default, Russian optional).

Bridge code writes its messages in Russian; they are translated in ONE place,
when an event or an error leaves the bridge. Templates use {} for variable parts;
variable parts are translated recursively (e.g. "Ошибка: Нет такой папки: X").
Claude's own answers, file contents, diffs and paths are never translated.
The language comes from the mod's settings file (config/claude-chat/client.json).
"""
import json
import re

EN = {
    # --- generic -----------------------------------------------------------
    'Новый чат': 'New chat', 'Думаю…': 'Thinking…', 'думает…': 'thinking…', 'Готово': 'Done',
    'Отмена': 'Cancel', 'нет': 'none', 'чат': 'chat', 'ошибка': 'error', 'готово': 'done',
    'Ошибка: {}': 'Error: {}', 'Проверь вход в аккаунт Claude: /c settings': 'Check your Claude login: /c settings',
    'Текущий запрос прерван.': 'Current request stopped.',
    'Остановлено; очередь очищена. История сохранена.': 'Stopped; queue cleared. History kept.',
    'Очередь очищена.': 'Queue cleared.', 'В очереди: {}': 'Queued: {}', 'Новый чат создан.': 'New chat created.',
    'Выбери чат:': 'Pick a chat:', 'Question must contain 1..8000 characters': 'Message must be 1..8000 characters',
    'Queue is full (20)': 'Queue is full (20)',
    'Расширения и коннекторы перезагружены для следующих запросов: {}':
        'Extensions and connectors reloaded for the next requests: {}',
    'Claude перезагрузил расширения: {}': 'Claude reloaded extensions: {}',
    'Чат не найден (нужен id из /cc tasks)': 'Chat not found (use an id from /cc tasks)',
    # --- status line -------------------------------------------------------
    '{} | {} | {} | очередь {} | auto {} | {}': '{} | {} | {} | queue {} | auto {} | {}',
    'ожидание': 'idle', 'работает': 'working', 'работает, f доступно': 'working, f available',
    'проект {} · {}': 'project {} · {}', 'ждёт ответа': 'waiting for you', 'в очереди': 'queued',
    # --- f / queue ---------------------------------------------------------
    'Messages API: уже отправленный HTTP-запрос нельзя дополнить сообщением. Текущий ответ НЕ отменён и '
    'продолжается. Используй q (очередь) или cancel, затем новый запрос. Для f выбери локальный чат: /c new local.':
        'Messages API: a request already sent cannot be extended. The current answer was NOT cancelled and '
        'continues. Use q (queue) or cancel and send again. For f, use a local chat: /c new local.',
    'Сейчас нет живой сессии для f: сообщение выполнится следующим, без отмены текущей работы.':
        'No live session for f right now: the message runs next, the current work is not cancelled.',
    'Сообщение передано Claude, работа продолжается.': 'Message delivered to Claude, work continues.',
    'Claude уже закончил ответ до получения f: сообщение выполнится следующим запросом в этой же сессии.':
        'Claude finished before f arrived: it runs as the next request in the same session.',
    'f не доставлено: {}': 'f not delivered: {}',
    'f будет передано, как только Claude начнёт отвечать…': 'f will be delivered as soon as Claude starts answering…',
    'Этот Claude CLI не подтверждает сообщения f; ответ на уже отправленные f может не показаться. Обнови Claude Code.':
        'This Claude CLI does not acknowledge f messages; answers to them may not show. Update Claude Code.',
    'Этот Claude CLI не подтверждает входящие сообщения (--replay-user-messages), поэтому f нельзя доставить без '
    'риска потерять ответ. Обнови claude-agent-sdk / Claude Code или используй q. Текущая работа продолжается.':
        'This Claude CLI does not acknowledge incoming messages (--replay-user-messages), so f cannot be delivered '
        'safely. Update claude-agent-sdk / Claude Code or use q. Current work continues.',
    'Обрабатываю дополнительное сообщение…': 'Handling the extra message…',
    # --- permissions & questions -------------------------------------------
    'Разрешить': 'Allow', 'Отклонить': 'Deny', 'Всегда разрешать {}': 'Always allow {}',
    'Нет ответа 10 минут — {} отклонён.': 'No answer for 10 minutes — {} denied.',
    '{} теперь разрешается без вопроса в этом чате (/cc auto off — сбросить).':
        '{} is now allowed without asking in this chat (/cc auto off to reset).',
    'Открой ссылку во внешнем браузере, затем нажми «Готово».': 'Open the link in your browser, then press “Done”.',
    'Завершил действие в браузере?': 'Done in the browser?',
    'Авторазрешение выключено: каждое действие спрашивает подтверждение.':
        'Auto-approve is off: every action asks first.',
    'Авторазрешение: чтение, поиск и правка файлов без вопросов; команды (Bash) и интернет спрашивают.':
        'Auto-approve: reading, searching and editing files without asking; commands (Bash) and web still ask.',
    'Авторазрешение ВСЕГО: Claude выполняет любые команды без вопросов, с правами моста. Будь осторожен.':
        'Auto-approve EVERYTHING: Claude runs any command without asking, with the bridge\'s rights. Be careful.',
    'Лимит подписки: {}': 'Subscription limit: {}', 'почти исчерпан': 'almost used up', 'исчерпан': 'used up',
    # --- tokens ------------------------------------------------------------
    'Токены': 'Tokens', 'Этот чат: {}': 'This chat: {}', 'Проект {}': 'Project {}', 'Все чаты': 'All chats',
    'Стоимость — оценка Claude Code по ценам API; на подписке списывается лимит, а не деньги.':
        'Cost is Claude Code\'s estimate at API prices; a subscription uses its limits, not money.',
    'Токены из ответов Messages API.': 'Tokens from Messages API responses.',
    # --- progress lines (describe.py) --------------------------------------
    'Читаю {}': 'Reading {}', 'Создаю {}': 'Creating {}', 'Редактирую {}': 'Editing {}',
    'Редактирую блокнот {}': 'Editing notebook {}', 'Ищу файлы {} в {}': 'Finding files {} in {}',
    'Ищу файлы {}': 'Finding files {}', 'Ищу «{}» в {}': 'Searching “{}” in {}', 'Ищу «{}»': 'Searching “{}”',
    'Смотрю папку {}': 'Listing {}', 'Ищу в интернете: {}': 'Web search: {}', 'Открываю {}': 'Fetching {}',
    'Обновляю план': 'Updating the plan', 'План': 'Plan', 'Агент {}: {}': 'Agent {}: {}', 'помощник': 'helper',
    'агент': 'agent', 'Задаю вопрос': 'Asking a question', 'Предлагаю открыть {}': 'Suggesting to open {}',
    'Перезагружаю расширения': 'Reloading extensions',
    # --- projects ----------------------------------------------------------
    'Проекты': 'Projects', 'Нет проекта {}. Список: /cc project': 'No project {}. List: /cc project',
    'Нет проекта {}': 'No project {}', 'Проект «{}» открыт. Claude работает в {}': 'Project “{}” opened. Claude works in {}',
    'Проект «{}» создан и открыт: {}': 'Project “{}” created and opened: {}', 'Новый проект «{}»: {}': 'New project “{}”: {}',
    'В проекте идёт работа — сначала cancel': 'Work is running in this project — cancel it first',
    'Проект «{}» убран из списка (файлы на диске не тронуты).': 'Project “{}” removed from the list (files untouched).',
    'project: list | open <имя> | here [имя] | add <имя> [путь] | new <имя> | rename <старое> <новое> | remove <имя>':
        'project: list | open <name> | here [name] | add <name> [path] | new <name> | rename <old> <new> | remove <name>',
    'Имя проекта: 1–40 символов, буквы, цифры, . _ -': 'Project name: 1–40 characters, letters, digits, . _ -',
    'Проект {} уже есть': 'Project {} already exists', 'Эта папка уже проект «{}»': 'This folder is already project “{}”',
    'Этот проект нельзя убрать': 'This project cannot be removed', 'workspace не переименовывается': 'workspace cannot be renamed',
    # --- files -------------------------------------------------------------
    'Нет такой папки: {}': 'No such folder: {}', 'Нет доступа: {}': 'Access denied: {}',
    'Формат: cat <файл> [начало-конец]': 'Usage: cat <file> [start-end]', 'Нет такого файла: {}': 'No such file: {}',
    'Это двоичный файл — не показываю': 'Binary file — not shown',
    'Формат: find <шаблон>, например find *.java или find Main': 'Usage: find <pattern>, e.g. find *.java or find Main',
    'Папка создана: {}': 'Folder created: {}', 'Файл создан: {}': 'File created: {}', 'Нет такого пути: {}': 'No such path: {}',
    'Уже существует: {}': 'Already exists: {}', 'Скопировано: {} → {}': 'Copied: {} → {}', 'Перемещено: {} → {}': 'Moved: {} → {}',
    'Слишком опасно удалять {}': 'Too dangerous to delete {}', 'Удалить {}? (внутри {} объектов)': 'Delete {}? ({} items inside)',
    'Удалить {}?': 'Delete {}?', 'Удалено: {}': 'Deleted: {}', 'Открыто в {}: {}': 'Opened in {}: {}', 'Открыто: {}': 'Opened: {}',
    'Не найден редактор (code, cursor, idea, subl) в PATH': 'No editor (code, cursor, idea, subl) found in PATH',
    'Нужны два пути: откуда и куда': 'Two paths are needed: from and to',
    'Файл больше 3 МБ — открой во внешнем редакторе': 'File is larger than 3 MB — open it in an external editor',
    'Файл изменился на диске после открытия (возможно, его правил Claude). Сохранить всё равно?':
        'The file changed on disk after it was opened (maybe Claude edited it). Save anyway?',
    'Что искать?': 'What should I search for?', 'Неверное регулярное выражение: {}': 'Invalid regular expression: {}',
    # --- git -----------------------------------------------------------------
    'git не найден в PATH — установи Git: git-scm.com': 'git not found in PATH — install Git: git-scm.com',
    'gh не найден в PATH — установи GitHub CLI: cli.github.com': 'gh not found in PATH — install GitHub CLI: cli.github.com',
    'Папки проекта нет: {}': 'Project folder is missing: {}', 'Создан git-репозиторий в {}': 'Git repository created in {}',
    'Claude посмотрит изменения и сделает коммит.': 'Claude will review the changes and commit.',
    'Коммит создан': 'Commit created', 'Создана и выбрана ветка {}': 'Created and switched to branch {}', 'Ветки': 'Branches',
    'Формат: git switch <ветка>': 'Usage: git switch <branch>', 'stash: готово': 'stash: done',
    'Формат: git clone <url> [имя проекта]': 'Usage: git clone <url> [project name]', 'Папка уже есть: {}': 'Folder already exists: {}',
    'Склонировано в {}; проект «{}» открыт.': 'Cloned into {}; project “{}” opened.',
    'Сначала открой проект: /cc project open <имя>': 'Open a project first: /cc project open <name>',
    'gh не авторизован: выполни gh auth login в терминале': 'gh is not logged in: run gh auth login in a terminal',
    'У проекта уже есть origin — используй git push': 'The project already has an origin — use git push',
    'репозиторий создан': 'repository created', '{} {}: не завершилось за {} с': '{} {}: did not finish in {} s',
    # --- agents & tasks ----------------------------------------------------
    'Агенты': 'Agents', 'Задачи': 'Tasks', 'встроенный': 'built-in', 'свой': 'custom', 'проект': 'project', 'все': 'all',
    'Имя агента: латиница в нижнем регистре, цифры, дефис (например my-helper)':
        'Agent name: lowercase latin letters, digits, dash (e.g. my-helper)',
    'Формат: agent new <имя> <чем занимается агент>': 'Usage: agent new <name> <what the agent does>',
    'Удалить можно только своего агента; встроенный — выключить: agent off <имя>':
        'Only custom agents can be removed; turn a built-in one off: agent off <name>',
    'Нет агента {}': 'No agent {}', 'Нет включённого агента {}: /cc agents': 'No enabled agent {}: /cc agents',
    'Запущено параллельно: {}': 'Started in parallel: {}', 'Формат: agent {} <задача>': 'Usage: agent {} <task>',
    'agent <имя> <задача> | new <имя> <описание> | on/off <имя> | rm <имя>':
        'agent <name> <task> | new <name> <description> | on/off <name> | rm <name>',
    'Остановлено из менеджера задач.': 'Stopped from the task manager.',
    # --- connectors ----------------------------------------------------------
    'Коннекторы': 'Connectors', '{}: нужен ключ': '{}: key needed', '{}: ключи': '{}: keys',
    'Коннектор {} подключён — заработает со следующего запроса /cc.': 'Connector {} enabled — active from the next /cc request.',
    'Нет коннектора {}': 'No connector {}', 'Нет коннектора {}. Список: /cc connectors': 'No connector {}. List: /cc connectors',
    '{}: неизвестный параметр {}': '{}: unknown parameter {}', '{}: слишком длинное или многострочное значение': '{}: value too long or multi-line',
    'Нужен Node.js (npx) — установи с nodejs.org и перезапусти мост': 'Node.js (npx) is required — install it from nodejs.org and restart the bridge',
    'Код и git': 'Code & git', 'Браузер и веб': 'Browser & web', 'Документация и поиск': 'Docs & search',
    'Мышление и память': 'Thinking & memory', 'Базы данных': 'Databases', 'Облака и DevOps': 'Cloud & DevOps',
    'Работа и дизайн': 'Work & design',
    'github.com/settings/tokens (или берётся из gh auth token)': 'github.com/settings/tokens (or taken from gh auth token)',
    'Issues, PR, ревью, Actions, поиск по репозиториям': 'Issues, PRs, reviews, Actions, repository search',
    'для своего сервера, напр. https://git.company/api/v4': 'for a self-hosted server, e.g. https://git.company/api/v4',
    'Проекты, merge requests, issues': 'Projects, merge requests, issues', 'Ошибки и трейсы из продакшена': 'Production errors and traces',
    'Логи падений сборок CI': 'Logs of failed CI builds', 'порт встроенного веб-сервера IDE (обычно не нужен)': 'IDE built-in web server port (usually not needed)',
    'Связь с открытой IntelliJ IDEA/PyCharm (нужен плагин MCP Server в IDE)': 'Talk to an open IntelliJ IDEA/PyCharm (needs the MCP Server plugin)',
    'Управление браузером: открыть сайт, кликать, заполнять, скриншоты': 'Drive a browser: open sites, click, fill forms, screenshots',
    'Отладка веб-страниц: консоль, сеть, производительность': 'Debug web pages: console, network, performance',
    'Скачивание и разбор сайтов целиком': 'Crawl and extract whole websites',
    'context7.com (необязательно, выше лимиты)': 'context7.com (optional, higher limits)',
    'Актуальная документация библиотек и фреймворков': 'Up-to-date docs for libraries and frameworks',
    'Веб-поиск': 'Web search', 'Поиск по коду и статьям для разработчиков': 'Search code and developer articles',
    'Пошаговое решение сложных задач': 'Step-by-step reasoning for hard problems',
    'Долгая память: факты о проектах между сессиями': 'Long-term memory: project facts across sessions',
    'Схема и SELECT-запросы (только чтение)': 'Schema and SELECT queries (read-only)', 'Коллекции и запросы': 'Collections and queries',
    'Проекты, таблицы, миграции, edge-функции': 'Projects, tables, migrations, edge functions',
    'Serverless Postgres: базы, ветки, запросы': 'Serverless Postgres: databases, branches, queries',
    'Кластеры из ~/.kube/config: поды, логи, деплой': 'Clusters from ~/.kube/config: pods, logs, deploys',
    'Деплой на Railway (нужен railway login)': 'Deploy to Railway (needs railway login)', 'Приложения, логи, dyno': 'Apps, logs, dynos',
    'dashboard.stripe.com/apikeys (лучше тестовый ключ)': 'dashboard.stripe.com/apikeys (a test key is safer)',
    'Платежи, клиенты, подписки': 'Payments, customers, subscriptions', 'Страницы и базы Notion': 'Notion pages and databases',
    'ID воркспейса (T…)': 'Workspace ID (T…)', 'Каналы, сообщения, треды': 'Channels, messages, threads',
    'Макеты Figma для вёрстки': 'Figma designs for implementation',
    'Модель': 'Model',
    'Замена': 'Replace',
    'Заменено в файлах: {}': 'Replaced in files: {}',
    'Нет такого превью — сделай /cc replace заново': 'No such preview — run /cc replace again',
    'Claude сейчас работает в этом чате — замени после него (или в другом чате)':
        'Claude is working in this chat — replace after it finishes (or in another chat)',
    'replace: /cc replace [-r] [-c] [--in *.java] <что> -> <на что>': 'replace: /cc replace [-r] [-c] [--in *.java] <find> -> <replace>',
    'Файлы изменились после превью — сделай превью заново': 'Files changed after the preview — preview again',
    'Неверная замена: {}': 'Invalid replacement: {}',
    '(пустой план)': '(empty plan)',
    'Принять': 'Accept',
    'Править': 'Edit',
    'План принят — выполняю.': 'Plan accepted — executing.',
    'План отклонён.': 'Plan rejected.',
    'Отправил правки к плану.': 'Sent your changes to the plan.',
    'plan: /cc plan <задача> — Claude сначала составит план': 'plan: /cc plan <task> — Claude makes a plan first',
    'План работает в чатах Claude Code (SDK)': 'Plan mode works in Claude Code (SDK) chats',
    'Всегда: {}': 'Always: {}',
    '{} запрещён правилом {}': '{} is denied by the rule {}',
    'Правило allow {} записано в .claude/settings.json проекта (/cc perms — все правила).':
        'Rule allow {} saved to the project .claude/settings.json (/cc perms — all rules).',
    'Правила разрешений': 'Permission rules',
    'perms [allow|deny|ask <правило> | rm <правило>]': 'perms [allow|deny|ask <rule> | rm <rule>]',
    'Тип правила: allow | deny | ask': 'Rule type: allow | deny | ask',
    'Неверное правило: {} (пример: Bash(git *), Edit(src/**))': 'Invalid rule: {} (example: Bash(git *), Edit(src/**))',
    'Нет такого правила в настройках проекта: {}': 'No such rule in the project settings: {}',
    '[на что смотреть]': '[what to focus on]',
    '<что объяснить>': '<what to explain>',
    '[какие тесты]': '[which tests]',
    '[что сохранить]': '[what to keep]',
    'Команды': 'Commands',
    'Нет команды /{}. Список: /cc commands': 'No command /{}. List: /cc commands',
    'Ревью текущих изменений (git diff): ошибки, риски, что улучшить': 'Review the current changes (git diff): bugs, risks, improvements',
    'Проверка изменений на уязвимости': 'Security review of the changes',
    'Создать или обновить CLAUDE.md по коду проекта': 'Create or update CLAUDE.md from the code',
    'Объяснить файл, функцию или идею проекта': 'Explain a file, function or idea of the project',
    'Запустить тесты и разобрать падения': 'Run the tests and analyse failures',
    'Сообщение коммита по текущим изменениям': 'Commit message for the current changes',
    'Сжать историю разговора': 'Compact the conversation history',
    'Новый чат (история остаётся в списке чатов)': 'New chat (history stays in the chat list)',
    'Токены и стоимость': 'Tokens and cost',
    'Заполнение контекста': 'Context fill',
    'Модель и усилие': 'Model and effort',
    'Проверка окружения': 'Environment check',
    'Откатить правки последнего запроса': "Undo the last request's edits",
    'Агенты': 'Agents',
    'Коннекторы MCP': 'MCP connectors',
    'Диагностика': 'Diagnostics',
    'Нужен Python 3.11+': 'Python 3.11+ is needed',
    'pip install -r requirements.txt (мост ставит сам при запуске)': 'pip install -r requirements.txt (the bridge does it on start)',
    'Встроен в SDK; если пусто — переустанови зависимости моста': 'Bundled with the SDK; if missing, reinstall the bridge dependencies',
    'Установи git: git-scm.com — нужен для /cc git и worktrees': 'Install git: git-scm.com — needed for /cc git and worktrees',
    'cli.github.com — для PR, issues и входа в GitHub': 'cli.github.com — for PRs, issues and GitHub sign-in',
    'nodejs.org — нужен коннекторам через npx и Codex CLI': 'nodejs.org — needed by npx connectors and Codex CLI',
    'Ставится вместе с Node.js': 'Comes with Node.js',
    'Нужен только для Codexcraft: кнопка «Установить Codex CLI» в его настройках': "Only for Codexcraft: the 'Install Codex CLI' button in its settings",
    'Необязательно: только для работы в контейнере': 'Optional: only for working in a container',
    'не найден': 'not found',
    'не запускается: {}': 'does not start: {}',
    'вход выполнен': 'signed in',
    'нет входа': 'not signed in',
    'Войди в GitHub: /claude → GitHub или gh auth login': 'Sign in to GitHub: /claude → GitHub or gh auth login',
    'Аккаунт Claude': 'Claude account',
    'не выполнен вход': 'not signed in',
    'Войди: /claude (настройки) → «Войти», либо задай API-ключ': 'Sign in: /claude (settings) → Sign in, or set an API key',
    'Порт моста': 'Bridge port',
    'Соседний мост': 'Other bridge',
    '{}: занят (работает второй мод)': '{}: in use (the other mod is running)',
    '{}: свободен': '{}: free',
    'Папка моста': 'Bridge folder',
    'Нет прав на запись в эту папку': 'No write access to this folder',
    'Проект': 'Project',
    'Папка проекта недоступна для записи — Claude не сможет править файлы': 'The project folder is not writable — Claude cannot edit files',
    'Свободно на диске': 'Free disk space',
    '{} ГБ': '{} GB',
    'Меньше 2 ГБ — сборки и npm могут не поместиться': 'Under 2 GB — builds and npm may not fit',
    'Права': 'Rights',
    'администратор': 'administrator',
    'обычный пользователь': 'regular user',
    'Команды Claude выполняются с правами этого пользователя': "Claude's commands run with this user's rights",
    'Коннектор {}': 'Connector {}',
    '/cc connect {} — проверь ключ или вход': '/cc connect {} — check the key or sign-in',
    'Контекст заполнен на {}% — сожми историю: /cc compact [на чём сосредоточиться]':
        'Context is {}% full — compress the history: /cc compact [what to focus on]',
    'compact работает в чатах Claude Code (SDK)': 'compact works in Claude Code (SDK) chats',
    'Сжимать нечего: в этом чате ещё не было разговора': 'Nothing to compact: this chat has no conversation yet',
    'История сжата (было {} токенов).': 'History compacted (was {} tokens).',
    'Контекст': 'Context',
    'Claude Code CLI уже встроен в SDK — ставить нечего': 'Claude Code CLI is bundled with the SDK — nothing to install',
    'Нужен Node.js (npm): установи с nodejs.org, перезапусти мост и нажми ещё раз':
        'Node.js (npm) is needed: install it from nodejs.org, restart the bridge and press again',
    'npm install завершился с кодом {}': 'npm install exited with code {}',
    'Codex установлен, но codex не найден в PATH — укажи codex_path в bridge-config.json':
        'Codex is installed but codex is not on PATH — set codex_path in bridge-config.json',
    'Вход через браузер': 'Browser sign-in',
    'Задачи, проекты и циклы Linear': 'Linear issues, projects and cycles',
    'Jira и Confluence': 'Jira & Confluence',
    'Задачи Jira, страницы Confluence (Atlassian Cloud)': 'Jira issues, Confluence pages (Atlassian Cloud)',
    'Notion (вход)': 'Notion (sign-in)', 'Notion без токена интеграции': 'Notion without an integration token',
    'Sentry (вход)': 'Sentry (sign-in)', 'Ошибки и трейсы, без auth-токена': 'Errors and traces, no auth token',
    'Задачи и проекты Asana': 'Asana tasks and projects', 'Деплои, логи, проекты Vercel': 'Vercel deployments, logs, projects',
    '{}: вход забыт': '{}: sign-in forgotten',
    'Войди в {} в браузере — потом всё подключится само.': 'Sign in to {} in the browser — it connects by itself afterwards.',
    'Ссылка входа устарела или уже использована — начни заново: /cc connect':
        'The sign-in link expired or was already used — start again: /cc connect',
    '{}: вход отменён ({})': '{}: sign-in cancelled ({})',
    '{}: сервер не выдал access_token': '{}: the server returned no access_token',
    '{}: сервер не поддерживает автоматическую регистрацию клиента — нужен ключ':
        '{}: the server does not support automatic client registration — a key is needed',
    'OAuth: {} ответил {}: {}': 'OAuth: {} answered {}: {}',
    'Не удалось обновить вход коннектора: {}': 'Could not refresh a connector sign-in: {}',
    'Готово — можно вернуться в Minecraft.': 'Done — you can go back to Minecraft.',
    'Открыто уже {} терминалов — закрой лишние': '{} terminals are open already — close some',
    'Нет такого терминала (закрыт или мост перезапущен)': 'No such terminal (closed, or the bridge restarted)',
    'Терминал завершён': 'The terminal has ended',
    'Картинка для Claude: {}': 'Picture for Claude: {}',
    'Картинка должна быть PNG, JPG, GIF или WebP': 'The picture must be PNG, JPG, GIF or WebP',
    'Картинка больше 5 МБ — уменьши её': 'The picture is over 5 MB — make it smaller',
    'img: /cc img <файл> [вопрос]': 'img: /cc img <file> [question]',
    'Картинки работают в чатах Claude Code (SDK), не в Messages API-чате. /c new local':
        'Pictures work in Claude Code (SDK) chats, not in a Messages API chat. /c new local',
    'Правки Claude': "Claude's edits",
    'Откат правок Claude': "Undo of Claude's edits",
    'Claude ещё работает над этим запросом — дождись конца или /cc cancel':
        'Claude is still working on this request — wait for it to finish or /cc cancel',
    'Нечего откатывать: Claude ещё не менял файлы в этом чате': "Nothing to undo: Claude hasn't changed files in this chat",
    'undo: без аргумента — откатить последний запрос; undo list — история':
        'undo: no argument — undo the last request; undo list — history',
    # --- shell & GitHub --------------------------------------------------------
    'Формат: /sh <команда>  (а также /ps, /cmd, /bash)': 'Usage: /sh <command>  (also /ps, /cmd, /bash)',
    'cmd есть только в Windows — используй /sh или /bash': 'cmd exists only on Windows — use /sh or /bash',
    'bash не найден — в Windows поставь Git for Windows или используй /ps':
        'bash not found — on Windows install Git for Windows or use /ps',
    'Нет команды {}': 'No running command {}',
    'Нужен GitHub CLI: установи с cli.github.com и перезапусти мост':
        'GitHub CLI is required: install it from cli.github.com and restart the bridge',
    'Это не похоже на токен GitHub (ghp_… или github_pat_…)': 'This does not look like a GitHub token (ghp_… or github_pat_…)',
    'Сначала войди в GitHub или задай токен': 'Sign in to GitHub or set a token first',
    # --- PC sync -------------------------------------------------------------
    'Синхронизировано с Claude Code на ПК: новых сессий {}.': 'Synced with Claude Code on the PC: {} new sessions.',
    'Открыт терминал: {}': 'Terminal opened: {}', 'Claude Code CLI не найден': 'Claude Code CLI not found',
    'Не найден терминал (x-terminal-emulator, gnome-terminal, konsole)':
        'No terminal found (x-terminal-emulator, gnome-terminal, konsole)',
    # --- account -------------------------------------------------------------
    'Claude Code CLI не найден: установи claude-agent-sdk или укажи cli_path': 'Claude Code CLI not found: install claude-agent-sdk or set cli_path',
    'API-ключ выглядит неверно': 'The API key looks wrong', 'Claude CLI не ответил вовремя': 'Claude CLI did not answer in time',
    'код выхода {}': 'exit code {}', 'Вставь код со страницы входа одной строкой': 'Paste the code from the sign-in page as one line',
    'Вход не запущен: нажми «Войти»': 'Sign-in is not running: press “Sign in”', 'Выход не удался: {}': 'Sign-out failed: {}',
    'Нет API-ключа: добавь его в /c settings (или ANTHROPIC_API_KEY в окружении моста)':
        'No API key: add it in /c settings (or ANTHROPIC_API_KEY in the bridge environment)',
}

_SETTINGS = {'mtime': None, 'lang': 'en'}
_PATTERNS = []


def _compile():
    """Templates with {} become anchored regexes; more specific (longer) ones first."""
    items = sorted((k for k in EN if '{}' in k), key=lambda k: -len(k.replace('{}', '')))
    for key in items:
        regex = '^' + '(.+?)'.join(re.escape(part) for part in key.split('{}')) + '$'
        _PATTERNS.append((re.compile(regex, re.S), EN[key]))


_compile()
CYRILLIC = re.compile('[А-Яа-яЁё]')


def language(directory):
    """'en' (default) or 'ru', from the mod's client.json (re-read when it changes)."""
    path = directory / 'client.json'
    try:
        mtime = path.stat().st_mtime
        if mtime != _SETTINGS['mtime']:
            _SETTINGS['mtime'] = mtime
            _SETTINGS['lang'] = 'ru' if json.loads(path.read_text(encoding='utf-8')).get('language') == 'ru' else 'en'
    except (OSError, ValueError):
        _SETTINGS['lang'] = 'en'
    return _SETTINGS['lang']


def to_english(text, depth=0):
    if not isinstance(text, str) or not CYRILLIC.search(text) or depth > 4:
        return text
    if '\n' in text:
        return '\n'.join(to_english(line, depth) for line in text.split('\n'))
    if text in EN:
        return EN[text]
    for regex, target in _PATTERNS:
        m = regex.match(text)
        if m:
            return _fill(target, [to_english(g, depth + 1) for g in m.groups()])
    return text


def _fill(target, parts):
    pieces = target.split('{}')
    out = pieces[0]
    for part, piece in zip(parts, pieces[1:]):
        out += part + piece
    return out


# Event fields that carry bridge-written UI text (never Claude content or paths).
UI_KEYS = {'note', 'summary', 'step', 'state', 'source', 'group', 'about', 'hint', 'label', 'title', 'description'}
CONTENT_KINDS = {'text', 'text_part', 'file', 'diff', 'history', 'tool_error', 'listing', 'tree', 'found',
                 'cwd', 'git_status', 'git_log', 'git_branches', 'git_none'}


def translate_event(event, lang):
    if lang != 'en':
        return event
    out = dict(event)
    if out.get('kind') not in CONTENT_KINDS and out.get('kind') != 'question':
        out['text'] = to_english(out.get('text', ''))
    if out.get('kind') == 'permission' and isinstance(out.get('choices'), list):
        out['choices'] = [to_english(c) for c in out['choices']]
    if out.get('kind') == 'question' and out.get('text') == 'Завершил действие в браузере?':
        out['text'] = to_english(out['text'])
        out['choices'] = [to_english(c) for c in out.get('choices', [])]
    for key in UI_KEYS & out.keys():
        out[key] = to_english(out[key])
    if isinstance(out.get('rows'), list) and out.get('kind') in ('tokens', 'tasks', 'agents', 'connectors', 'chats', 'projects', 'commands'):
        out['rows'] = [{k: (to_english(v) if k in UI_KEYS else v) for k, v in r.items()} if isinstance(r, dict) else r
                       for r in out['rows']]
    if out.get('kind') == 'doctor' and isinstance(out.get('rows'), list):
        out['rows'] = [{k: (to_english(v) if k in ('name', 'value', 'hint') else v) for k, v in r.items()}
                       for r in out['rows']]
    if isinstance(out.get('fields'), list):
        out['fields'] = [{k: (to_english(v) if k in UI_KEYS else v) for k, v in f.items()} for f in out['fields']]
    return out
