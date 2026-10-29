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
Мод: ConversationScreen (клавиша K, свой hit-test стилей; Enter — отправить, Shift+Enter — новая строка), KeyScreen
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

## Ночь 2026-10-29: ветка night/2026-10-29 (48 коммитов, НЕ запушена, НЕ смержена)

Полный список и что нужно от пользователя — docs/NIGHT_REPORT.md. Главное для продолжения:
- Новые модули моста (bridge/backends): commands, permrules, replace, hunks, worktrees, budget,
  rewind, memory, skills, hooks, styles, export, search, ledger, formatters, lsp (+ bridge/fake_lsp.py),
  conflicts, pulls, watch, openai_compat (ollama/lmstudio/openrouter), doctor, guests (#69),
  transfer (#81). Константы в bridge.py: OAI_BACKENDS, HTTP_BACKENDS.
- Мод: EditorScreen, Highlighter, TerminalScreen, PaletteScreen, IdeScreen, DashboardScreen, Theme,
  Sounds, OsNotify, InputHistory, PathLinks, Achievements (#85). JUnit: src/test/java (16 тестов).
- Окно игры в 26.3 — SDL3 (org.lwjgl.sdl.SDLVideo), не GLFW.
- Geminicraft (geminicraft/): Gemini CLI через ACP (`gemini --experimental-acp`), /gem и /gemini,
  клавиша G, порт 18743. Как и Codexcraft, генерирует код из основного мода заменой строк, поэтому
  литералы 'codexcraft'/'/cc' в bridge.py собираются из частей.
- #70: client.json → "remote" {port, token}: request() идёт на 127.0.0.1:<порт> SSH-туннеля, локальный
  мост не стартует. #69: гостевые токены (в guests.json только хеш), только /guest/events и /guest/say.
- Проверка: `$TEMP/check.sh` (сборка, тесты моста с CLAUDECRAFT_CLI_IT=1, тесты аддонов) и
  `gradlew runLiveTest` (41/41). Gemini CLI, Ollama, LSP-серверы, форматтеры, CI, настоящий gh — не
  проверялись: на этом ПК их нет, ветка не запушена.
- Открытые вопросы к пользователю: мерж и push ветки; три решения по #69 (транспорт, клиент гостя,
  auto off для гостей); пункт 4 (держать CLI живым); достижения по умолчанию.

## Проверка агентами 2026-10-29 (коммиты 6960517, 639aa00, b9146d7)

- backends/trust.py: доверенные папки (trust.json, не экспортируется). Недоверенная папка: в SDK
  только setting_sources=['user'], её allow-правила игнорируются (deny/ask действуют). Клон —
  недоверенный, выбранная/созданная пользователем папка — доверенная. /cc perms trust|untrust.
- permrules: allow не совпадает с цепочками команд (COMPOUND), deny/ask проверяют каждую часть.
- transfer: SAFE_CONFIG — единственные ключи bridge-config, которые экспорт/импорт трогают.
- Гость: chat.guest_turn → ничего не одобряется автоматически; history гостю не отдаётся.
- auto edits: запись только внутри папки чата (sdk.auto_allows(chat, name, path, folder)).
- Аддоны: i18n.py BRAND заменяется в их build.gradle (replaceFirst) — переводы брендированных строк.
- Python на Windows пишет CRLF при open(..., 'w'): замены строк в gradle — через регулярки.

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

114 тестов моста проходят (Python 3.14, SDK 0.2.174, на 2026-10-29): `cd bridge && python -m unittest -v`.
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

## Overlaycraft (2026-10-29): аддон overlaycraft/

Отдельный мод (НЕ копирует код основного, работает и без Claudecraft), id overlaycraft,
пакет dev.overlaycraft, порт 18745. Полупрозрачные карточки HUD: музыка (медиасессии Windows
через media.ps1 — WinRT; Console.In.ReadLineAsync в .NET Framework блокирует, поэтому StreamReader
над OpenStandardInput), Яндекс Еда и вкладки браузера (browser-extension/, MV3, заказ ловится из
fetch/XHR страницы и переспрашивается фоном). Видно в игре и в чате (перетаскивание, ПКМ —
свернуть, ✖ — скрыть), в других экранах скрыто или приглушено (/ov screens). Claude управляет
через claude-extension/server.py (MCP без зависимостей), аддон ставит его в workspace сам.
В 26.3 нет Util.getPlatform().openUri/openPath (открываем через rundll32), F1 = gui.hud.isHidden().
Проверка: `gradlew :overlaycraft:runShots`, `gradlew :overlaycraft:runLiveTest` (15/15).
Не проверено: настоящее расширение в браузере и реальный ответ Яндекс Еды; кнопки
play/pause на живой музыке (не трогали музыку пользователя).
Overlaycraft 0.2.0: справка для разработки — overlaycraft/DEVELOPMENT.md (раздел на каждую версию,
пользователь просил давать её с каждой версией). Запуск вместе с Claudecraft:
(устарело, см. «Всё в одном jar») `gradlew :overlaycraft:installToClaudecraft`, потом `gradlew runClient`.
Подозрение: ChatSize (уголок размера чата) не получает mouseDragged после отменённого клика — см. DEVELOPMENT.md.
Overlaycraft 0.3.0: Яндекс Еда через API сайта (eats-orders-tracking/v1/tracking-for-mobile/check →
tracking-for-desktop, Bearer из cookie webapitoken). Запускать игру ТОЛЬКО `gradlew :runClient`
(без двоеточия стартуют все подпроекты, и порт 18745 занимает лишняя игра). ChatSize тянется в afterExtract
(LiveTest-шаг с настоящей мышью). Подробности — overlaycraft/DEVELOPMENT.md.

## Всё в одном jar (2026-10-29, #6)

Пользователь: «кодекс отвалился», «пришей всё обратно». Теперь корневой build.gradle
(`ext.ADDONS`) делает `include project(':codexcraft' | ':geminicraft' | ':overlaycraft')` —
jar-in-jar в claude-chat-<версия>.jar (META-INF/jars) — и `localRuntime` их jar для dev-запусков.
`gradlew :runClient` (с двоеточием!) = все четыре мода в одной игре. installToClaudecraft больше не
нужен (оставлен безвредным); любой JavaExec корня удаляет run/mods/<аддон>-*.jar (дубль id = Fabric не
стартует). Аддоны Codex/Gemini для своих запусков берут root `bareJar` (без вложенных аддонов).
Порты: 18741 Claude, 18742 Codex, 18743 Gemini, 18745 оверлей; клавиши K/J/G; панели задач Notify
на y=4/86/168. Overlaycraft 0.4.0 берёт палитру Claudecraft рефлексией (Palette.java).

## Картинки и видео нейросетей (#7, 2026-10-29)

bridge/backends/media.py (только urllib): картинки — OpenAI Images (gpt-image-1) / Gemini
(gemini-2.5-flash-image), видео — OpenAI /v1/videos (Sora, опрос) / Veo (predictLongRunning, опрос).
Ключи: secrets.json openai_api_key / gemini_api_key (`/claude key media` → KeyScreen → action media_key),
затем OPENAI_API_KEY / GEMINI_API_KEY / GOOGLE_API_KEY, ~/.codex/auth.json, config/geminicraft/secrets.json.
Модели — bridge-config.json "media". Файлы: config/claude-chat/media/*.png|mp4 + .json, index.json (галерея;
мод читает его сам — MediaStore.java). Команды: /cc draw, /cc video, /cc img gen, /cc media [gen|list|cancel|
open|folder|frames|add|key|view]. Bridge.emit → media.observe: свежий файл картинки/видео, названный агентом
(текст или Write-инструмент, проверка на done), попадает в галерею и чат (событие media). Мод: миниатюры в окне K
(ConversationScreen.addThumb), MediaScreen (зум/пан, ←/→, «Открыть на ПК» через мост, «Отправить в чат» =
[[image:…]] в поле ввода), MediaGalleryScreen (/cc media). Видео: ffmpeg → media/frames/<id>/*.jpg + frames.json,
играет без звука одной текстурой. ffmpeg на этом ПК нет; реальные API не вызывались (тесты с FakeHttp).
