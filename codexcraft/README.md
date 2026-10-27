# Codexcraft

Отдельный клиентский аддон для Claudecraft. Minecraft Java 26.3, Java 25,
Fabric Loader 0.20.1+, Fabric API. Работает одновременно с Claude:
у аддона свои Java-пакеты, клавиша, HTTP-порт, настройки и чаты.

## Установка

1. Положи `claude-chat-1.1.0.jar` и `codexcraft-1.1.0.jar` в `mods` профиля.
2. Установи Python 3.11+ и официальный Codex CLI. Проверь `codex --version`.
3. Запусти Minecraft. Python-мост распакуется и запустится автоматически.
4. `/codex settings` или `/codexcraft` → «Войти через ChatGPT» → открой
   страницу входа, скопируй код и введи его **на странице**. Если уже выполнен
   `codex login` на ПК, используется существующая авторизация.
5. **J** открывает окно Codexcraft; **K** остаётся у Claudecraft.

Аддон использует `codex app-server` по stdio, а не Anthropic API/SDK.
Python-зависимость только `aiohttp`. Codex CLI устанавливается отдельно.
Авторизация хранится средствами Codex; мод не копирует токены из его хранилища.
API-авторизацию можно настроить через `codex login --with-api-key` в терминале.

## Команды

| Команда | Действие |
| --- | --- |
| `/gpt <текст>` | Разговор через Codex в read-only sandbox |
| `/codex <задача>` | Работа с кодом в выбранном проекте |
| `/gpt ui`, `/codex ui` | Окно переписки (J) |
| `/gpt new`, `/codex new` | Новый отдельный диалог |
| `/gpt chats`, `/codex chats` | Выбор чата |
| `/<режим> q <текст>` | Очередь текущего чата |
| `/<режим> f <текст>` | Дописать в активный ход через `turn/steer`, без перезапуска |
| `/<режим> cancel` | `turn/interrupt`, остановка и очистка очереди |
| `/<режим> restart` | Остановить запрос; сохранить thread ID для продолжения |
| `/codex project pick` | Выбрать папку проекта на ПК |
| `/codex project` | Проекты; также open/add/new/rename/remove |
| `/codex ls`, `cat`, `tree`, `find`, `cd` | Файловый менеджер |
| `/codex git` | Git текущего проекта |
| `/codex sh <команда>` | Команда в системном shell с выводом в игру |
| `/codex spawn <задача>`, `tasks`, `stop <id>` | Параллельные задачи |
| `/codex connectors`, `connect`, `reload` | MCP и расширения для следующего запроса |
| `/codex model <id>`, `/codex model default` | Модель / модель из настроек CLI |
| `/codex tokens` | Токены текущего чата, без оценки стоимости |
| `/codex auto off\|edits\|all` | Политика для следующего запроса |

Все обычные операции проектов, файлов и Git наследуются от Claudecraft.
Глобальные `/sh`, `/git`, `/ps` аддон не регистрирует, чтобы не конфликтовать
с основным модом. Используй их с префиксом `/codex` или внутри окна аддона.

`auto off` (по умолчанию): read-only sandbox с запросом разрешения на выход
за ограничения. `edits`: workspace-write, дополнительные разрешения спрашиваются.
`all`: workspace-write и автоматическое подтверждение запросов команд/файлов;
это не отключает sandbox. В `/gpt` повышение доступа отклоняется при любом auto.
Прямые команды пользователя `git`, файлового менеджера и `sh` выполняются мостом
с правами пользователя ПК, как в Claudecraft.

Если `f` пришло после завершения хода, оно выполняется следующим запросом в том
же диалоге. Сессии сохраняются как Codex thread IDs; Claude session IDs не импортируются.
Агенты Claude и их пресеты не переносятся. Для Codex используются его `AGENTS.md`,
настройки CLI и обычный `spawn <задача>` без `@имя`.

## Конфигурация и сборка

Данные: `config/codexcraft/`; порт по умолчанию **18742** (Claudecraft — 18741).
В `bridge-config.json` можно добавить `codex_path` (абсолютный путь к CLI)
и `codex_model`. Без модели аддон сохраняет выбор из конфигурации Codex.
Путь к Python настраивается в `/codex settings`.
Лог: `config/codexcraft/runtime/bridge.log`.

Из корня репозитория:

```powershell
.\gradlew.bat :codexcraft:build
.\.venv\Scripts\python.exe -m unittest discover -s codexcraft/tests -v
```

Результат: `codexcraft/build/libs/codexcraft-1.1.0.jar`.
`generateShared` собирает приватную копию общего UI и workspace-кода из
исходников Claudecraft; Codex-протокол и экран входа находятся в этом модуле.
Исходники основного мода при генерации не изменяются.

`CODEXCRAFT_CLI_IT=1` включает тест настоящего CLI: initialize и account/read,
без запроса к модели. `gradlew :codexcraft:runShots` запускает оба мода,
снимает демонстрационные экраны и завершает игру.

Проверки не заменяют ручной прогон живой модели, входа по коду и внешних MCP.
App Server развивается: используйте актуальный CLI с методами v2.
Протокол: https://learn.chatgpt.com/docs/app-server
