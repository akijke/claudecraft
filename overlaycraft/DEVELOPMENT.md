# Overlaycraft — справка для разработки

Аддон Claudecraft (Minecraft 26.3, Fabric Loader 0.20.1, Fabric API 0.165.0+26.3, Java 25).
Отдельный мод: id `overlaycraft`, пакет `dev.overlaycraft`, код основного мода не копирует и
работает без него. Новая версия = новый раздел сверху.

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
