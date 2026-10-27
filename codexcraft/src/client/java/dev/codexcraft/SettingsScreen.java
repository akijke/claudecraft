package dev.codexcraft;

import com.google.gson.JsonObject;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.screens.ConfirmLinkScreen;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.Component;
import java.net.URI;
import java.util.concurrent.CompletableFuture;

/** Codex account UI. Authentication belongs to the installed Codex CLI. */
final class SettingsScreen extends Screen {
    private final ClaudeChat mod;
    private final Screen parent;
    private JsonObject status;
    private EditBox python;
    private String message = "";
    private long nextPoll;
    private boolean fetching;
    private boolean autoStart;
    private boolean preview;

    SettingsScreen(ClaudeChat mod, Screen parent) {
        super(Component.literal("Codexcraft"));
        this.mod = mod;
        this.parent = parent;
        autoStart = mod.launcher.autoStart();
    }

    static SettingsScreen preview(ClaudeChat mod, JsonObject status) {
        SettingsScreen screen = new SettingsScreen(mod, null);
        screen.status = status;
        screen.preview = true;
        return screen;
    }

    private String t(String ru, String en) { return L.russian() ? ru : en; }

    @Override protected void init() {
        int x = width / 2 - 150;
        int y = Math.max(28, height / 2 - 105);
        addRenderableWidget(Button.builder(Component.literal(t("Войти через ChatGPT", "Sign in with ChatGPT")),
            b -> action("login")).bounds(x, y + 32, 148, 20).build());
        addRenderableWidget(Button.builder(Component.literal(t("Отменить вход", "Cancel sign-in")),
            b -> action("login_cancel")).bounds(x + 152, y + 32, 148, 20).build());
        addRenderableWidget(Button.builder(Component.literal(t("Открыть страницу входа", "Open sign-in page")), b -> {
            JsonObject login = status != null && status.has("login") ? status.getAsJsonObject("login") : null;
            String url = string(login, "url");
            if (!url.isEmpty()) ConfirmLinkScreen.confirmLinkNow(this, URI.create(url));
        }).bounds(x, y + 56, 148, 20).build());
        addRenderableWidget(Button.builder(Component.literal(t("Копировать код", "Copy code")), b -> {
            JsonObject login = status != null && status.has("login") ? status.getAsJsonObject("login") : null;
            minecraft.keyboardHandler.setClipboard(string(login, "code"));
        }).bounds(x + 152, y + 56, 148, 20).build());
        addRenderableWidget(Button.builder(Component.literal(t("Перезапустить мост", "Restart bridge")), b -> {
            Thread worker = new Thread(mod.launcher::restart, "codexcraft-restart");
            worker.setDaemon(true); worker.start();
        }).bounds(x, y + 100, 148, 20).build());
        addRenderableWidget(Button.builder(Component.literal("Language / Язык"), b -> {
            mod.launcher.setLanguage(L.russian() ? "en" : "ru"); rebuildWidgets();
        }).bounds(x + 152, y + 100, 148, 20).build());
        python = new EditBox(font, x, y + 142, 300, 20, Component.literal("Python path"));
        python.setMaxLength(1024);
        python.setValue(mod.launcher.pythonSetting());
        addRenderableWidget(python);
        addRenderableWidget(Button.builder(Component.literal(t("Автозапуск: ", "Auto-start: ") + autoStart), b -> {
            autoStart = !autoStart;
            b.setMessage(Component.literal(t("Автозапуск: ", "Auto-start: ") + autoStart));
        }).bounds(x, y + 166, 148, 20).build());
        addRenderableWidget(Button.builder(Component.literal(t("Готово", "Done")), b -> onClose())
            .bounds(x + 152, y + 166, 148, 20).build());
    }

    private static String string(JsonObject object, String key) {
        return object != null && object.has(key) && !object.get(key).isJsonNull() ? object.get(key).getAsString() : "";
    }

    private void action(String action) {
        mod.send("c", mod.command("c", action, ""));
        nextPoll = 0;
    }

    @Override public void tick() {
        if (preview || fetching || System.currentTimeMillis() < nextPoll) return;
        fetching = true;
        nextPoll = System.currentTimeMillis() + 2000;
        CompletableFuture.supplyAsync(() -> {
            try { return mod.request("/account", null); }
            catch (Exception e) { return null; }
        }, mod.io).thenAccept(value -> minecraft.execute(() -> { status = value; fetching = false; }));
    }

    @Override public void onClose() {
        mod.launcher.saveSettings(autoStart, python == null ? "" : python.getValue());
        minecraft.setScreenAndShow(parent);
    }

    @Override public boolean isPauseScreen() { return false; }

    @Override public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float delta) {
        int x = width / 2 - 150;
        int y = Math.max(28, height / 2 - 105);
        g.fill(0, 0, width, height, CodexTheme.BACKDROP);
        CodexTheme.panel(g, x - 10, y - 24, 320, 234, CodexTheme.GPT);
        super.extractRenderState(g, mouseX, mouseY, delta);
        g.centeredText(font, "Codexcraft · /gpt · /codex", width / 2, y - 18, CodexTheme.opaque(CodexTheme.GPT));
        String accountText = t("Мост недоступен", "Bridge unavailable");
        if (status != null) {
            JsonObject account = status.getAsJsonObject("account");
            accountText = account != null && account.has("logged_in") && account.get("logged_in").getAsBoolean()
                ? t("Вход выполнен: ", "Signed in: ") + string(account, "method") + " " + string(account, "email")
                : t("Нужен вход в Codex", "Sign in to Codex");
            String error = string(status, "error");
            if (!error.isEmpty()) accountText = error;
            JsonObject login = status.getAsJsonObject("login");
            message = string(login, "code");
            if (!message.isEmpty()) message = t("Код на странице входа: ", "Code for sign-in page: ") + message;
            if (!string(login, "error").isEmpty()) message = string(login, "error");
        }
        g.text(font, font.plainSubstrByWidth(accountText, 300), x, y, CodexTheme.opaque(CodexTheme.TEXT));
        g.text(font, font.plainSubstrByWidth(message, 300), x, y + 82, CodexTheme.opaque(CodexTheme.WARNING));
        g.text(font, t("Python 3.11+ (пусто — найти автоматически)", "Python 3.11+ (empty = auto-detect)"), x, y + 130, CodexTheme.opaque(CodexTheme.DIM));
        g.text(font, font.plainSubstrByWidth(mod.launcher.state().text() + " " + mod.launcher.detail(), 300),
            x, y + 192, CodexTheme.opaque(CodexTheme.DIM));
    }
}
