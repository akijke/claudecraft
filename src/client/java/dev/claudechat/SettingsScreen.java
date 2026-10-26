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
        addRenderableWidget(Button.builder(Component.literal("⟳ Перезапустить мост"), b -> restartBridge())
            .bounds(leftX, top + 34, cw, 20).build());
        addRenderableWidget(Button.builder(autoLabel(), b -> { autoStart = !autoStart; b.setMessage(autoLabel()); })
            .bounds(leftX, top + 58, cw, 20)
            .tooltip(Tooltip.create(Component.literal("Запускать мост вместе с игрой"))).build());
        python = new EditBox(font, leftX, top + 82, cw, 18, Component.literal("Python"));
        python.setMaxLength(500);
        python.setHint(Component.literal("путь к python.exe (пусто = авто)"));
        python.setValue(pythonValue == null ? "" : pythonValue);
        addRenderableWidget(python);

        // ---- left: API key ----
        apiKey = new EditBox(font, leftX, top + 136, cw - 66, 18, Component.literal("API-ключ"));
        apiKey.setMaxLength(300);
        apiKey.setHint(Component.literal("sk-ant-…"));
        apiKey.addFormatter((text, offset) -> FormattedCharSequence.forward("•".repeat(text.length()), Style.EMPTY));
        apiKey.setValue(keyValue);
        addRenderableWidget(apiKey);
        addRenderableWidget(Button.builder(Component.literal("✔"), b -> {
            String key = apiKey.getValue().strip();
            if (!key.isEmpty()) { action("apikey", key); keyValue = ""; apiKey.setValue(""); }
        }).bounds(leftX + cw - 62, top + 135, 30, 20).tooltip(Tooltip.create(Component.literal("Сохранить ключ"))).build());
        addRenderableWidget(Button.builder(Component.literal("✖"), b -> action("apikey", ""))
            .bounds(leftX + cw - 30, top + 135, 30, 20).tooltip(Tooltip.create(Component.literal("Удалить ключ"))).build());

        // ---- right: account ----
        if (waitingForCode()) {
            String url = str(status.getAsJsonObject("login"), "url");
            Button open = addRenderableWidget(Button.builder(Component.literal("1. Открыть страницу входа"), b -> {
                if (url != null) ConfirmLinkScreen.confirmLinkNow(this, URI.create(url));
            }).bounds(rightX, top + 34, cw, 20).build());
            open.active = url != null;
            code = new EditBox(font, rightX, top + 59, cw, 18, Component.literal("Код"));
            code.setMaxLength(2000);
            code.setHint(Component.literal("2. вставь код (Ctrl+V)"));
            code.setValue(codeValue);
            addRenderableWidget(code);
            addRenderableWidget(Button.builder(Component.literal("3. Отправить код"), b -> {
                action("login_code", code.getValue().strip());
                codeValue = "";
                code.setValue("");
            }).bounds(rightX, top + 82, cw - 64, 20).build());
            addRenderableWidget(Button.builder(CommonComponents.GUI_CANCEL, b -> action("login_cancel", ""))
                .bounds(rightX + cw - 60, top + 82, 60, 20).build());
        } else {
            code = null;
            if (loggedIn()) {
                addRenderableWidget(Button.builder(Component.literal("Выйти из аккаунта"), b -> action("logout", ""))
                    .bounds(rightX, top + 34, cw, 20).build());
            } else {
                addRenderableWidget(Button.builder(Component.literal("Войти: подписка Claude"), b -> action("login", ""))
                    .bounds(rightX, top + 34, cw, 20)
                    .tooltip(Tooltip.create(Component.literal("Pro / Max — как claude auth login"))).build());
                addRenderableWidget(Button.builder(Component.literal("Войти: Console (API)"), b -> action("login", "console"))
                    .bounds(rightX, top + 58, cw, 20)
                    .tooltip(Tooltip.create(Component.literal("Оплата по API через console.anthropic.com"))).build());
            }
        }

        addRenderableWidget(Button.builder(CommonComponents.GUI_DONE, b -> onClose())
            .bounds(width / 2 - 100, top + 184, 200, 20).build());
        layoutKey = layoutKey();
    }

    private Component autoLabel() {
        return Component.literal("Автозапуск: " + (autoStart ? "вкл" : "выкл"));
    }

    private String layoutKey() {
        JsonObject login = status == null ? null : status.getAsJsonObject("login");
        return loggedIn() + ":" + waitingForCode() + ":" + (str(login, "url") != null);
    }

    @Override public void tick() {
        if (preview) return;
        long now = System.currentTimeMillis();
        if (now < nextPoll || busy) return;
        nextPoll = now + (waitingForCode() ? 1000 : 4000);
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
            case "login" -> "Открой страницу входа, войди и вставь код.";
            case "login_code" -> "Код отправлен, проверяю…";
            case "logout" -> "Выход выполнен.";
            case "apikey" -> "API-ключ обновлён.";
            default -> "Готово.";
        };
    }

    private void restartBridge() {
        if (preview) return;
        mod.launcher.saveSettings(autoStart, python.getValue());
        say("Перезапуск моста…", GRAY);
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
        g.fill(leftX - 6, top - 6, leftX + cw + 6, top + 162, PANEL);
        g.fill(rightX - 6, top - 6, rightX + cw + 6, top + 162, PANEL);
        super.extractRenderState(g, mouseX, mouseY, delta);
        g.centeredText(font, Component.literal("✦ Claudecraft").withColor(0xD97757), width / 2, top - 20, WHITE);

        // left: bridge
        g.text(font, "Мост", leftX, top, GOLD);
        BridgeLauncher.State state = preview ? BridgeLauncher.State.RUNNING : mod.launcher.state();
        int color = switch (state) {
            case RUNNING, EXTERNAL -> GREEN;
            case FAILED -> RED;
            case INSTALLING, STARTING -> GOLD;
            default -> GRAY;
        };
        g.text(font, "● " + state.label, leftX, top + 12, color);
        String detail = preview ? "" : mod.launcher.detail();
        if (!detail.isEmpty()) g.text(font, fit(detail, cw), leftX, top + 22, DARK);

        // left: API key
        g.text(font, "API-ключ для /c new api", leftX, top + 112, GOLD);
        String key = status == null ? null : str(status, "api_key");
        g.text(font, key == null ? "не задан" : key, leftX, top + 124, key == null ? DARK : GRAY);

        // right: account
        g.text(font, "Аккаунт Claude", rightX, top, GOLD);
        JsonObject account = account();
        if (status == null) {
            g.text(font, "● мост недоступен", rightX, top + 12, DARK);
        } else if (loggedIn()) {
            String email = str(account, "email");
            g.text(font, "● " + fit(email != null ? email : "вход выполнен", cw - 10), rightX, top + 12, GREEN);
            String method = str(account, "method");
            if (method != null) g.text(font, method, rightX, top + 22, DARK);
        } else {
            g.text(font, "● вход не выполнен", rightX, top + 12, GOLD);
        }
        if (waitingForCode()) {
            g.text(font, fit("Войди на странице, скопируй код", cw), rightX, top + 108, DARK);
            g.text(font, fit("и вставь его в поле выше.", cw), rightX, top + 118, DARK);
        } else if (!loggedIn()) {
            g.text(font, fit("Вход сохраняется как у Claude Code", cw), rightX, top + 84, DARK);
            g.text(font, fit("в терминале (claude auth login).", cw), rightX, top + 94, DARK);
        }

        if (!message.isEmpty()) g.centeredText(font, fit(message, width - 20), width / 2, top + 170, messageColor);
    }
}
