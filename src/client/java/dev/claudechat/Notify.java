package dev.claudechat;

import com.google.gson.JsonObject;
import net.fabricmc.fabric.api.client.rendering.v1.hud.HudElementRegistry;
import net.minecraft.client.DeltaTracker;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.toasts.SystemToast;
import net.minecraft.client.resources.sounds.SimpleSoundInstance;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.Identifier;
import net.minecraft.sounds.SoundEvents;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * While playing: a small HUD panel with running tasks (chat, current step, time; gold when
 * Claude waits for you), plus a toast and a sound when a task is done, needs an answer or
 * fails. Each part can be switched off: /claude hud|toasts|sound on|off.
 */
final class Notify {
    private static final SystemToast.SystemToastId TOAST = new SystemToast.SystemToastId(6000L);
    private static final Map<String, Task> TASKS = new LinkedHashMap<>();

    private static final class Task {
        String mode, title, step = "";
        long started = System.currentTimeMillis();
        boolean waiting;
    }

    private Notify() { }

    static void register() {
        HudElementRegistry.addLast(Identifier.fromNamespaceAndPath("claude_chat", "tasks"), Notify::render);
    }

    private static boolean on(String key) {
        ClaudeChat mod = ClaudeChat.instance();
        return mod == null || mod.launcher.flag(key, true);
    }

    /** Called for every bridge event (main thread). */
    static void onEvent(JsonObject e) {
        String kind = str(e, "kind"), chat = str(e, "chat"), mode = str(e, "mode");
        String title = str(e, "title");
        switch (kind) {
            case "thinking" -> {
                Task t = TASKS.computeIfAbsent(chat, k -> new Task());
                t.mode = mode; t.title = title; t.step = L.t("думает…"); t.started = System.currentTimeMillis(); t.waiting = false;
            }
            case "tool" -> { Task t = TASKS.get(chat); if (t != null) { t.step = str(e, "text"); t.waiting = false; } }
            case "text_part", "text" -> { Task t = TASKS.get(chat); if (t != null) t.waiting = false; }
            case "permission", "question", "plan", "budget_hit", "watch_failed" -> {
                Task t = TASKS.get(chat);
                if (t != null) t.waiting = true;
                if (!viewing(chat)) alert(L.t("Claude ждёт твоего ответа"), title, "ask");
                else if (on("sound")) Sounds.play("ask");  // the card is on screen, a sound still helps
            }
            case "done" -> {
                Task t = TASKS.remove(chat);
                long seconds = t == null ? 0 : (System.currentTimeMillis() - t.started) / 1000;
                if (!viewing(chat) && seconds >= 5) alert("✔ " + L.t("Claude закончил"), title, "done");
            }
            case "error" -> {
                TASKS.remove(chat);
                if (!viewing(chat)) alert("✖ " + L.t("Ошибка"), title, "error");
            }
            case "status" -> {
                String text = str(e, "text");
                if (text.startsWith("Остановлено") || text.startsWith("Stopped") || text.startsWith("Текущий запрос прерван")
                        || text.startsWith("Current request stopped")) TASKS.remove(chat);
            }
            default -> { }
        }
    }

    /** No need to ping you about a chat you are looking at in the conversation window. */
    private static boolean viewing(String chat) {
        return ConversationScreen.isOpen() && chat.equals(ConversationScreen.shownChat());
    }

    private static void alert(String head, String title, String sound) {
        Minecraft client = Minecraft.getInstance();
        ClaudeChat mod = ClaudeChat.instance();
        if (Theme.ours(client.gui.screen()) && mod != null) mod.notice = head + (title == null || title.isEmpty() ? "" : " — " + title);
        else if (on("toasts"))
            SystemToast.addOrUpdate(client.gui.toastManager(), TOAST, Component.literal(head),
                Component.literal(title == null ? "" : title));
        if (on("sound")) Sounds.play(sound);
        OsNotify.show(head, title);  // only when the game is in the background or minimized
    }

    /** Dev screenshots: draw the panel outside a world. */
    static void preview(GuiGraphicsExtractor g) { render(g, null); }

    private static void render(GuiGraphicsExtractor g, DeltaTracker delta) {
        if (TASKS.isEmpty() || !on("hud")) return;
        Minecraft client = Minecraft.getInstance();
        var font = client.font;
        int width = client.getWindow().getGuiScaledWidth();
        // Codexcraft and Geminicraft generate copies of this class (package renamed) and run in the
        // same game: each gets its own row, so the three panels never cover each other.
        String pkg = Notify.class.getPackageName();
        boolean main = pkg.equals("dev.claude" + "chat");
        // Top-left: toasts and status effects already use the top-right corner.
        int w = 150, x = 6, y = main ? 4 : pkg.endsWith("gemini" + "craft") ? 168 : 86, shown = 0;
        for (Task t : TASKS.values()) {
            if (shown++ == 3) break;
            long s = (System.currentTimeMillis() - t.started) / 1000;
            String time = s >= 60 ? (s / 60) + "m " + (s % 60) + "s" : s + "s";
            int accent = "cc".equals(t.mode) ? 0xFFB48EF0 : 0xFFD97757;
            g.fill(x - 3, y - 2, x + w + 1, y + 21, t.waiting ? 0xB0503A10 : 0x90000000);
            g.fill(x - 3, y - 2, x - 1, y + 21, t.waiting ? 0xFFF2C14E : accent);
            String head = (t.waiting ? "⚠ " : "✦ ") + font.plainSubstrByWidth(t.title == null ? "" : t.title, w - 40);
            g.text(font, head, x + 1, y, t.waiting ? 0xFFF2C14E : 0xFFE8E8E8);
            g.text(font, time, x + w - font.width(time) - 1, y, 0xFF8A8A8A);
            String step = t.waiting ? L.t("ждёт твоего ответа · K") : t.step;
            g.text(font, font.plainSubstrByWidth(ChatStyle.clean(step == null ? "" : step), w - 2), x + 1, y + 10, 0xFF8A8A8A);
            y += 26;
        }
        if (TASKS.size() > 3) g.text(font, "+" + (TASKS.size() - 3), x + 1, y, 0xFF8A8A8A);
    }

    private static String str(JsonObject e, String key) {
        return e.has(key) && !e.get(key).isJsonNull() ? e.get(key).getAsString() : "";
    }
}
