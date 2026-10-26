package dev.claudechat;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.screens.ConfirmLinkScreen;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.CommonComponents;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.Style;
import net.minecraft.util.FormattedCharSequence;
import java.net.URI;
import java.util.concurrent.CompletableFuture;

/** /c settings: bridge process, Claude account login, API key. Talks to the bridge over HTTP. */
final class SettingsScreen extends Screen {
    private static final int WHITE = 0xFFFFFFFF, GRAY = 0xFFA0A0A0, GREEN = 0xFF55FF55, RED = 0xFFFF5555, GOLD = 0xFFFFAA00;
    private final ClaudeChat mod;
    private final Screen parent;
    private JsonObject status;
    private String message = "";
    private int messageColor = GRAY;
    private long nextPoll;
    private volatile boolean busy;
    private boolean autoStart;
    private String codeValue = "", keyValue = "", pythonValue;
    private EditBox code, apiKey, python;
    private String layoutKey = "";
    private int left, top;

    SettingsScreen(ClaudeChat mod, Screen parent) {
        super(Component.literal("Claudecraft — настройки"));
        this.mod = mod;
        this.parent = parent;
        this.autoStart = mod.launcher.autoStart();
        this.pythonValue = mod.launcher.pythonSetting();
    }

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
    private static String str(JsonObject o, String key) {
        JsonElement e = o == null ? null : o.get(key);
        return e == null || e.isJsonNull() ? null : e.getAsString();
    }

    @Override protected void init() {
        // Keep typed text across rebuilds (the layout changes with login state).
        if (code != null) codeValue = code.getValue();
        if (apiKey != null) keyValue = apiKey.getValue();
        if (python != null) pythonValue = python.getValue();
        left = width / 2 - 160;
        top = Math.max(8, height / 2 - 120);
        int y = top + 22;

        // Bridge
        addRenderableWidget(Button.builder(Component.literal("Перезапустить мост"), b -> restartBridge())
            .bounds(left + 160, y, 158, 20).build());
        y += 44;

        // Account
        String login = loginState();
        boolean waiting = login.equals("waiting_code") || login.equals("checking") || login.equals("starting");
        if (loggedIn() && !waiting) {
            addRenderableWidget(Button.builder(Component.literal("Выйти из аккаунта"), b -> action("logout", ""))
                .bounds(left + 160, y, 158, 20).build());
        } else if (!waiting) {
            addRenderableWidget(Button.builder(Component.literal("Войти: подписка Claude"), b -> action("login", ""))
                .bounds(left, y, 158, 20).build());
            addRenderableWidget(Button.builder(Component.literal("Войти: Console (API)"), b -> action("login", "console"))
                .bounds(left + 162, y, 158, 20).build());
        }
        if (waiting) {
            String url = str(status.getAsJsonObject("login"), "url");
            Button open = addRenderableWidget(Button.builder(Component.literal("1. Открыть страницу входа"), b -> {
                if (url != null) ConfirmLinkScreen.confirmLinkNow(this, URI.create(url));
            }).bounds(left, y, 158, 20).build());
            open.active = url != null;
            addRenderableWidget(Button.builder(CommonComponents.GUI_CANCEL, b -> action("login_cancel", ""))
                .bounds(left + 162, y, 158, 20).build());
            y += 24;
            code = new EditBox(font, left, y, 236, 20, Component.literal("Код"));
            code.setMaxLength(2000);
            code.setHint(Component.literal("2. Вставь код со страницы (Ctrl+V)"));
            code.setValue(codeValue);
            addRenderableWidget(code);
            addRenderableWidget(Button.builder(Component.literal("3. Отправить"), b -> {
                action("login_code", code.getValue().strip());
                codeValue = "";
                code.setValue("");
            }).bounds(left + 240, y, 80, 20).build());
        } else {
            code = null;
        }
        y += 44;

        // API key (only for /c new api chats)
        apiKey = new EditBox(font, left, y, 156, 20, Component.literal("API-ключ"));
        apiKey.setMaxLength(300);
        apiKey.setHint(Component.literal("sk-ant-…"));
        apiKey.addFormatter((text, offset) -> FormattedCharSequence.forward("•".repeat(text.length()), Style.EMPTY));
        apiKey.setValue(keyValue);
        addRenderableWidget(apiKey);
        addRenderableWidget(Button.builder(Component.literal("Сохранить"), b -> {
            String key = apiKey.getValue().strip();
            if (!key.isEmpty()) { action("apikey", key); keyValue = ""; apiKey.setValue(""); }
        }).bounds(left + 160, y, 78, 20).build());
        addRenderableWidget(Button.builder(Component.literal("Удалить"), b -> action("apikey", ""))
            .bounds(left + 242, y, 78, 20).build());
        y += 34;

        // Launcher settings
        addRenderableWidget(Button.builder(Component.literal("Автозапуск моста: " + (autoStart ? "вкл" : "выкл")), b -> {
            autoStart = !autoStart;
            b.setMessage(Component.literal("Автозапуск моста: " + (autoStart ? "вкл" : "выкл")));
        }).bounds(left, y, 158, 20).build());
        python = new EditBox(font, left + 162, y, 158, 20, Component.literal("Python"));
        python.setMaxLength(500);
        python.setHint(Component.literal("python.exe (пусто = авто)"));
        python.setValue(pythonValue == null ? "" : pythonValue);
        addRenderableWidget(python);
        y += 30;

        addRenderableWidget(Button.builder(CommonComponents.GUI_DONE, b -> onClose())
            .bounds(width / 2 - 100, y, 200, 20).build());
        layoutKey = layoutKey();
    }

    private String layoutKey() { return loggedIn() + ":" + loginState() + ":" + (status != null && status.getAsJsonObject("login") != null
        && str(status.getAsJsonObject("login"), "url") != null); }

    @Override public void tick() {
        long now = System.currentTimeMillis();
        if (now < nextPoll || busy) return;
        nextPoll = now + (loginState().equals("idle") || loginState().equals("done") ? 4000 : 1000);
        busy = true;
        CompletableFuture.supplyAsync(() -> {
            try { return mod.request("/account", null); }
            catch (Exception e) { return null; }
        }, mod.io).thenAccept(result -> minecraft.execute(() -> {
            busy = false;
            String before = loginState();
            status = result;
            String after = loginState();
            if (before.equals("checking") && after.equals("done")) say("Вход выполнен.", GREEN);
            if (after.equals("failed") && !before.equals("failed"))
                say("Вход не удался: " + str(status.getAsJsonObject("login"), "error"), RED);
            if (!layoutKey().equals(layoutKey)) rebuildWidgets();
        }));
    }

    private void say(String text, int color) { message = text; messageColor = color; }

    private void action(String action, String text) {
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
            case "login" -> "Открой страницу входа, войди и вставь код.";
            case "login_code" -> "Код отправлен, проверяю…";
            case "logout" -> "Выход выполнен.";
            case "apikey" -> "API-ключ обновлён.";
            default -> "Готово.";
        };
    }

    private void restartBridge() {
        mod.launcher.saveSettings(autoStart, python.getValue());
        say("Перезапуск моста…", GRAY);
        Thread t = new Thread(mod.launcher::restart, "claude-bridge-restart");
        t.setDaemon(true);
        t.start();
    }

    @Override public void onClose() {
        mod.launcher.saveSettings(autoStart, python == null ? "" : python.getValue());
        minecraft.setScreenAndShow(parent);
    }

    @Override public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float delta) {
        super.extractRenderState(g, mouseX, mouseY, delta);
        g.centeredText(font, title, width / 2, top, WHITE);
        int y = top + 22;
        BridgeLauncher.State state = mod.launcher.state();
        int bridgeColor = switch (state) {
            case RUNNING, EXTERNAL -> GREEN;
            case FAILED -> RED;
            case INSTALLING, STARTING -> GOLD;
            default -> GRAY;
        };
        g.text(font, "Мост: " + state.label, left, y + 6, bridgeColor);
        String detail = mod.launcher.detail();
        if (!detail.isEmpty()) g.text(font, font.plainSubstrByWidth(detail, 320), left, y + 22, GRAY);
        y += 44;

        JsonObject account = account();
        String accountText;
        int accountColor;
        if (status == null) { accountText = "Аккаунт Claude: мост недоступен"; accountColor = GRAY; }
        else if (loggedIn()) {
            String email = str(account, "email"), method = str(account, "method");
            accountText = "Аккаунт Claude: " + (email != null ? email : "вход выполнен")
                + (method != null ? " (" + method + ")" : "");
            accountColor = GREEN;
        } else { accountText = "Аккаунт Claude: вход не выполнен"; accountColor = GOLD; }
        g.text(font, accountText, left, y - 10, accountColor);
        boolean waiting = code != null;
        y += waiting ? 68 : 44;

        String key = status == null ? null : str(status, "api_key");
        g.text(font, "API-ключ для /c new api: " + (key == null ? "нет" : key), left, y - 12, GRAY);
        y += 34;
        y += 30;
        if (!message.isEmpty()) g.centeredText(font, font.plainSubstrByWidth(message, 360), width / 2, y + 26, messageColor);
    }
}
