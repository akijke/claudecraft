package dev.geminicraft;

import com.google.gson.JsonObject;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.screens.ConfirmLinkScreen;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.Component;
import java.net.URI;
import java.util.concurrent.CompletableFuture;

/** Gemini account UI: Google sign-in (Gemini CLI opens the browser) or an API key; CLI install. */
final class SettingsScreen extends Screen {
    private final ClaudeChat mod;
    private final Screen parent;
    private JsonObject status;
    private EditBox python, apiKey;
    private String message = "";
    private long nextPoll;
    private boolean fetching;
    private boolean autoStart;
    private boolean preview;
    private Boolean cliInstalled;  // null until the bridge answers

    SettingsScreen(ClaudeChat mod, Screen parent) {
        super(Component.literal("Geminicraft"));
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
        if (Boolean.FALSE.equals(cliInstalled)) {
            // No Gemini CLI yet: one click runs npm install -g @google/gemini-cli on the bridge.
            addRenderableWidget(Button.builder(Component.literal(t("Установить Gemini CLI", "Install Gemini CLI")),
                b -> action("install_cli")).bounds(x, y + 32, 300, 20)
                .tooltip(net.minecraft.client.gui.components.Tooltip.create(Component.literal(
                    "npm install -g @google/gemini-cli" + t(" (нужен Node.js)", " (needs Node.js)")))).build());
        } else {
            addRenderableWidget(Button.builder(Component.literal(t("Войти через Google", "Sign in with Google")),
                b -> action("login")).bounds(x, y + 32, 148, 20).build());
            addRenderableWidget(Button.builder(Component.literal(t("Отменить вход", "Cancel sign-in")),
                b -> action("login_cancel")).bounds(x + 152, y + 32, 148, 20).build());
        }
        apiKey = new EditBox(font, x, y + 56, 222, 20, Component.literal("API key"));
        apiKey.setMaxLength(200);
        apiKey.setHint(Component.literal(t("API-ключ (aistudio.google.com/apikey)", "API key (aistudio.google.com/apikey)")));
        addRenderableWidget(apiKey);
        addRenderableWidget(Button.builder(Component.literal(t("Сохранить", "Save")), b -> {
            mod.send("c", mod.command("c", "apikey", apiKey.getValue().strip()));
            apiKey.setValue("");
            nextPoll = 0;
        }).bounds(x + 226, y + 56, 74, 20).build());
        addRenderableWidget(Button.builder(Component.literal(t("Перезапустить мост", "Restart bridge")), b -> {
            Thread worker = new Thread(mod.launcher::restart, "geminicraft-restart");
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
        }, mod.io).thenAccept(value -> minecraft.execute(() -> {
            status = value;
            fetching = false;
            Boolean installed = value != null && value.has("cli") ? value.getAsJsonObject("cli").get("installed").getAsBoolean() : null;
            if (installed != null && !installed.equals(cliInstalled)) { cliInstalled = installed; rebuildWidgets(); }
        }));
    }

    @Override public void onClose() {
        mod.launcher.saveSettings(autoStart, python == null ? "" : python.getValue());
        minecraft.setScreenAndShow(parent);
    }

    @Override public boolean isPauseScreen() { return false; }

    @Override public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float delta) {
        int x = width / 2 - 150;
        int y = Math.max(28, height / 2 - 105);
        g.fill(0, 0, width, height, GeminiTheme.BACKDROP);
        GeminiTheme.panel(g, x - 10, y - 24, 320, 234, GeminiTheme.GPT);
        super.extractRenderState(g, mouseX, mouseY, delta);
        g.centeredText(font, "Geminicraft · /gem · /gemini", width / 2, y - 18, GeminiTheme.opaque(GeminiTheme.GPT));
        String accountText = t("Мост недоступен", "Bridge unavailable");
        if (status != null) {
            JsonObject account = status.getAsJsonObject("account");
            accountText = account != null && account.has("logged_in") && account.get("logged_in").getAsBoolean()
                ? t("Вход выполнен: ", "Signed in: ") + string(account, "method") + " " + string(account, "email")
                : t("Нужен вход в Gemini (Google или API-ключ)", "Sign in to Gemini (Google or an API key)");
            String error = string(status, "error");
            if (!error.isEmpty()) accountText = error;
            JsonObject login = status.getAsJsonObject("login");
            message = string(login, "code");
            if (!message.isEmpty()) message = t("Код на странице входа: ", "Code for sign-in page: ") + message;
            if (!string(login, "error").isEmpty()) message = string(login, "error");
            JsonObject install = status.has("cli") ? status.getAsJsonObject("cli").getAsJsonObject("install") : null;
            String state = string(install, "state");
            if (state.equals("running")) {
                var log = install.getAsJsonArray("log");
                message = t("Устанавливаю Gemini CLI… ", "Installing Gemini CLI… ") + (log.isEmpty() ? "" : log.get(log.size() - 1).getAsString());
            } else if (state.equals("failed")) message = string(install, "error");
            else if (state.equals("done")) message = t("Gemini CLI установлен — теперь войди через Google", "Gemini CLI installed — now sign in with Google");
            if (Boolean.FALSE.equals(cliInstalled) && !state.equals("running") && !state.equals("failed"))
                accountText = t("Gemini CLI не установлен", "Gemini CLI is not installed");
        }
        g.text(font, font.plainSubstrByWidth(accountText, 300), x, y, GeminiTheme.opaque(GeminiTheme.TEXT));
        g.text(font, font.plainSubstrByWidth(message, 300), x, y + 82, GeminiTheme.opaque(GeminiTheme.WARNING));
        g.text(font, t("Python 3.11+ (пусто — найти автоматически)", "Python 3.11+ (empty = auto-detect)"), x, y + 130, GeminiTheme.opaque(GeminiTheme.DIM));
        g.text(font, font.plainSubstrByWidth(mod.launcher.state().text() + " " + mod.launcher.detail(), 300),
            x, y + 192, GeminiTheme.opaque(GeminiTheme.DIM));
    }
}
