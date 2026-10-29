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
    'Чат: {}': 'Chat: {}',
    'perms [allow|deny|ask <правило> | rm <правило> | trust | untrust]': 'perms [allow|deny|ask <rule> | rm <rule> | trust | untrust]',
    'Совпадение через несколько строк в {} — замена работает в пределах строки': 'A match spans several lines in {} — replace works within a line',
    'Это папка, не файл: {}': 'This is a folder, not a file: {}',
    'save: text — строка': 'save: text must be a string',
    'Ожидалось число: {}': 'A number was expected: {}',
    'Неверный запрос: нужен JSON-объект': 'Bad request: a JSON object is expected',
    'Неверный запрос: неверный тип поля': 'Bad request: wrong field type',
    'open_session <id сессии>': 'open_session <session id>',
    'В репозитории есть свои настройки Claude (.claude, CLAUDE.md) — они не применяются, пока ты не доверишь папку: /cc perms trust': 'The repository has its own Claude settings (.claude, CLAUDE.md) — they are not used until you trust the folder: /cc perms trust',
    'этого файла': 'this file',
    'git: адрес — https://, ssh://, git@host: или git://': 'git: the URL must be https://, ssh://, git@host: or git://',
    '{}: адрес с ключом — только https://': '{}: a URL that gets a key must be https://',
    '{}: адрес — https:// или http://localhost': '{}: the URL must be https:// or http://localhost',
    'Настройки экспортированы: {}': 'Settings exported: {}',
    'Импортировано: {}': 'Imported: {}',
    'export [файл.zip] | import <файл.zip>': 'export [file.zip] | import <file.zip>',
    'Нет файла: {}': 'No file: {}',
    'Это не экспорт Claudecraft (нет claudecraft-export.json)': 'Not a Claudecraft export (no claudecraft-export.json)',
    'guest: имя — буквы, цифры, . _ - (до 24)': 'guest: name — letters, digits, . _ - (up to 24)',
    'guest: права view или chat': 'guest: scope is view or chat',
    'guest: нет гостя {}': 'guest: no guest {}',
    'guest new <имя> [view|chat] [часов]': 'guest new <name> [view|chat] [hours]',
    'Гость {}: {}, {} ч — этот чат': 'Guest {}: {}, {} h — this chat',
    'Гость {} отключён': 'Guest {} revoked',
    'Гости': 'Guests',
    'Провайдер: {}': 'Provider: {}',
    'Нет ключа {}: /c {}-key <ключ> (openrouter.ai/keys)': 'No {} key: /c {}-key <key> (openrouter.ai/keys)',
    '{} работает без ключа': '{} needs no key',
    '{} не отвечает ({}) — запущен ли он?': '{} does not answer ({}) — is it running?',
    'Выбери модель: /c models {}': 'Choose a model: /c models {}',
    '{}: пустой ответ модели {}': '{}: empty answer from model {}',
    '{} не отвечает ({}): {}': '{} does not answer ({}): {}',
    'Ключ {} сохранён': '{} key saved',
    'Агент': 'Agent', 'Агент сохранён в проект: {}': 'Agent saved to the project: {}',
    'Агент {} импортирован: {}': 'Agent {} imported: {}',
    'Нужен файл агента .md: {}': 'An agent .md file is needed: {}',
    'В файле нет заголовка агента (--- name / description ---)': 'The file has no agent header (--- name / description ---)',
    'agent <имя> <задача> | new <имя> <описание> | file <имя> | edit <имя> | export <имя> | import <файл.md> | on/off <имя> | rm <имя>':
        'agent <name> <task> | new <name> <description> | file <name> | edit <name> | export <name> | import <file.md> | on/off <name> | rm <name>',
    'Изменения отправлены на ревью в {} — ответ появится там': 'The changes were sent for review to {} — the answer appears there',
    'Второго мода нет — ревью делает отдельный агент code-reviewer': 'The other mod is not running — a separate code-reviewer agent reviews',
    'Наблюдение остановлено': 'Watching stopped', 'Сейчас ничего не падает': 'Nothing is failing right now',
    'Слежу за проектом: при изменениях запускаю «{}»': 'Watching the project: running “{}” on changes',
    'Наблюдение': 'Watch mode',
    '{} — упало (код {}, {} с)': '{} — failed (code {}, {} s)',
    '{} — снова проходит ({} с)': '{} — passes again ({} s)',
    'Claude берётся за issue #{}: ветка, работа, черновик PR': 'Claude takes issue #{}: branch, work, draft PR',
    'issues [list | <номер> | do <номер>]': 'issues [list | <number> | do <number>]',
    'Нужен GitHub CLI (gh): cli.github.com, затем вход — /claude → GitHub': 'GitHub CLI (gh) is needed: cli.github.com, then sign in — /claude → GitHub',
    'gh вернул не JSON: {}': 'gh returned no JSON: {}',
    'Claude готовит PR (черновик): описание, push, gh pr create — каждую команду подтверждаешь ты':
        'Claude prepares a draft PR: description, push, gh pr create — you confirm every command',
    'Claude разбирает ревью PR #{}: {} комментариев': 'Claude works through the review of PR #{}: {} comments',
    'pr [list | <номер> | create [база] | reply <номер>]': 'pr [list | <number> | create [base] | reply <number>]',
    'Конфликты': 'Conflicts', 'Конфликтов нет': 'No conflicts',
    'Выбор: ours | theirs | both': 'Choice: ours | theirs | both',
    'Нет такого конфликта (файл изменился?) — обнови список: /cc git conflicts': 'No such conflict (the file changed?) — refresh: /cc git conflicts',
    'git pick <файл> <номер> ours|theirs|both': 'git pick <file> <number> ours|theirs|both',
    'Нет такого файла в проекте: {}': 'No such file in the project: {}',
    '{}: конфликты разрешены, файл добавлен в коммит': '{}: conflicts resolved, the file is staged',
    'Claude разрешает конфликты: {}': 'Claude is resolving the conflicts: {}',
    'Нет языкового сервера для {} — установи: {}': 'No language server for {} — install: {}',
    'Нет языкового сервера для {}': 'No language server for {}',
    'npm i -g pyright  или  pip install python-lsp-server': 'npm i -g pyright  or  pip install python-lsp-server',
    'Нет форматтера для {} — установи: {}': 'No formatter for {} — install: {}',
    'Нет форматтера для {}': 'No formatter for {}',
    '{}: не уложился в {} с': '{}: did not finish in {} s',
    'google-java-format (github.com/google/google-java-format) в PATH': 'google-java-format (github.com/google/google-java-format) on PATH',
    'Сессия открыта — пиши, Claude продолжит её': 'Session opened — write, Claude continues it',
    'Экспортировать нечего: в чате ещё не было разговора': 'Nothing to export: this chat has no conversation yet',
    'Чат сохранён: {}': 'Chat saved: {}',
    'Ветвить нечего: у чата ещё нет сессии Claude Code': 'Nothing to fork: this chat has no Claude Code session yet',
    'Ветка от сообщения {} чата «{}» — продолжай здесь, исходный чат не изменился':
        'Branch from message {} of “{}” — continue here, the original chat is unchanged',
    'Стиль ответов': 'Output style',
    'Стиль задаётся для проекта: используй /cc style': 'A style is set per project: use /cc style',
    'Нет стиля {}. Есть: {}': 'No style {}. Available: {}',
    'Обычные ответы Claude Code': 'Normal Claude Code answers',
    'Коротко: только суть, без вступлений': 'Brief: just the point, no preamble',
    'Объясняет «почему» и решения по ходу работы': 'Explains the why and its decisions while working',
    'Учит: оставляет пользователю небольшие куски кода': 'Teaches: leaves small pieces of code for you to write',
    'Строгий ревьюер: риски и ошибки прежде всего': 'Strict reviewer: risks and bugs first',
    'Фоновая задача Claude: {}': "Claude's background task: {}",
    'Нет такой фоновой задачи': 'No such background task',
    'пока нет вывода': 'no output yet',
    'Задача уже не работает (фоновые процессы живут, пока идёт запрос Claude)': 'The task is not running any more (background processes live while a Claude request runs)',
    'Остановлено: {}': 'Stopped: {}',
    'bg out|stop <id>': 'bg out|stop <id>',
    'Журнал хуков': 'Hook log', 'Хуки': 'Hooks',
    'Хук {} {}: {}': 'Hook {} {}: {}',
    'hooks [add <Событие> [matcher] -- <команда> | rm <id> | log]': 'hooks [add <Event> [matcher] -- <command> | rm <id> | log]',
    'Событие хука: {}': 'Hook event: {}',
    'hooks add <Событие> [matcher] -- <команда>': 'hooks add <Event> [matcher] -- <command>',
    'Нет такого хука в настройках проекта (свои ~/.claude хуки правь в файле)': 'No such hook in the project settings (edit ~/.claude hooks in the file)',
    'Скиллы': 'Skills', 'Нет скилла {}': 'No skill {}', 'skills [on|off <имя>]': 'skills [on|off <name>]',
    'Память': 'Memory', 'Память Claude': "Claude's memory",
    'memory [edit <файл> | on | off]': 'memory [edit <file> | on | off]',
    'Запомнено в {}': 'Remembered in {}',
    '#: /cc # <что запомнить>': '#: /cc # <what to remember>',
    'Заметка длиннее 2000 символов — открой CLAUDE.md в редакторе': 'The note is over 2000 characters — open CLAUDE.md in the editor',
    'Откатывать нечего: у чата ещё нет сессии Claude Code': 'Nothing to rewind: this chat has no Claude Code session yet',
    'История запросов': 'Request history',
    'Claude ещё работает в этом чате — дождись конца или /cc cancel': 'Claude is still working in this chat — wait or /cc cancel',
    'rewind: /cc rewind — список · /cc rewind <номер>': 'rewind: /cc rewind — list · /cc rewind <number>',
    'Откат к сообщению {}: убрано запросов из истории — {}': 'Rewound to message {}: requests removed from the history — {}',
    'Нет сообщения №{} — всего {}': 'No message #{} — there are {}',
    'Лимит «{}» достигнут: {} из {} — работа на паузе': 'The “{}” limit is reached: {} of {} — work is paused',
    'запрос': 'request', 'чат': 'chat', 'день': 'day',
    'Чат на паузе из-за лимита — /cc budget resume': 'The chat is paused by a limit — /cc budget resume',
    'Пауза снята — продолжаю (лимит пропускается для одного запроса).': 'Resumed — the limit is skipped for one request.',
    'Бюджет': 'Budget',
    'budget: task | chat | day <лимит> · resume': 'budget: task | chat | day <limit> · resume',
    'Лимит: $0.50, 200k, 2M или off': 'Limit: $0.50, 200k, 2M or off',
    'worktree не создан ({}) — задача работает в папке проекта': 'no worktree ({}) — the task works in the project folder',
    'Задача «{}» готова в своём worktree': 'Task “{}” is ready in its worktree',
    'Слито в проект: {} — {}': 'Merged into the project: {} — {}',
    'Worktree удалён без слияния: {}': 'Worktree removed without merging: {}',
    'worktree: list | merge <чат> | diff <чат> | drop <чат>': 'worktree: list | merge <chat> | diff <chat> | drop <chat>',
    'Задача ещё работает — дождись конца или /cc stop': 'The task is still running — wait or /cc stop',
    'У этого чата нет своего worktree': 'This chat has no worktree of its own',
    'В основном проекте есть незакоммиченные изменения — закоммить или спрячь их (git stash) и повтори merge':
        'The main project has uncommitted changes — commit or stash them and merge again',
    'Конфликт при слиянии — ничего не изменено. Файлы: {}': 'Merge conflict — nothing changed. Files: {}',
    'все изменения': 'all changes',
    'Этот кусок уже изменился или применён — обнови список: /cc git hunks': 'This hunk changed or was applied already — refresh: /cc git hunks',
    'Кусок {} добавлен в коммит (staged)': 'Hunk of {} staged',
    'Кусок {} убран из коммита (unstaged)': 'Hunk of {} unstaged',
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

# Codexcraft/Geminicraft rename "Claude" in the bridge they copy (their build.gradle sets BRAND):
# the same keys with the addon's names are added, so their messages are translated too.
BRAND = []
for _old, _new in BRAND:
    EN.update({re.sub(_old, _new, k): re.sub(_old, _new, v) for k, v in list(EN.items())})

_SETTINGS = {'mtime': None, 'lang': 'en'}
_PATTERNS = []


def _compile():
    """Templates with {} become anchored regexes; more specific (longer) ones first."""
    items = sorted((k for k in EN if '{}' in k), key=lambda k: -len(k.replace('{}', '')))
    for key in items:
        regex = '^' + '(.*?)'.join(re.escape(part) for part in key.split('{}')) + '$'
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
    if isinstance(out.get('rows'), list) and out.get('kind') in ('tokens', 'tasks', 'agents', 'connectors', 'chats', 'projects', 'commands', 'styles'):
        out['rows'] = [{k: (to_english(v) if k in UI_KEYS else v) for k, v in r.items()} if isinstance(r, dict) else r
                       for r in out['rows']]
    if out.get('kind') == 'doctor' and isinstance(out.get('rows'), list):
        out['rows'] = [{k: (to_english(v) if k in ('name', 'value', 'hint') else v) for k, v in r.items()}
                       for r in out['rows']]
    if isinstance(out.get('fields'), list):
        out['fields'] = [{k: (to_english(v) if k in UI_KEYS else v) for k, v in f.items()} for f in out['fields']]
    return out
