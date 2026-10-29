# Overlaycraft

Аддон Claudecraft: полупрозрачные карточки поверх игры.

- **Музыка**: всё, что играет на ПК, через медиасессии Windows: YouTube, SoundCloud, VK, huevo.su
  и любые сайты в браузере, Telegram Desktop, Spotify, Яндекс Музыка. Название, исполнитель,
  прогресс, кнопки ⏮ ⏯ ⏭, переключение между источниками (1/2).
- **Яндекс Еда** (и Маркет Деливери): заведение, статус, «~12 мин», четыре шага заказа.
- **Браузер**: вкладки, где играет звук, и открытые вкладки окон; клик переключает на вкладку.

Видно в игре и в чате. В инвентаре и других меню скрыто (`/ov screens hide`, по умолчанию) или
приглушено под окном (`/ov screens behind`).

## Управление

В чате (T) мышь свободна:
- тяни карточку куда угодно (липнет к краям, карточки не наезжают друг на друга);
- ПКМ — свернуть в одну строку и обратно;
- ✖ — скрыть (`/ov show music|food|tabs` или `/ov show all` — вернуть);
- кнопки на карточках работают.

Команды: `/ov` (справка), `on|off`, `show|hide|compact <карточка>`, `screens hide|behind`,
`opacity 0..100`, `reset`, `toggle|play|pause|next|prev`, `food [мин] <статус>`, `food clear`,
`ext`, `claude`, `status`.

Claude: при старте аддон сам ставит MCP-расширение в workspace Claudecraft
(`<workspace>/extensions/overlaycraft`), после `/cc reload` у Claude есть инструменты
`overlay_status`, `overlay_media`, `overlay_browser`, `overlay_food`, `overlay_layout` —
можно сказать «поставь на паузу», «переключи трек», «передвинь музыку в левый верхний угол».
Для Claude Code на ПК:

```bash
claude mcp add overlaycraft -- python "<папка игры>/config/overlaycraft/claude-extension/server.py"
```

## Расширение браузера (вкладки и Яндекс Еда)

Музыка работает без него. Для вкладок и заказа: `/ov ext` откроет папку
`config/overlaycraft/browser-extension`. В Opera GX / Chrome / Яндекс Браузере / Edge: страница
расширений → режим разработчика → «Загрузить распакованное» → эта папка.

Заказ расширение берёт из ответов, которые загружает сама страница Яндекс Еды (открой
отслеживание заказа хотя бы раз), и потом раз в 30 секунд запрашивает тот же адрес. Если
формат страницы изменится и заказ не появится — `/ov food 25 Готовят` или попроси Claude.

## Устройство и безопасность

- `media.ps1` (PowerShell 5.1, WinRT `GlobalSystemMediaTransportControlsSessionManager`):
  JSON-строки о сессиях в stdout, команды в stdin. Только Windows.
- Локальный HTTP `127.0.0.1:18745`, наружу не слушает. `/ext/*` — для расширения, запросы
  с веб-страниц (Origin http/https) отклоняются. `/api/*` — для Claude, по токену из
  `~/.overlaycraft/connection.json`.
- Вкладки из инкогнито не отправляются. Данные никуда, кроме игры на этом ПК, не уходят.
- Настройки: `config/overlaycraft/overlay.json`.

## Проверка

- `gradlew :overlaycraft:runShots` — карточки с тестовыми данными, PNG в `overlaycraft/run/screenshots`.
- `gradlew :overlaycraft:runLiveTest` — тестовый мир: игра, чат (перетаскивание, сворачивание),
  инвентарь (скрыто / приглушено). Отчёт `overlaycraft/run/livetest/overlay-report.md`.
