# Claudecraft: передача работы

Продолжи существующий проект. Общайся с пользователем по-русски.

## Цель и структура

Claudecraft — клиентский Fabric-мод, интегрирующий Claude Chat и Claude Code в
Minecraft Java. Цель — Minecraft 26.3 / Java 25, Fabric Loader 0.20.1,
Fabric API 0.165.0+26.3, Loom 1.19.3, Gradle wrapper 9.8.2 (сверено с
meta.fabricmc.net 2026-10-27; `gradlew build` с JDK 25 проходит, JAR собирается).
В 26.3 команды — `ClientCommands.literal/argument`, сообщение в чат —
`gui.chatListener().handleSystemMessage(c, false)`. В игре мод ещё не запускался.

- src/client/java/dev/claudechat/ClaudeChat.java — команды, кнопки, HTTP polling.
- bridge/bridge.py — локальный HTTP-мост, очереди, чаты, разрешения, MCP registry.
- bridge/backends/sdk.py — Claude Agent SDK, диалоги и инструменты Claude Code.
- bridge/backends/api.py — Anthropic Messages API для обычных разговоров.
- bridge/test_bridge.py — тесты ядра.
- bridge/Dockerfile, compose*.yaml — подготовленные конфигурации контейнера.
- examples/extensions/system-info/ — пример MCP stdio расширения.
- bridge/test_sdk_integration.py — опциональная проверка f на настоящем CLI.
- docs/EXTENSIONS.md, README.md — устройство и запуск (семантика f обновлена).

Мод подключается к 127.0.0.1 по bearer-токену из config/claude-chat/connection.json.
Python-мост запускается отдельно с --game-dir <папка конкретного профиля Minecraft>.
Чаты и SDK session IDs сохраняются в config/claude-chat/chats.json.

## Последние требования пользователя: имеют приоритет над текущим кодом

Два пространства команд: /c — разговоры, /cc — работа с кодом.
В обоих: <запрос> или q <запрос> = queue, дождаться завершения текущей работы.
f / force <запрос> = отправить дополнительное сообщение в текущую работу Claude,
пока он думает, без отмены задачи и без перезапуска процесса.
c / chats = выбор чатов кнопками. Сохрани chat как совместимый алиас.

Статус f (сделано 2026-10-27): enqueue только для q; `Bridge.force()` никогда не
вызывает stop. backends/sdk.py → `SdkTurn`: живой ClaudeSDKClient на весь элемент
очереди, f пишется через client.query() (streaming input). CLI запускается с
`--replay-user-messages`; ответ завершается на ResultMessage, только когда все
отправленные сообщения подтверждены эхом — иначе читаем следующий ход. Флаг
accepting сбрасывается без await между проверкой и сбросом: f либо попадает в
сессию, либо получает TurnClosed и выполняется следующим (с сообщением, без отмены).
Доставка f — фоновая задача (не держит bridge.lock). CLI без эха → f отклоняется
явно. API-чат во время ответа → явная ошибка, без отмены и без очереди.
Алиасы: queue=q, force=f, c/chats=chat.

Проверено на настоящем CLI 2.1.310 через bridge/fake_anthropic.py
(`CLAUDECRAFT_CLI_IT=1 python -m unittest test_cli_fake_api`, 4/4 OK):
- эхо промпта приходит только вместе с первым выводом модели, не при приёме;
- f во время хода с инструментами → вставляется в следующий tool_result
  («The user sent a new message while you were working»), один ResultMessage;
- f во время чистого текста → эхо ПОСЛЕ первого ResultMessage, отдельный ход
  (новый SystemMessage init) в том же процессе;
- f до того, как CLI прочитал первый промпт → CLI склеивает их в одно сообщение
  "a\nb" с одним эхом. Поэтому подтверждения сопоставляются по содержимому
  (SdkTurn.acknowledge), а не по количеству эхо.
Со встроенным CLI f пишется сразу (trusted_replay); с cli_path — после первого эха.
Ответы настоящей модели: `CLAUDECRAFT_SDK_IT=1 python -m unittest test_sdk_integration`
(нужен claude auth login), ещё не прогонялось.

Автозапуск и аккаунт (2026-10-27): BridgeLauncher.java распаковывает bridge из JAR
(claudecraft/bridge, см. processResources) в config/claude-chat/runtime, ищет Python
3.11+ (настройка → py -3 → python), создаёт runtime/venv, pip install по хешу
requirements, запускает bridge.py --game-dir, останавливает дерево процессов на
CLIENT_STOPPING. Уже работающий мост (отвечает на /events) не трогает. Настройки
мода: config/claude-chat/client.json (autoStart, python). SettingsScreen.java
(/c settings, /claude; рендер 26.3 = extractRenderState + GuiGraphicsExtractor):
статус моста, вход/выход, код входа, API-ключ (маскирован), автозапуск, Python.
Мост: backends/account.py — `claude auth login` с BROWSER=none, ссылка из stdout,
код пишется в stdin; GET /account; действия login/login_code/login_cancel/logout/
apikey; ключ в secrets.json (0600), api.py берёт его оттуда или из окружения.
Проверено: runClient — мод грузится, распаковка/Python/venv; распакованный мост
запускается; реальный CLI выдаёт ссылку входа, отмена работает. В игре экран
настроек визуально ещё не проверялся.

Интерфейс (2026-10-27): ChatStyle.java рендерит события по kind (thinking, text c
markdown, tool, tool_error, todo, done, error, status, permission, question,
chats, listing, cwd, file, tokens, auto, url). Описания инструментов — в мосте,
backends/describe.py. Значки только из проверенных на скриншоте (📄 📁 🔍 ✎ ✚ ☰ ◆
⚙ ◎ ⟳ ✔ ✖ ⚠ ✦ Σ ∞ ☑ ☐ ▶); ▤ ▣ ⌕ ↻ ▸ рисуются крошечными — не использовать.
Визуальная проверка: `gradlew runShots` (DevShots.java, -Dclaudecraft.devshots)
открывает настройки и превью чата, кладёт PNG в run/screenshots и выходит.
Чат хранит cwd, session_cwd, auto (off|edits|all), allowed (кнопка «всегда»),
usage. Токены: usage в ResultMessage — за ход, total_cost_usd — накопительно за
процесс CLI (проверено на реальном CLI), поэтому стоимость считается по приращению.

Рабочий инструмент (2026-10-27). Пользователь: «это мод для программиста», не про
геймплей; готовые зависимости брать охотно. Модули моста: backends/projects.py
(проект = имя+папка; у /cc-чата поле project; рабочая папка Claude = папка проекта;
open_project возвращает чаты проекта), files.py (файловый менеджер, своя папка на
режим, НЕ двигает Claude; rm с подтверждением), gitops.py (git/gh, сеть в фоне,
`git commit` без текста = задача Claude), agents.py (пресеты + agents.json +
<проект>/.claude/agents/*.md → ClaudeAgentOptions.agents, инструмент Task),
connectors.py (каталог MCP из npm с проверенными env; секреты в secrets.json вместе
с API-ключом; статус из init.mcp_servers). sdk.py: include_partial_messages →
события text_part построчно (AssistantMessage-текст тогда не дублируется),
субагенты по parent_tool_use_id (поле agent), RateLimitEvent → /c tokens,
инструмент minecraft_logs. Bridge: tasks/spawn/stop, /state для окна, history.
Мод: ConversationScreen (клавиша K, свой hit-test стилей, Ctrl+Enter), KeyScreen
(ключи коннекторов, не через чат), WorkStyle (новые kind'ы), Mod Menu (compileOnly
21.0.0, entrypoint modmenu). Игровые п.2–3 (Claude видит/действует в игре) отложены.

Codexcraft (подпроект codexcraft/, автор коммита 1b7adda — akaeji). Аддон: Codex
через `codex app-server`, команды /gpt и /codex, клавиша J, порт 18742,
config/codexcraft. Его build.gradle (generateShared) КОПИРУЕТ наш Java-код и
bridge/ с заменой строк ('ClaudeChat', '/cc', 'literal(mode)', 'SHORTCUTS.entrySet()',
'InputConstants.KEY_K', тексты help и т.д.). Поэтому после любой правки основного
мода собирать ОБА: `gradlew build` (корень включает :codexcraft) и гонять
`python -m unittest discover -s codexcraft/tests`. Меняя строки, на которые
опираются замены, — правь и codexcraft/build.gradle. Codex CLI на этом ПК не
установлен (2026-10-28).

## Сделано 2026-10-28/29 (пакет 1,3,5–12,16,17)

- Модель/усилие на чат (/cc model, effort); HUD-панель задач и тосты/звук (Notify.java).
- Окно файлов: поиск по содержимому (/grep); EditorScreen (/edit, F4) + Highlighter
  (подсветка и в превью, и в ```-блоках чата); мост GET /edit, POST /save (409 при конфликте).
- backends/undo.py: снимок файла перед первой правкой Claude в запросе (в колбэке
  разрешений, видит и автоодобренные); diff в карточке разрешения и событие edit_diff;
  /cc undo, /cc undo list. Bash-правки не отслеживаются.
- backends/images.py: маркер [[image:путь]] в тексте очереди → image-блок SDK (картинка
  сохраняется в JSONL сессии = видна в Claude Code на ПК). /cc img, /cc shot (без HUD,
  ≤1600 px, config/claude-chat/shots, последние 30), кнопка «Скриншот» в окне K.
- backends/vt.py + ptyterm.py: настоящий терминал (/pty): ConPTY через ctypes
  (STARTF_USESTDHANDLES обязателен, после выхода процесса ClosePseudoConsole), pty на
  POSIX; TerminalScreen. Без новых зависимостей (pip download пользователь отклонил).
- backends/oauth.py: вход через браузер для удалённых MCP (Linear, Atlassian, Notion,
  Sentry, Asana, Vercel); колбэк /oauth/callback без токена моста (защита state).
- Codexcraft: «Установить Codex CLI» (npm i -g @openai/codex), codex_path сохраняется.
- PortablePython.java: если Python нет — python-build-standalone 3.13 по SHA256SUMS,
  свой распаковщик tar. Реальная загрузка ещё не проверялась.
- LiveTest.java: `gradlew runLiveTest` — мост, тестовый мир, команды и клики, отчёт
  run/livetest/report.md со скриншотами. Claude не вызывается.
- Подвох инструментов: в heredoc Bash `\\n`/`\\r` становятся настоящими переводами
  строки — Java/Python-строки с экранированием писать через Write/Edit.

## Отложено: аддон для модеров (просьба пользователя 2026-10-28, не забыть)

Сделать ОТДЕЛЬНЫМ аддоном (как Codexcraft), не в основном моде:
- 13. Цикл «правка → проверка»: Claude правит мод, сам собирает (gradlew build) и
  запускает второй dev-клиент; ошибки сборки и крэши разбирает автоматически.
- 14. Датапаки/ресурспаки: проверка JSON и авто `/reload` в мире после правок.
- 15. Claude видит игру (по явному разрешению): блок под прицелом, NBT, сущности,
  логи сервера; действия в игре (/give, /tp, постройки) только с подтверждением.
Пункт 4 (держать процесс CLI живым между запросами) — пользователь спросил о
влиянии, решение не принято.

Прочее: /events возвращает boot id моста; мод шлёт его обратно, при смене моста
курсор сбрасывается (раньше после перезапуска моста события пропадали). Новый
клиент без boot начинает с текущего момента (старую историю не проигрывает).
Таймаут разрешения (10 мин) → PermissionResultDeny, а не исключение в SDK.
Отладка: logger `claudecraft.sdk` (DEBUG) печатает каждое сообщение SDK.
На Windows SDK блокирует event loop ~1 с при запуске CLI (CreateProcess) —
довод держать процесс CLI живым между элементами очереди (ещё не сделано).

Другие команды уже есть: cancel, restart, new local, /c new api, rename, status,
clearqueue, pending, reply, /cc local (импорт CLI-сессий настроенного workspace),
reload, tools/capabilities. Restart сохраняет историю, new начинает чистый чат.
Краткие алиасы можно добавить без потери полных имён.

## Уже подготовлено в исходниках

FIFO по каждому чату, независимая работа разных чатов; кнопки разрешений SDK,
AskUserQuestion и свой ответ/множественный выбор. Разрешения одноразовые,
привязаны к request ID и чату; устаревшие кнопки не подтверждают новые операции.
Внешний браузер открывается кликом через minecraft.open_browser. Встроенного
Chromium/CEF внутри Minecraft нет. Подключение браузерного MCP предусмотрено,
но не проверено; UAC/MFA/интерактивные терминалы не преобразованы в игровой UI.
Чаты сайта claude.ai не импортируются — только сессии SDK/CLI и чаты моста.

## Расширяемость, Docker, администратор

Пользователь хочет Docker, SSH, CMD/PowerShell и права администратора.
Операции идут через SDK Bash на машине моста. Права наследуются от пользователя
процесса: это не автоматическое повышение прав/UAC bypass. Docker root не равен
root/Administrator на хосте. Host Docker socket подключается отдельным override.
Нужные ключи SSH, known_hosts, sudo и зависимости должны быть настроены отдельно.

Расширения: <workspace>/extensions/<id>/manifest.json + MCP server произвольного
языка. Manifest: id, enabled, server {command,args,env?}; в args раскрываются
${extension} и ${workspace}. /cc reload атомарно перечитывает манифесты.
Claude Code может писать расширения и вызвать minecraft.reload_extensions.
Новые инструменты действуют со следующего запроса; текущий сохраняет свой snapshot.
Мгновенное добавление инструментов посреди одного agent turn ещё не реализовано.
Не обещай hot swap Java-байткода или автоматическую установку зависимостей.

## Проверка и ближайшая работа

23 теста ядра проходят (Python 3.14, SDK 0.2.174): `cd bridge && python -m unittest -v`.
HTTP middleware тестируется в памяти, без реального TCP. Нет подтверждённой
запуска в Minecraft, реального SDK/API, Docker image (демон Docker не был запущен).

1. (сделано) f/force, c/chats, тесты; зависимости сверены; Gradle build проходит.
2. Прогони test_sdk_integration на авторизованном CLI.
3. Проверь кнопки в игре, отмену, гонки force/ResultMessage и смену чатов.
4. Проверь Docker/host shell/SSH и загрузку MCP-расширений.
5. Подготовь собираемый JAR, обнови документацию и закрепи версии.

## GitHub

Репозиторий: akijke/claudecraft (private). Не отправляй .env, ключи, connection.json,
bridge-token, chats.json, bridge-config.json, рабочие каталоги или историю Claude.
