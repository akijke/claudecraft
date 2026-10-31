# Overlaycraft — справка для разработки

Аддон Claudecraft (Minecraft 26.3, Fabric Loader 0.20.1, Fabric API 0.165.0+26.3, Java 25).
Отдельный мод: id `overlaycraft`, пакет `dev.overlaycraft`, код основного мода не копирует и
работает без него. Новая версия = новый раздел сверху.

---

## 0.4.0 — 2026-10-29: вшит в Claudecraft

### Что изменилось
- **Один jar, одна игра.** Корневой build.gradle: `include project(':overlaycraft')` (и так же
  `:codexcraft`, `:geminicraft`) — `build/libs/claude-chat-1.1.0.jar` содержит
  `META-INF/jars/overlaycraft-0.4.0.jar`, `codexcraft-1.1.0.jar`, `geminicraft-1.1.0.jar`, а
  `fabric.mod.json` — их записи `"jars"`. В профиль игрока кладётся только claude-chat.
- **Dev-запуск:** `gradlew :runClient` / `:runShots` / `:runLiveTest` грузят все четыре мода
  (jar аддонов в `localRuntime`). `installToClaudecraft` больше не нужен: задача оставлена
  безвредной (только удаляет старую копию из `run/mods` и пишет подсказку). Каждый запуск
  корня перед стартом сам удаляет `run/mods/{overlaycraft,codexcraft,geminicraft}-*.jar` —
  иначе Fabric падает на повторном id мода.
- Аддоны Codex/Gemini для своих отдельных запусков (`:codexcraft:runShots`) берут
  `claude-chat-…-bare.jar` (задача `bareJar`, без вложенных аддонов) — без дублей.
- **Палитра Claudecraft:** `Palette.java` раз в 2 с читает (рефлексией, без зависимости)
  `dev.claudechat.ChatStyle` (TEXT, DIM, FAINT, GOLD, CLAUDE) и фон окон `Theme.SCREEN_BG`;
  карточки следуют `/claude theme`. На теме light фон карточек остаётся тёмным (текст чата
  Claudecraft светлый во всех темах). Без Claudecraft — те же значения тёмной темы по умолчанию
  (фон 0x151518 вместо 0x0B0B0E, DIM 0xA8A8A8, FAINT 0x8C8C8C).
- Справка `/c`, `/cc` Claudecraft: строка «В комплекте:» с кнопками Codex (J) /gpt, Gemini (G) /gem,
  Оверлей /ov (только для загруженных аддонов).
- HUD-панель задач (Notify): у Geminicraft своя строка (y=168), раньше она лежала на панели Codexcraft.

### Проверено
- `gradlew build` — ок, jar содержит три вложенных аддона.
- `gradlew :runShots` в worktree: «Loading 55 mods» — claude_chat 1.1.0, codexcraft 1.1.0,
  geminicraft 1.1.0, overlaycraft 0.4.0; подложенный `run/mods/overlaycraft-0.3.0.jar` удалён
  перед стартом; без крэша. Порт 18745 был занят открытой игрой пользователя
  («Address already in use») — ожидаемо, на загрузку модов не влияет.
- `gradlew :overlaycraft:runShots` (отдельно, без Claudecraft) — карточки рисуются.

### Не проверено
- Цвета карточек при живом Claudecraft (рефлексия) на скриншоте: в `:runShots` корня карточки
  пусты (нет музыки/заказа). Строка «В комплекте:» в справке в игре не открывалась.

### Скриншоты и описание функций (после 0.4.0)
- `DevShots` снимает 9 состояний по очереди (игра, чат, свёрнуто, этапы 0/1/3, «войди», «не подключён»,
  приглушено); имена по порядку — `run/screenshots/shots.txt`. Настройки не сохраняет, раскладку сбрасывает.
- Готовые снимки и страница с описанием всего функционала: `overlaycraft/docs/features.html` + `docs/screens/`
  (10–14 — из `:overlaycraft:runLiveTest`, в настоящем мире).
- LiveTest «ПКМ сворачивает вкладки» иногда падал: после перетаскивания карточки съезжают вверх, а курсор
  ставился по прямоугольнику прошлого кадра. Теперь прицел дважды (waited 18 и 21) и в середину карточки.
- F1 (скрытый HUD) прятал карточки и в чате. Теперь в открытом чате они рисуются из afterExtract и при F1.
- Opera не открывает `opera://extensions`, переданный процессу снаружи (ни от игры, ни из консоли): вкладка просто
  не появляется. `/ov ext` теперь просит нажать Ctrl+Shift+E в браузере; путь к папке по-прежнему в буфере.
- Клики в чате вместе с Claudecraft: карточка, лежащая на уголке/краю чата или его панели `↺ < >`, уступает
  клик чату — `Hud.press` спрашивает `ChatSize.claims(x, y)` (рефлексией, `Palette.chatClaims`). Иначе порядок
  хуков двух модов решал, кто получит клик, и в тесте Claudecraft «↺» нажимал «Подключить» у оверлея.
- Перетаскивание и уголок чата шли только по кадрам (afterExtract). При `inactivityFpsLimit: afk` окно без ввода
  рисует ~10 кадров/с, и отпускание через два тика после последнего движения оставляло карточку на полпути.
  Теперь `release(x, y)` сначала применяет позицию курсора из события отпускания. То же в `ChatSize`.
- В Codexcraft/Geminicraft лежат копии `ChatSize`; хуки чата регистрирует только одна (`ChatSize.OWNER`:
  Claudecraft, иначе первый аддон), остальные молчат — иначе каждая копия ловила клики и рисовала свою панель.

---

## 0.3.0 — 2026-10-29

### Что изменилось
- **Яндекс Еда через настоящий API сайта** (адреса найдены в коде eda.yandex.ru, сборка
  `eats.desktop`, appVersion 26.50.8). Фон расширения каждые 30 с:
  1. `GET https://eda.yandex.ru/eats/v1/eats-orders-tracking/v1/tracking-for-mobile/check` →
     `{ tracked_order: { order_nr } }` (нет `tracked_order` — нет заказа);
  2. `GET …/tracking-for-desktop?order_nr=…` (запасной `…/tracking-for-mobile/full-update`) —
     виджеты серверной разметки (`payload.title.value`, `subtitle`, `progress`…).
  Заголовки как у сайта: `X-Platform: desktop_web`, `X-App-Version`, cookie-сессия
  (`credentials: include`) и `Authorization: Bearer <cookie webapitoken>` (так делает сам сайт:
  `authToken: F.get("webapitoken")`). Токен уходит только на eda.yandex.ru, в игру — никогда.
  401/403 → состояние `login`: карточка «Еда: войди в браузере» + кнопка «Войти».
  То же для `market-delivery.yandex.ru`.
- Разбор ответа — по словам, не по структуре (разметка у Яндекса часто меняется):
  `overlaycraftFromTracking()` собирает тексты виджетов по порядку; этап = первый текст с
  фразой этапа (`STAGES`), минуты = «N мин» / «в|к HH:MM», заведение = `place.name`.
  Был отслеживаемый заказ и пропал из `check` → «Заказ завершён» (этап 3).
- Расширение шлёт `order: {none: true}` и `eda: ok|login|error`; мод снимает автоматический
  заказ (ручной остаётся), `/api/state` отдаёт `yandex_eda`.
- **Уголок размера чата Claudecraft** (`ChatSize`) — та же болезнь, что у карточек в 0.2.0
  (после забранного клика нет `mouseDragged`): теперь тянется в `afterExtract`, размер меняется
  только после реального сдвига мыши (двойной клик — сброс — не затирается).
  `LiveTest` Claudecraft: шаг «the corner grip resizes the chat with the mouse» (настоящая мышь).
- Подсказка подключения браузера: про ⟳ и переключатель (см. «Подвохи»).

### Найдено при проверке
- `gradlew runClient` из корня запускает **все** подпроекты с `runClient` — две игры, вторая
  занимает порт 18745, и в окне Claudecraft сервер оверлея не стартует
  («Address already in use»). Запуск — только `gradlew :runClient`.
- Браузер **выключает** распакованное расширение, если новая версия просит больше разрешений
  (0.3.0: `cookies`, `scripting`, музыкальные сайты). После ⟳ — включить переключатель.
  После обновления файлов фон расширения сам не перезагружается — нужен ⟳.

### Проверено
- `gradlew build` (все проекты, JUnit) — ок.
- `gradlew :runLiveTest` (Claudecraft): 50/50, включая уголок чата настоящей мышью.
- Автотест оверлея **внутри Claudecraft** (`JAVA_TOOL_OPTIONS=-Doverlaycraft.livetest=true
  gradlew :runClient`): 6/6.
- Расширение в отдельной Opera GX (`--user-data-dir`, `--load-extension`, `--mute-audio`,
  отладка через `--remote-debugging-port`): подключается к игре, реальный запрос к Яндекс Еде
  без входа → `401` → игра показывает `yandex_eda: login`.
- `overlaycraftFromTracking` на примерах ответов: этапы 1/2/3, минуты, «к 21:40», заведение.

### Не проверено
- Ответ `tracking-for-desktop` с настоящим активным заказом (нужен вход и заказ): разбор по
  словам должен выдержать, но формат не видели вживую.
- Принимает ли API только cookie без `webapitoken` (если токена в cookie нет).

### Отладка расширения без влияния на браузер пользователя
```bash
"%LOCALAPPDATA%\Programs\Opera GX\opera.exe" --user-data-dir=%TEMP%\oc-profile --load-extension=<папка> --remote-debugging-port=9333 --mute-audio --no-first-run
```
Дальше `http://127.0.0.1:9333/json/list` → WebSocket фонового скрипта → `Runtime.evaluate`
(`refreshOrder()`, `edaState`); ошибки и состояние — `chrome.developerPrivate.getExtensionInfo(id)`
со страницы `chrome://extensions`.

---

## 0.2.0 — 2026-10-29

### Что изменилось
- **Перетаскивание в чате работает по-настоящему.** Причина бага: если мод забрал клик
  (`ScreenMouseEvents.allowMouseClick` → false), `MouseHandler.onButton` выходит до установки
  `isLeftPressed`, и `mouseDragged` игра не шлёт. Теперь карточка идёт за курсором в
  `ScreenEvents.afterExtract` (каждый кадр) от нажатия до `allowMouseRelease`.
  ⚠ У `ChatSize` основного мода (уголок размера чата) скорее всего та же проблема.
- **Следующий/предыдущий трек на сайтах** — через расширение, а не Windows: у одиночного видео
  YouTube нет `nexttrack` для SMTC. `media-hook.js` (world MAIN) по порядку:
  YouTube `movie_player.nextVideo()/previousVideo()` (кнопка `.ytp-next-button` в новом
  интерфейсе `display:none`) → кнопки плееров (SoundCloud, VK, Яндекс Музыка, по aria-label) →
  перехваченные `navigator.mediaSession.setActionHandler` → `<video>/<audio>`.
  Ответ `how` приходит в `POST /ext/done`; при `none/no-script/timeout` мод повторяет команду
  через Windows (`Media.windows`). Play/pause по-прежнему через Windows.
- **Яндекс Еда:** запасной разбор текста страницы заказа (фразы этапов + «N мин»), если 60 с
  нет данных из ответов API; смена этапа → тост и звук в игре (`Browser.setOrder` →
  `Overlaycraft.notify`). Ручной статус (`/ov food`, Claude) сам определяет этап по словам и
  сохраняет заведение/ссылку текущего заказа.
- **Подключение браузера:** `/ov ext` и кнопка «Подключить» на карточке — копирует путь к
  папке в буфер и открывает страницу расширений браузера, где играет музыка (Opera GX, Opera,
  Яндекс, Chrome, Edge). Расширение после установки само подключается к открытым вкладкам
  (`chrome.scripting`), перезагружать их не нужно.
- **Запуск вместе с Claudecraft:** `gradlew :overlaycraft:installToClaudecraft` кладёт jar в
  `run/mods`, затем `gradlew runClient` — оба мода, общий `run/` с мостом и входом Claude.
- Автотест `runLiveTest` гоняет настоящие события мыши (`MouseHandler.onMove/onButton`) и
  работает без звука (громкость возвращается в конце).

### Проверено
- `runLiveTest` 6/6: карточки в игре, клик и перетаскивание мышью в чате, ПКМ, инвентарь.
- `media-hook.js` на живом YouTube: next/prev/pause.
- Claudecraft + Overlaycraft вместе: оба мода грузятся, инструменты Claude ставятся в
  `run/config/claude-chat/workspace/extensions/overlaycraft` и отвечают из игры.

### Не проверено
- Расширение, установленное в браузер целиком (фон, long poll, инъекция в вкладки).
- Реальные ответы и страница Яндекс Еды (разбор написан по догадкам, с запасом).
- SoundCloud/VK/huevo.su кнопки «следующий» (селекторы не проверены на живых сайтах).

---

## Устройство

| Файл | Что делает |
| --- | --- |
| `Overlaycraft.java` | вход, команды `/ov`, тосты, `connectBrowser()` |
| `Hud.java` | рисует карточки (HUD-слой), раскладка без наложений, мышь в чате |
| `Config.java` | `config/overlaycraft/overlay.json`: позиции (доли свободного места), видимость, фон |
| `Media.java` | процесс `media.ps1`, сессии Windows, команды |
| `Browser.java` | вкладки, заказ, очередь команд для расширения, определение сервиса по вкладке |
| `Server.java` | HTTP `127.0.0.1:18745`: `/ext/*` (расширение, Origin http/https отклоняется), `/api/*` (токен) |
| `Control.java` | общие действия для `/ov` и Claude |
| `Installer.java` | распаковка расширения, MCP-расширение в workspace Claudecraft, открытие страницы расширений |
| `DevShots.java`, `LiveTest.java` | `runShots`, `runLiveTest` |
| `src/client/resources/overlaycraft/media.ps1` | PowerShell 5.1 + WinRT GSMTC: JSON-строки в stdout, команды в stdin |
| `browser-extension/` | MV3: `background.js`, `eda-hook.js`/`eda-relay.js`, `media-hook.js`/`media-relay.js` |
| `claude-extension/server.py` | MCP stdio без зависимостей: `overlay_status/media/browser/food/layout` |

**HTTP API** (`Authorization: Bearer <token из ~/.overlaycraft/connection.json>`):
`GET /api/state`; `POST /api/control` с `action`: `media {command, session?}`, `pick {session}`,
`focus {tab}`, `open {url}`, `food {status?, place?, minutes?, step?}`, `food_clear`,
`widget {name, visible?, compact?, x?, y?}`, `overlay {enabled?, screens?, opacity?, reset?}`.

**Расширение ↔ игра:** `POST /ext/state {browser, tabs[], order?}`, `GET /ext/poll` (long poll
20 с, команды `focus`/`open`/`media`), `POST /ext/done {id, how}`.

### Подвохи 26.3
- Нет `Util.getPlatform().openUri/openPath` → `Browser.openOutside` (rundll32 / xdg-open / open).
- F1: `mc.gui.hud.isHidden()`; экран: `mc.gui.screen()`.
- `ScreenEvents.afterExtract` не вызывается на части экранов (онбординг) — для превью свой `Screen`.
- В .NET Framework `Console.In.ReadLineAsync` блокирует — stdin читается через `StreamReader(OpenStandardInput())`.
- Медиасессия браузера на секунду пропадает при смене видео — карточка мигает.

### Сборка и проверка
```bash
gradlew :overlaycraft:build
```
```bash
gradlew :overlaycraft:runShots
```
```bash
gradlew :overlaycraft:runLiveTest
```
```bash
gradlew :overlaycraft:installToClaudecraft
```
Тесты со звуком на ПК пользователя — только с его согласия; автотест сам глушит игру.

---

## 0.1.0 — 2026-10-29
Первая версия: карточки музыки (Windows media sessions), Яндекс Еды и вкладок; `/ov`; режимы
экранов `hide/behind`; локальный сервер; MCP-инструменты для Claude; `runShots`, `runLiveTest`.
