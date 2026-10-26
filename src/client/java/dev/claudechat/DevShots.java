package dev.claudechat;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Screenshot;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.Component;
import net.minecraft.util.FormattedCharSequence;
import java.util.ArrayList;
import java.util.List;

/**
 * Developer-only visual check (gradlew runShots): opens the settings screen and a chat
 * preview with sample events, takes screenshots into run/screenshots, then quits.
 * Inactive unless -Dclaudecraft.devshots=true is set.
 */
final class DevShots {
    static final boolean ENABLED = Boolean.getBoolean("claudecraft.devshots");
    private final ClaudeChat mod;
    private int ticks;

    DevShots(ClaudeChat mod) { this.mod = mod; }

    void tick(Minecraft client) {
        if (!ENABLED) return;
        ticks++;
        switch (ticks) {
            case 200 -> client.setScreenAndShow(SettingsScreen.preview(mod, status(false, "idle")));
            case 240 -> Screenshot.grab(client, true);
            case 260 -> client.setScreenAndShow(SettingsScreen.preview(mod, status(false, "waiting_code")));
            case 300 -> Screenshot.grab(client, true);
            case 320 -> client.setScreenAndShow(SettingsScreen.preview(mod, status(true, "done")));
            case 360 -> Screenshot.grab(client, true);
            case 380 -> client.setScreenAndShow(new ChatPreview(0));
            case 420 -> Screenshot.grab(client, true);
            case 440 -> client.setScreenAndShow(new ChatPreview(1));
            case 480 -> Screenshot.grab(client, true);
            case 520 -> client.stop();
            default -> { }
        }
    }

    private static JsonObject status(boolean logged, String login) {
        return JsonParser.parseString("{\"account\":{\"logged_in\":" + logged + ",\"method\":\"claude.ai\","
            + "\"email\":" + (logged ? "\"player@example.com\"" : "null") + "},"
            + "\"api_key\":" + (logged ? "\"sk-ant-…abcd\"" : "null") + ","
            + "\"login\":{\"state\":\"" + login + "\",\"url\":\"https://claude.com/cai/oauth/authorize?code=true\",\"error\":null}}")
            .getAsJsonObject();
    }

    /** Draws ChatStyle output like the chat HUD, without needing a world. */
    static final class ChatPreview extends Screen {
        private final int page;
        ChatPreview(int page) { super(Component.literal("preview")); this.page = page; }

        private List<Component> lines() {
            List<Component> out = new ArrayList<>();
            String[] events = page == 0 ? new String[]{
                "{\"mode\":\"cc\",\"kind\":\"thinking\",\"title\":\"Почини сборку\",\"text\":\"Думаю…\",\"queued\":1}",
                "{\"mode\":\"cc\",\"kind\":\"tool\",\"icon\":\"📄\",\"text\":\"Читаю src/Main.java\"}",
                "{\"mode\":\"cc\",\"kind\":\"tool\",\"icon\":\"🔍\",\"text\":\"Ищу «TODO» в src\"}",
                "{\"mode\":\"cc\",\"kind\":\"todo\",\"text\":\"План\",\"rows\":[{\"text\":\"Найти ошибку\",\"status\":\"completed\"},{\"text\":\"Исправляю импорт\",\"status\":\"in_progress\"},{\"text\":\"Запустить тесты\",\"status\":\"pending\"}]}",
                "{\"mode\":\"cc\",\"kind\":\"tool\",\"icon\":\"$\",\"text\":\"gradlew build  — Собираю проект\"}",
                "{\"mode\":\"cc\",\"kind\":\"permission\",\"request\":\"r1\",\"text\":\"Bash\\n{\\\"command\\\":\\\"gradlew build\\\"}\",\"summary\":\"$ gradlew build\",\"choices\":[\"Разрешить\",\"Отклонить\",\"Всегда разрешать Bash\"]}",
                "{\"mode\":\"cc\",\"kind\":\"tool_error\",\"text\":\"error: cannot find symbol Foo\"}",
                "{\"mode\":\"cc\",\"kind\":\"text\",\"text\":\"## Готово\\nИсправил **импорт** в `Main.java`:\\n- убрал лишний класс\\n- добавил тест\\n```\\nimport java.util.List;\\n```\"}",
                "{\"mode\":\"cc\",\"kind\":\"done\",\"text\":\"Готово\",\"seconds\":12.4,\"input\":18234,\"output\":912,\"cost\":0.0412}",
            } : new String[]{
                "{\"mode\":\"cc\",\"kind\":\"listing\",\"path\":\"C:/projects/mod\",\"parent\":\"C:/projects\",\"more\":0,\"rows\":[{\"name\":\"src\",\"dir\":true,\"size\":0},{\"name\":\"gradle\",\"dir\":true,\"size\":0},{\"name\":\"build.gradle\",\"dir\":false,\"size\":1830},{\"name\":\"README.md\",\"dir\":false,\"size\":9100},{\"name\":\"settings.gradle\",\"dir\":false,\"size\":210},{\"name\":\"gradlew.bat\",\"dir\":false,\"size\":4776}]}",
                "{\"mode\":\"cc\",\"kind\":\"cwd\",\"text\":\"C:/projects/mod/src\",\"note\":\"\"}",
                "{\"mode\":\"cc\",\"kind\":\"file\",\"path\":\"C:/projects/mod/settings.gradle\",\"text\":\"pluginManagement {\\n    repositories { mavenCentral() }\\n}\\nrootProject.name = 'mod'\",\"truncated\":false}",
                "{\"mode\":\"c\",\"kind\":\"tokens\",\"text\":\"Токены\",\"note\":\"Стоимость — оценка по ценам API.\",\"rows\":[{\"label\":\"Этот чат: привет\",\"input\":18234,\"output\":912,\"cache\":15000,\"cost\":0.041,\"turns\":3},{\"label\":\"Все чаты\",\"input\":1203400,\"output\":40200,\"cache\":900000,\"cost\":2.31,\"turns\":40}]}",
                "{\"mode\":\"cc\",\"kind\":\"auto\",\"text\":\"Авторазрешение: чтение, поиск и правка файлов без вопросов.\",\"mode_value\":\"edits\",\"allowed\":[\"WebFetch\"]}",
                "{\"mode\":\"c\",\"kind\":\"question\",\"request\":\"q1\",\"text\":\"Какой язык?\\n1. Java — быстро\\n2. Kotlin — коротко\",\"choices\":[\"Java\",\"Kotlin\"],\"multiple\":false}",
                "{\"mode\":\"c\",\"kind\":\"error\",\"text\":\"Ошибка: 401 authentication_error\\nПроверь вход в аккаунт Claude: /c settings\"}",
            };
            for (String e : events) out.addAll(ChatStyle.render(JsonParser.parseString(e).getAsJsonObject()));
            return out;
        }

        @Override public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float delta) {
            super.extractRenderState(g, mouseX, mouseY, delta);
            int width = 320, x = 4, lineHeight = 9;
            List<FormattedCharSequence> wrapped = new ArrayList<>();
            for (Component line : lines()) wrapped.addAll(font.split(line, width));
            int y = Math.max(4, height - 40 - wrapped.size() * lineHeight);
            g.fill(0, y - 2, width + 8, y + wrapped.size() * lineHeight + 2, 0x80000000);
            for (FormattedCharSequence line : wrapped) { g.text(font, line, x, y, 0xFFFFFFFF); y += lineHeight; }
        }
    }
}
