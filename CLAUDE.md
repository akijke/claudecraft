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
Алиасы: queue=q, force=f, c/chats=chat. Реальный CLI ещё не прогнан:
`CLAUDECRAFT_SDK_IT=1 python -m unittest test_sdk_integration` (нужен claude auth login).

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

22 теста ядра проходят (Python 3.14, SDK 0.2.174): `cd bridge && python -m unittest -v`.
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
