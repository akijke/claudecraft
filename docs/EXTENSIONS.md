# Расширения и права процесса

Структура проекта:

- `src/client/` — Minecraft UI, команды, кнопки, HTTP-клиент.
- `bridge/bridge.py` — очередь, сессии, HTTP и расширения.
- `bridge/backends/` — независимые реализации Anthropic API и Claude Agent SDK.
- `<workspace>/extensions/<id>/manifest.json` — декларация MCP-расширения.
- `<workspace>/extensions/<id>/server.py` — пример реализации, язык произвольный.

`workspace` задаётся в `config/claude-chat/bridge-config.json`. По умолчанию это
`config/claude-chat/workspace`, а не вся папка Minecraft. В Docker используется
`MC_WORKSPACE=/projects`.

Скопируй `examples/extensions/system-info` в `<workspace>/extensions/`.
Укажи в `server.command` абсолютный путь к Python из venv моста, если `python`
в PATH указывает на другую среду. После `/cc reload` следующий запрос получит
новый инструмент. Сам Minecraft перезапускать не нужно. `/cc tools` показывает
ОС, статус прав, доступные исполняемые файлы и MCP-серверы.

В manifest поддерживаются `id`, `enabled` и `server` с `command`, `args`,
опциональным `env`. В args раскрываются `${extension}` и `${workspace}`.
Расширения запускаются стандартным MCP stdio transport. Логи сервера пиши в
stderr, stdout оставь протоколу MCP. Зависимости расширения устанавливаются в
его окружение отдельно; reload не выполняет установку пакетов.

Claude Code может писать такие файлы через `/cc` и вызвать встроенный инструмент
`minecraft.reload_extensions`; также пользователь может выполнить `/cc reload`. Если JSON некорректен, прежняя конфигурация остаётся активной.
Текущий запрос использует старый набор инструментов; новый набор действует со
следующего запроса из очереди. `f <запрос>` попадает в ту же живую сессию и
поэтому видит старый набор. Чтобы применить сразу: `/cc cancel`, затем новый запрос
(cancel — явная остановка текущей работы).
Это замена инструментов, а не изменение байткода работающего Minecraft.

Другие MCP-серверы можно прописать в `bridge-config.json` → `mcp_servers`.
Например Playwright MCP при установленном Node.js:

```json
{
  "browser": {
    "command": "npx",
    "args": ["-y", "@playwright/mcp@latest"]
  }
}
```

Этот объект — значение `mcp_servers`, сохрани остальные поля конфигурации.
Пакет и браузерные зависимости должны быть установлены на машине моста.
Воспроизводимая установка требует закрепить проверенную версию вместо latest.
Браузер MCP работает рядом с мостом. Это не встроенный экран браузера в Minecraft.
В Docker графическому браузеру нужен отдельный display/VNC или headless-режим.

## Администратор, SSH, CMD, Docker

Claude имеет права учётной записи моста. Для настоящего доступа к Windows CMD,
PowerShell и локальному Docker Desktop запускай мост на Windows, где установлены
Claude Code и Git Bash. Windows нативный CLI для shell-инструментов требует Git
Bash. SSH использует существующие ключи/ssh-agent/known_hosts этой учётной записи.
Вход, MFA, пароль sudo и ключи не имитируются кнопками и не обходятся модом.

Если нужны права администратора Windows, запусти терминал через «Запуск от имени
администратора» и запускай мост оттуда. В Linux root-права означают запуск моста
под root; предпочтительнее доступ конкретного пользователя к Docker/SSH и sudo
для нужных команд. Мод не выдаёт себе права и не отключает системные запросы UAC.
Root в контейнере не равен администратору Windows или root на хосте.

SDK работает в стандартном режиме разрешений. Операции, которые SDK отправляет
на подтверждение, показываются в Minecraft. Чтение и другие автоматически
разрешённые SDK операции могут выполняться без кнопки. Разрешение даётся одному
запросу инструмента, режим bypassPermissions не включается.

Docker socket даёт широкие права над хостом через Docker daemon. Поэтому он
подключается отдельным opt-in compose-файлом. Не добавляй privileged: true:
для обычной работы с Docker socket это не нужно. SSH в контейнере требует отдельно
настроенного агента или ключей; host SSH настройки автоматически не монтируются.

## Docker

Из `bridge/`, задай переменные окружения (абсолютные пути):

```sh
export MC_GAME_DIR=/path/to/minecraft-instance
export CLAUDE_WORKSPACE=/path/to/project
export BRIDGE_UID=$(id -u)
export BRIDGE_GID=$(id -g)
export ANTHROPIC_API_KEY=...  # также используется SDK в контейнере
# Уже существующие папки должны быть доступны этому UID/GID.
docker compose up --build -d
```

Linux, с доступом к хостовому Docker daemon:

```sh
export DOCKER_GID=$(stat -c %g /var/run/docker.sock)
docker compose -f compose.yaml -f compose.host-docker.yaml up --build -d
```

Порт публикуется только на loopback хоста. Внутри контейнера мост слушает
0.0.0.0, снаружи — 127.0.0.1. Python на хосте по умолчанию слушает только loopback.
Токен моста записывается в `connection.json`, не в чат и не в журнал.
В Windows запускай host bridge для прямого доступа к Windows-инструментам:
Linux-контейнер видит Linux shell и смонтированные каталоги.

Dockerfile и compose подготовлены, но сборка контейнера в текущей среде не
выполнена. SDK и браузерные интеграции требуют проверки на целевой машине.
