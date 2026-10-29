package dev.claudechat;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.components.Tooltip;
import net.minecraft.client.gui.screens.ConfirmLinkScreen;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.CommonComponents;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.Style;
import net.minecraft.util.FormattedCharSequence;
import java.net.URI;
import java.util.concurrent.CompletableFuture;

/**
 * /c settings or /claude. Two columns: bridge + API key on the left, Claude account on
 * the right, so it fits at GUI scale 4 on 1080p. Talks to the bridge over HTTP.
 */
final class SettingsScreen extends Screen {
    private static final int WHITE = 0xFFFFFFFF, GRAY = 0xFFA0A0A0, DARK = 0xFF707070, GREEN = 0xFF6BD08B,
        RED = 0xFFF0626A, GOLD = 0xFFF2C14E, CLAUDE = 0xFFD97757, PANEL = 0x90000000;
    private final ClaudeChat mod;
    private final Screen parent;
    private final boolean preview;
    private JsonObject status;
    private String message = "";
    private int messageColor = GRAY;
    private long nextPoll;
    private volatile boolean busy;
    private boolean autoStart;
    private String codeValue = "", keyValue = "", pythonValue;
    private EditBox code, apiKey, python;
    private String layoutKey = "";
    private int columnWidth, leftX, rightX, top;

    SettingsScreen(ClaudeChat mod, Screen parent) { this(mod, parent, null); }

    private SettingsScreen(ClaudeChat mod, Screen parent, JsonObject fixedStatus) {
        super(Component.literal("Claudecraft"));
        this.mod = mod;
        this.parent = parent;
        this.preview = fixedStatus != null;
        this.status = fixedStatus;
        this.autoStart = mod.launcher.autoStart();
        this.pythonValue = mod.launcher.pythonSetting();
    }

    /** Dev screenshots: fixed status, no bridge calls. */
    static SettingsScreen preview(ClaudeChat mod, JsonObject status) { return new SettingsScreen(mod, null, status); }

    private String loginState() {
        if (status == null || !status.has("login")) return "idle";
        return status.getAsJsonObject("login").get("state").getAsString();
    }
    private JsonObject account() {
        return status != null && status.has("account") ? status.getAsJsonObject("account") : null;
    }
    private boolean loggedIn() {
        JsonObject a = account();
        return a != null && a.get("logged_in").getAsBoolean();
    }
    private boolean waitingForCode() {
        String s = loginState();
        return s.equals("waiting_code") || s.equals("checking") || s.equals("starting");
    }
    private static String str(JsonObject o, String key) {
        JsonElement e = o == null ? null : o.get(key);
        return e == null || e.isJsonNull() ? null : e.getAsString();
    }

    @Override protected void init() {
        if (code != null) codeValue = code.getValue();
        if (apiKey != null) keyValue = apiKey.getValue();
        if (python != null) pythonValue = python.getValue();
        columnWidth = Math.min(190, (width - 40) / 2);
        leftX = width / 2 - columnWidth - 10;
        rightX = width / 2 + 10;
        top = Math.max(6, (height - 230) / 2) + 20;
        int cw = columnWidth;

        // ---- left: bridge ----
        addRenderableWidget(Ui.button(Component.literal(L.t("⟳ Перезапустить мост")), b -> restartBridge())
            .bounds(leftX, top + 34, cw, 20).dark().build());
        addRenderableWidget(Ui.button(autoLabel(), b -> { autoStart = !autoStart; b.setMessage(autoLabel()); })
            .bounds(leftX, top + 58, cw / 2 - 2, 20)
            .tooltip(Tooltip.create(Component.literal(L.t("Запускать мост вместе с игрой")))).dark().build());
        // Language of the mod and of bridge messages (Claude answers in the language you write in).
        addRenderableWidget(Ui.button(Component.literal("Lang: " + (L.russian() ? "RU" : "EN")), b -> {
                mod.launcher.saveSettings(autoStart, python.getValue());
                mod.launcher.setLanguage(L.russian() ? "en" : "ru");
                rebuildWidgets();
            }).bounds(leftX + cw / 2 + 2, top + 58, cw / 2 - 2, 20)
            .tooltip(Tooltip.create(Component.literal(L.t("Language / Язык: English ⇄ Русский")))).dark().build());
        python = new Ui.Field(font, leftX, top + 82, cw, 18, Component.literal("Python")).dark();
        python.setMaxLength(500);
        python.setHint(Component.literal(L.t("путь к python.exe (пусто = авто)")));
        python.setValue(pythonValue == null ? "" : pythonValue);
        addRenderableWidget(python);

        // ---- left: API key ----
        apiKey = new Ui.Field(font, leftX, top + 136, cw - 66, 18, Component.literal(L.t("API-ключ"))).dark();
        apiKey.setMaxLength(300);
        apiKey.setHint(Component.literal("sk-ant-…"));
        apiKey.addFormatter((text, offset) -> FormattedCharSequence.forward("•".repeat(text.length()), Style.EMPTY));
        apiKey.setValue(keyValue);
        addRenderableWidget(apiKey);
        addRenderableWidget(Ui.button(Component.literal("✔"), b -> {
            String key = apiKey.getValue().strip();
            if (!key.isEmpty()) { action("apikey", key); keyValue = ""; apiKey.setValue(""); }
        }).bounds(leftX + cw - 62, top + 135, 30, 20).tooltip(Tooltip.create(Component.literal(L.t("Сохранить ключ")))).dark().build());
        addRenderableWidget(Ui.button(Component.literal("✖"), b -> action("apikey", ""))
            .bounds(leftX + cw - 30, top + 135, 30, 20).tooltip(Tooltip.create(Component.literal(L.t("Удалить ключ")))).dark().build());

        // ---- right: account ----
        if (waitingForCode()) {
            String url = str(status.getAsJsonObject("login"), "url");
            Button open = addRenderableWidget(Ui.button(Component.literal(L.t("1. Открыть страницу входа")), b -> {
                if (url != null) ConfirmLinkScreen.confirmLinkNow(this, URI.create(url));
            }).bounds(rightX, top + 34, cw, 20).dark().build());
            open.active = url != null;
            code = new Ui.Field(font, rightX, top + 59, cw, 18, Component.literal(L.t("Код"))).dark();
            code.setMaxLength(2000);
            code.setHint(Component.literal(L.t("2. вставь код (Ctrl+V)")));
            code.setValue(codeValue);
            addRenderableWidget(code);
            addRenderableWidget(Ui.button(Component.literal(L.t("3. Отправить код")), b -> {
                action("login_code", code.getValue().strip());
                codeValue = "";
                code.setValue("");
            }).bounds(rightX, top + 82, cw - 64, 20).dark().build());
            addRenderableWidget(Ui.button(CommonComponents.GUI_CANCEL, b -> action("login_cancel", ""))
                .bounds(rightX + cw - 60, top + 82, 60, 20).dark().build());
        } else {
            code = null;
            if (loggedIn()) {
                addRenderableWidget(Ui.button(Component.literal(L.t("Выйти из аккаунта")), b -> action("logout", ""))
                    .bounds(rightX, top + 34, cw, 20).dark().build());
            } else {
                addRenderableWidget(Ui.button(Component.literal(L.t("Войти: подписка Claude")), b -> action("login", ""))
                    .bounds(rightX, top + 34, cw, 20)
                    .tooltip(Tooltip.create(Component.literal(L.t("Pro / Max — как claude auth login")))).dark().build());
                addRenderableWidget(Ui.button(Component.literal(L.t("Войти: Console (API)")), b -> action("login", "console"))
                    .bounds(rightX, top + 58, cw, 20)
                    .tooltip(Tooltip.create(Component.literal(L.t("Оплата по API через console.anthropic.com")))).dark().build());
            }
        }

        // ---- right: GitHub (gh device login → git push/pull + GitHub connector for Claude) ----
        JsonObject gh = github();
        int gy = top + 137;
        if (gh != null && "waiting".equals(str(gh.getAsJsonObject("flow"), "state"))) {
            String url = str(gh.getAsJsonObject("flow"), "url");
            Button open = addRenderableWidget(Ui.button(Component.literal(L.t("Открыть github.com/login/device")), b ->
                ConfirmLinkScreen.confirmLinkNow(this, URI.create(url != null ? url : "https://github.com/login/device")))
                .bounds(rightX, gy, cw - 64, 20).dark().build());
            open.active = true;
            addRenderableWidget(Ui.button(CommonComponents.GUI_CANCEL, b -> action("gh_cancel", ""))
                .bounds(rightX + cw - 60, gy, 60, 20).dark().build());
        } else if (gh != null && str(gh, "login") != null && !str(gh, "login").isEmpty()) {
            boolean on = gh.get("connector").getAsBoolean();
            addRenderableWidget(Ui.button(Component.literal("MCP: " + (on ? L.t("вкл") : L.t("выкл"))),
                    b -> action("gh_connector", on ? "off" : "on"))
                .bounds(rightX, gy, cw / 2 - 2, 20)
                .tooltip(Tooltip.create(Component.literal(L.t("Коннектор GitHub для Claude: issues, PR, Actions")))).dark().build());
            addRenderableWidget(Ui.button(Component.literal(L.t("Выйти")), b -> action("gh_logout", ""))
                .bounds(rightX + cw / 2 + 2, gy, cw / 2 - 2, 20).dark().build());
        } else if (gh != null && gh.get("installed").getAsBoolean()) {
            addRenderableWidget(Ui.button(Component.literal(L.t("Войти через GitHub")), b -> action("gh_login", ""))
                .bounds(rightX, gy, cw, 20)
                .tooltip(Tooltip.create(Component.literal(L.t("Вход GitHub CLI по коду: git push/pull без паролей + коннектор для Claude")))).dark().build());
        } else if (gh != null) {
            addRenderableWidget(Ui.button(Component.literal(L.t("Установить GitHub CLI")), b ->
                    ConfirmLinkScreen.confirmLinkNow(this, URI.create("https://cli.github.com")))
                .bounds(rightX, gy, cw / 2 - 2, 20).dark().build());
            addRenderableWidget(Ui.button(Component.literal(L.t("Токен…")), b -> mod.runLine("cc", "/claude key github"))
                .bounds(rightX + cw / 2 + 2, gy, cw / 2 - 2, 20)
                .tooltip(Tooltip.create(Component.literal(L.t("Personal access token для коннектора (без gh)")))).dark().build());
        }

        int extra = errorLines().size() * 9;
        addRenderableWidget(Ui.button(CommonComponents.GUI_DONE, b -> onClose())
            .bounds(width / 2 - 100, top + 184 + extra, 200, 20).dark().build());
        if (extra > 0)
            addRenderableWidget(Ui.button(Component.literal(L.t("Копировать ошибку")), b -> minecraft.keyboardHandler.setClipboard(mod.launcher.detail()))
                .bounds(width / 2 + 104, top + 184 + extra, 110, 20).dark().build());
        layoutKey = layoutKey();
    }

    private JsonObject github() {
        return status != null && status.has("github") ? status.getAsJsonObject("github") : null;
    }

    private Component autoLabel() {
        return Component.literal(L.t("Автозапуск") + ": " + (autoStart ? L.t("вкл") : L.t("выкл")));
    }

    /** The bridge failed: its error in full (Python missing, pip failed…), at most 4 lines. */
    private java.util.List<net.minecraft.util.FormattedCharSequence> errorLines() {
        if (preview || mod.launcher.state() != BridgeLauncher.State.FAILED || mod.launcher.detail().isEmpty()) return java.util.List.of();
        var lines = font.split(Component.literal(mod.launcher.detail()), Math.max(100, width - 40));
        return lines.subList(0, Math.min(4, lines.size()));
    }

    private String layoutKey() {
        JsonObject login = status == null ? null : status.getAsJsonObject("login");
        JsonObject gh = github();
        String ghKey = gh == null ? "-" : str(gh, "login") + "/" + gh.get("connector").getAsBoolean() + "/"
            + str(gh.getAsJsonObject("flow"), "state") + "/" + gh.get("installed").getAsBoolean();
        return loggedIn() + ":" + waitingForCode() + ":" + (str(login, "url") != null) + ":" + ghKey + ":" + errorLines().size();
    }

    private boolean githubWaiting() {
        JsonObject gh = github();
        return gh != null && "waiting".equals(str(gh.getAsJsonObject("flow"), "state"));
    }

    @Override public void tick() {
        if (preview) return;
        long now = System.currentTimeMillis();
        if (now < nextPoll || busy) return;
        nextPoll = now + (waitingForCode() || githubWaiting() ? 1000 : 4000);
        busy = true;
        CompletableFuture.supplyAsync(() -> {
            try { return mod.request("/account", null); }
            catch (Exception e) { return null; }
        }, mod.io).thenAccept(result -> minecraft.execute(() -> {
            busy = false;
            String before = loginState();
            status = result;
            String after = loginState();
            if (before.equals("checking") && after.equals("done")) say(L.t("Вход выполнен."), GREEN);
            if (after.equals("failed") && !before.equals("failed"))
                say(L.t("Вход не удался: ") + str(status.getAsJsonObject("login"), "error"), RED);
            JsonObject gh = github();
            if (gh != null && "failed".equals(str(gh.getAsJsonObject("flow"), "state")))
                say("GitHub: " + str(gh.getAsJsonObject("flow"), "error"), RED);
            if (!layoutKey().equals(layoutKey)) rebuildWidgets();
        }));
    }

    private void say(String text, int color) { message = text; messageColor = color; }

    private void action(String action, String text) {
        if (preview) return;
        say("…", GRAY);
        CompletableFuture.runAsync(() -> {
            try {
                mod.request("/command", mod.command("c", action, text));
                minecraft.execute(() -> { say(done(action), GREEN); nextPoll = 0; });
            } catch (Exception e) {
                minecraft.execute(() -> say(e.getMessage(), RED));
            }
        }, mod.io);
    }

    private static String done(String action) {
        return switch (action) {
            case "login" -> L.t("Открой страницу входа, войди и вставь код.");
            case "login_code" -> L.t("Код отправлен, проверяю…");
            case "logout" -> L.t("Выход выполнен.");
            case "apikey" -> L.t("API-ключ обновлён.");
            default -> L.t("Готово.");
        };
    }

    private void restartBridge() {
        if (preview) return;
        mod.launcher.saveSettings(autoStart, python.getValue());
        say(L.t("Перезапуск моста…"), GRAY);
        Thread t = new Thread(mod.launcher::restart, "claude-bridge-restart");
        t.setDaemon(true);
        t.start();
    }

    @Override public void onClose() {
        if (!preview) mod.launcher.saveSettings(autoStart, python == null ? "" : python.getValue());
        minecraft.setScreenAndShow(parent);
    }

    private String fit(String text, int width) { return font.plainSubstrByWidth(text, width); }

    @Override public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float delta) {
        int cw = columnWidth;
        // Panels behind the two columns.
        Ui.panel(g, leftX - 6, top - 6, cw + 12, 168, PANEL, Theme.darkUi().line());
        Ui.panel(g, rightX - 6, top - 6, cw + 12, 168, PANEL, Theme.darkUi().line());
        g.fill(leftX - 6, top - 6, leftX + cw + 6, top - 5, 0xFFF2C14E);
        g.fill(rightX - 6, top - 6, rightX + cw + 6, top - 5, 0xFFF2C14E);
        super.extractRenderState(g, mouseX, mouseY, delta);
        g.centeredText(font, Component.literal("✦ Claudecraft").withColor(0xD97757), width / 2, top - 20, WHITE);

        // left: bridge
        g.text(font, L.t("Мост"), leftX, top, GOLD);
        BridgeLauncher.State state = preview ? BridgeLauncher.State.RUNNING : mod.launcher.state();
        int color = switch (state) {
            case RUNNING, EXTERNAL -> GREEN;
            case FAILED -> RED;
            case INSTALLING, STARTING -> GOLD;
            default -> GRAY;
        };
        g.text(font, "● " + state.text(), leftX, top + 12, color);
        String detail = preview ? "" : mod.launcher.detail();
        if (!detail.isEmpty()) g.text(font, fit(detail, cw), leftX, top + 22, DARK);
        int ey = top + 166;
        for (var errorLine : errorLines()) { g.text(font, errorLine, 20, ey, RED); ey += 9; }

        // left: API key
        g.text(font, L.t("API-ключ для /c new api"), leftX, top + 112, GOLD);
        String key = status == null ? null : str(status, "api_key");
        g.text(font, key == null ? L.t("не задан") : key, leftX, top + 124, key == null ? DARK : GRAY);

        // right: account
        g.text(font, L.t("Аккаунт Claude"), rightX, top, GOLD);
        JsonObject account = account();
        if (status == null) {
            g.text(font, L.t("● мост недоступен"), rightX, top + 12, DARK);
        } else if (loggedIn()) {
            String email = str(account, "email");
            g.text(font, "● " + fit(email != null ? email : L.t("вход выполнен"), cw - 10), rightX, top + 12, GREEN);
            String method = str(account, "method");
            if (method != null) g.text(font, method, rightX, top + 22, DARK);
        } else {
            g.text(font, L.t("● вход не выполнен"), rightX, top + 12, GOLD);
        }
        if (!waitingForCode() && !loggedIn()) {  // while signing in, the numbered buttons explain the steps
            g.text(font, fit(L.t("Вход сохраняется как у Claude Code"), cw), rightX, top + 84, DARK);
            g.text(font, fit(L.t("в терминале (claude auth login)."), cw), rightX, top + 94, DARK);
        }

        // right: GitHub
        g.text(font, "GitHub", rightX, top + 114, GOLD);
        JsonObject gh = github();
        String ghLine;
        int ghColor;
        if (gh == null) { ghLine = "—"; ghColor = DARK; }
        else if (githubWaiting()) {
            ghLine = L.t("Код: ") + str(gh.getAsJsonObject("flow"), "code") + L.t("  (уже в буфере, вставь на странице)");
            ghColor = GOLD;
        } else if (str(gh, "login") != null && !str(gh, "login").isEmpty()) {
            ghLine = "● " + str(gh, "login") + (gh.get("connector").getAsBoolean() ? "  · git + MCP" : "  · git");
            ghColor = GREEN;
        } else if (gh.get("installed").getAsBoolean()) { ghLine = L.t("● не подключён"); ghColor = GOLD; }
        else { ghLine = L.t("● нужен GitHub CLI (gh) или токен"); ghColor = DARK; }
        g.text(font, fit(ghLine, cw), rightX, top + 125, ghColor);

        if (!message.isEmpty()) g.centeredText(font, fit(message, width - 20), width / 2, top + 170 + errorLines().size() * 9, messageColor);
    }
}
