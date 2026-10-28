package dev.claudechat;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Screenshot;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.Component;
import net.minecraft.util.FormattedCharSequence;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;

/**
 * Developer-only visual check (gradlew runShots): opens the settings screen, the
 * conversation window and chat previews with sample events, takes screenshots into
 * run/screenshots, then quits. Inactive unless -Dclaudecraft.devshots=true is set.
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
            case 200 -> client.setScreenAndShow(SettingsScreen.preview(mod, status(true, "done")));
            case 240 -> Screenshot.grab(client, true);
            case 260 -> {
                Deque<JsonObject> log = new ArrayDeque<>();
                for (String e : WORK) log.add(parse(e));
                mod.log.put("demo", log);
                client.setScreenAndShow(new ConversationScreen(mod, parse(STATE)));
            }
            case 300 -> Screenshot.grab(client, true);
            case 302 -> {
                Notify.onEvent(parse("{\"kind\":\"thinking\",\"chat\":\"a\",\"mode\":\"cc\",\"title\":\"Fix the build\"}"));
                Notify.onEvent(parse("{\"kind\":\"tool\",\"chat\":\"a\",\"mode\":\"cc\",\"text\":\"gradlew build  — Building\"}"));
                Notify.onEvent(parse("{\"kind\":\"thinking\",\"chat\":\"b\",\"mode\":\"c\",\"title\":\"Explain mixins\"}"));
                Notify.onEvent(parse("{\"kind\":\"permission\",\"chat\":\"b\",\"mode\":\"c\",\"title\":\"Explain mixins\"}"));
                client.setScreenAndShow(new HudPreview());
            }
            case 307 -> Screenshot.grab(client, true);
            case 310 -> client.setScreenAndShow(new FileScreen(mod, null, parse(FILES)));
            case 318 -> Screenshot.grab(client, true);
            case 362 -> client.setScreenAndShow(new FileScreen(mod, null, parse(FILES)).previewSearch(
                com.google.gson.JsonParser.parseString("[{\"path\":\"Main.java\",\"line\":12,\"col\":4,\"text\":\"// TODO handle reconnect\"},"
                    + "{\"path\":\"client/Hud.java\",\"line\":88,\"col\":2,\"text\":\"// TODO scale with GUI\"}]").getAsJsonArray(),
                parse("{\"path\":\"C:\\\\dev\\\\my-mod\\\\src\\\\Main.java\",\"binary\":false,\"size\":4210,\"start\":4,\"total\":40,"
                    + "\"lines\":[\"public final class Main {\",\"    private int retries;\",\"\",\"    void connect() {\","
                    + "\"        open();\",\"        // TODO handle reconnect\",\"        retries++;\",\"    }\",\"}\"]}"), 9));
            case 368 -> Screenshot.grab(client, true);
            case 370 -> FileScreen.resetSearchMode();
            case 320 -> client.setScreenAndShow(new ChatPreview(PROJECT));
            case 360 -> Screenshot.grab(client, true);
            case 380 -> client.setScreenAndShow(new ChatPreview(TOOLS));
            case 420 -> Screenshot.grab(client, true);
            case 440 -> client.setScreenAndShow(new ChatPreview(WORK));
            case 480 -> Screenshot.grab(client, true);
            case 485 -> client.setScreenAndShow(new EditorScreen(mod, null, "src/main/java/dev/demo/Hello.java").withText(EDITOR));
            case 495 -> Screenshot.grab(client, true);
            case 510 -> client.setScreenAndShow(new ChatPreview(EDITS));
            case 540 -> Screenshot.grab(client, true);
            case 550 -> client.stop();
            default -> { }
        }
    }

    private static JsonObject parse(String json) { return JsonParser.parseString(json).getAsJsonObject(); }

    private static JsonObject status(boolean logged, String login) {
        return parse("{\"account\":{\"logged_in\":" + logged + ",\"method\":\"claude.ai\","
            + "\"email\":" + (logged ? "\"player@example.com\"" : "null") + "},"
            + "\"api_key\":" + (logged ? "\"sk-ant-…abcd\"" : "null") + ","
            + "\"login\":{\"state\":\"" + login + "\",\"url\":\"https://claude.com/cai/oauth/authorize?code=true\",\"error\":null},"
            + "\"github\":{\"installed\":true,\"login\":\"akijke\",\"connector\":true,\"token\":false,"
            + "\"flow\":{\"state\":\"idle\",\"code\":null,\"url\":null,\"error\":null}}}");
    }

    private static final String STATE = "{\"selected\":{\"cc\":\"demo\",\"c\":\"x\"},\"project\":\"my-mod\","
        + "\"project_path\":\"C:/dev/my-mod\",\"projects\":[],\"chats\":["
        + "{\"id\":\"demo\",\"mode\":\"cc\",\"title\":\"Fix the build\",\"project\":\"my-mod\",\"running\":true,\"queue\":0,\"updated\":3},"
        + "{\"id\":\"demo2\",\"mode\":\"cc\",\"title\":\"test-writer: tests for utils\",\"project\":\"my-mod\",\"running\":true,\"queue\":0,\"updated\":2},"
        + "{\"id\":\"demo3\",\"mode\":\"cc\",\"title\":\"README\",\"project\":\"my-mod\",\"running\":false,\"queue\":0,\"updated\":1}]}";

    private static final String FILES = "{\"path\":\"C:\\\\dev\\\\my-mod\\\\src\",\"parent\":\"C:\\\\dev\\\\my-mod\",\"more\":0,"
        + "\"project\":null,\"git\":false,\"claude\":\"C:\\\\dev\\\\my-mod\",\"current_project\":\"my-mod\","
        + "\"workspace\":\"C:\\\\mc\\\\workspace\",\"home\":\"C:\\\\Users\\\\me\",\"roots\":[\"C:\\\\\",\"D:\\\\\"],\"rows\":["
        + "{\"name\":\"client\",\"dir\":true,\"size\":0,\"mtime\":1790000000,\"hidden\":false},"
        + "{\"name\":\"main\",\"dir\":true,\"size\":0,\"mtime\":1790000000,\"hidden\":false},"
        + "{\"name\":\".cache\",\"dir\":true,\"size\":0,\"mtime\":1790000000,\"hidden\":true},"
        + "{\"name\":\"Main.java\",\"dir\":false,\"size\":4210,\"mtime\":1790000000,\"hidden\":false},"
        + "{\"name\":\"Util.java\",\"dir\":false,\"size\":980,\"mtime\":1790000000,\"hidden\":false},"
        + "{\"name\":\"notes.md\",\"dir\":false,\"size\":120400,\"mtime\":1790000000,\"hidden\":false}]}";

    private static final String[] WORK = {
        "{\"seq\":1,\"chat\":\"demo\",\"mode\":\"cc\",\"kind\":\"thinking\",\"title\":\"Fix the build\",\"text\":\"Думаю…\",\"queued\":0}",
        "{\"seq\":2,\"chat\":\"demo\",\"mode\":\"cc\",\"kind\":\"tool\",\"icon\":\"📄\",\"text\":\"Reading build.gradle\"}",
        "{\"seq\":3,\"chat\":\"demo\",\"mode\":\"cc\",\"kind\":\"tool\",\"icon\":\"$\",\"text\":\"gradlew build  — Building the project\"}",
        "{\"seq\":4,\"chat\":\"demo\",\"mode\":\"cc\",\"kind\":\"tool_error\",\"text\":\"error: cannot find symbol ClientCommandManager\"}",
        "{\"seq\":5,\"chat\":\"demo\",\"mode\":\"cc\",\"kind\":\"tool\",\"icon\":\"◆\",\"text\":\"Agent debugger: Find the cause of the compile error\"}",
        "{\"seq\":6,\"chat\":\"demo\",\"mode\":\"cc\",\"kind\":\"tool\",\"icon\":\"🔍\",\"text\":\"Searching “ClientCommands” in .gradle\",\"agent\":\"debugger\"}",
        "{\"seq\":7,\"chat\":\"demo\",\"mode\":\"cc\",\"kind\":\"tool\",\"icon\":\"✎\",\"text\":\"Editing src/client/Main.java\"}",
        "{\"seq\":8,\"chat\":\"demo\",\"mode\":\"cc\",\"kind\":\"text_part\",\"first\":true,\"text\":\"## Fixed\"}",
        "{\"seq\":9,\"chat\":\"demo\",\"mode\":\"cc\",\"kind\":\"text_part\",\"first\":false,\"text\":\"In 26.3 the class was renamed to **ClientCommands**:\"}",
        "{\"seq\":10,\"chat\":\"demo\",\"mode\":\"cc\",\"kind\":\"text_part\",\"first\":false,\"text\":\"```java\"}",
        "{\"seq\":11,\"chat\":\"demo\",\"mode\":\"cc\",\"kind\":\"text_part\",\"first\":false,\"text\":\"import static ...ClientCommands.*;\"}",
        "{\"seq\":12,\"chat\":\"demo\",\"mode\":\"cc\",\"kind\":\"text_part\",\"first\":false,\"text\":\"```\"}",
        "{\"seq\":13,\"chat\":\"demo\",\"mode\":\"cc\",\"kind\":\"text_part\",\"first\":false,\"text\":\"- the build passes, `gradlew build` ✔\"}",
        "{\"seq\":14,\"chat\":\"demo\",\"mode\":\"cc\",\"kind\":\"done\",\"text\":\"Done\",\"seconds\":41.2,\"input\":38200,\"output\":1450,\"cost\":0.092}",
        "{\"seq\":15,\"chat\":\"demo\",\"mode\":\"cc\",\"kind\":\"permission\",\"request\":\"r1\",\"text\":\"Bash\\n{}\",\"summary\":\"$ git push\",\"choices\":[\"Allow\",\"Deny\",\"Always allow Bash\"]}",
    };

    private static final String[] EDITS = {
        "{\"seq\":1,\"chat\":\"demo\",\"mode\":\"cc\",\"kind\":\"edit_diff\",\"text\":\"✎ Editing src/Main.java\",\"path\":\"C:/dev/my-mod/src/Main.java\","
            + "\"added\":2,\"removed\":1,\"lines\":[\"@@ -3,4 +3,5 @@\",\" import x;\",\"-import old.Commands;\",\"+import new.ClientCommands;\",\"+import new.Hud;\",\" class Main {}\"]}",
        "{\"seq\":2,\"chat\":\"demo\",\"mode\":\"cc\",\"kind\":\"permission\",\"request\":\"r2\",\"text\":\"Write\\n{}\",\"summary\":\"✚ Creating src/Hud.java\","
            + "\"diff\":{\"new\":true,\"added\":3,\"removed\":0,\"lines\":[\"@@ -0,0 +1,3 @@\",\"+final class Hud {\",\"+    void draw() { }\",\"+}\"]},"
            + "\"choices\":[\"Allow\",\"Deny\",\"Always allow Write\"]}",
        "{\"seq\":3,\"chat\":\"demo\",\"mode\":\"cc\",\"kind\":\"undo\",\"text\":\"Undo\",\"prompt\":\"fix the build\",\"restored\":[\"C:/dev/my-mod/src/Main.java\"],"
            + "\"removed\":[\"C:/dev/my-mod/src/Hud.java\"],\"skipped\":[],\"left\":2}",
    };

    private static final String[] PROJECT = {
        "{\"mode\":\"cc\",\"kind\":\"projects\",\"text\":\"Projects\",\"browsing\":\"C:/dev\",\"rows\":["
            + "{\"name\":\"workspace\",\"path\":\"C:/mc/config/claude-chat/workspace\",\"exists\":true,\"git\":false,\"chats\":1,\"running\":0,\"current\":false},"
            + "{\"name\":\"my-mod\",\"path\":\"C:/dev/my-mod\",\"exists\":true,\"git\":true,\"chats\":3,\"running\":2,\"current\":true},"
            + "{\"name\":\"website\",\"path\":\"D:/web/site\",\"exists\":true,\"git\":true,\"chats\":2,\"running\":0,\"current\":false}]}",
        "{\"mode\":\"cc\",\"kind\":\"git_status\",\"branch\":\"main\",\"upstream\":\"origin/main\",\"ahead\":2,\"behind\":0,\"project\":\"my-mod\","
            + "\"url\":\"https://github.com/akijke/my-mod\",\"remote\":\"git@github.com:akijke/my-mod.git\",\"last\":\"a1b2c3d fix: commands (5 min ago)\","
            + "\"more\":0,\"files\":[{\"code\":\" M\",\"path\":\"src/Main.java\"},{\"code\":\"A \",\"path\":\"src/Util.java\"},{\"code\":\"??\",\"path\":\"notes.md\"}]}",
        "{\"mode\":\"cc\",\"kind\":\"diff\",\"path\":\"src/Main.java\",\"staged\":false,\"truncated\":false,\"empty\":false,\"text\":"
            + "\"diff --git a/src/Main.java b/src/Main.java\\n--- a/src/Main.java\\n+++ b/src/Main.java\\n@@ -1,4 +1,4 @@\\n import x;\\n-import old.Commands;\\n+import new.ClientCommands;\\n class Main {}\"}",
        "{\"mode\":\"cc\",\"kind\":\"listing\",\"path\":\"C:/dev/my-mod\",\"parent\":\"C:/dev\",\"project\":\"my-mod\",\"git\":true,\"more\":0,\"rows\":["
            + "{\"name\":\"src\",\"dir\":true,\"size\":0},{\"name\":\"gradle\",\"dir\":true,\"size\":0},{\"name\":\"build.gradle\",\"dir\":false,\"size\":1830},{\"name\":\"README.md\",\"dir\":false,\"size\":9100}]}",
    };

    private static final String EDITOR = String.join("\n",
        "package dev.demo;",
        "",
        "import java.util.List;",
        "",
        "/** Says hello to everyone in the list. */",
        "public final class Hello {",
        "    private static final int TIMES = 3;",
        "",
        "    @Override",
        "    public String toString() { return \"Hello\"; }",
        "",
        "    public static void main(String[] args) {",
        "        List<String> names = List.of(\"Steve\", \"Alex\");",
        "        for (int i = 0; i < TIMES; i++) {",
        "            // greet each player",
        "            names.forEach(n -> System.out.println(\"Hi, \" + n + \"!\"));",
        "        }",
        "    }",
        "}");

    private static final String[] TOOLS = {
        "{\"mode\":\"cc\",\"kind\":\"agents\",\"text\":\"Agents\",\"rows\":["
            + "{\"name\":\"code-reviewer\",\"description\":\"Review\",\"source\":\"built-in\",\"enabled\":true,\"tools\":[]},"
            + "{\"name\":\"debugger\",\"description\":\"Debug\",\"source\":\"built-in\",\"enabled\":true,\"tools\":[]},"
            + "{\"name\":\"lint-fixer\",\"description\":\"Lint\",\"source\":\"project\",\"enabled\":true,\"tools\":[]},"
            + "{\"name\":\"docs-writer\",\"description\":\"Up-to-date library docs\",\"source\":\"built-in\",\"enabled\":false,\"tools\":[]}]}",
        "{\"mode\":\"cc\",\"kind\":\"tasks\",\"text\":\"Tasks\",\"rows\":["
            + "{\"id\":\"demo\",\"mode\":\"cc\",\"title\":\"Fix the build\",\"project\":\"my-mod\",\"state\":\"working\",\"code\":\"running\",\"step\":\"$ gradlew build\",\"queue\":1,\"seconds\":42},"
            + "{\"id\":\"demo2\",\"mode\":\"cc\",\"title\":\"test-writer: tests for utils\",\"project\":\"my-mod\",\"state\":\"waiting for you\",\"code\":\"waiting\",\"step\":\"Creating src/test/UtilTest.java\",\"queue\":0,\"seconds\":12}]}",
        "{\"mode\":\"cc\",\"kind\":\"connectors\",\"text\":\"Connectors\",\"rows\":["
            + "{\"id\":\"github\",\"name\":\"GitHub\",\"group\":\"Code & git\",\"about\":\"Issues, PR\",\"enabled\":true,\"missing\":[],\"needs_node\":false,\"status\":\"connected\"},"
            + "{\"id\":\"sentry\",\"name\":\"Sentry\",\"group\":\"Code & git\",\"about\":\"Production errors\",\"enabled\":false,\"missing\":[\"SENTRY_ACCESS_TOKEN\"],\"needs_node\":false,\"status\":null},"
            + "{\"id\":\"playwright\",\"name\":\"Playwright\",\"group\":\"Browser & web\",\"about\":\"Drive a browser\",\"enabled\":true,\"missing\":[],\"needs_node\":false,\"status\":\"failed\"},"
            + "{\"id\":\"context7\",\"name\":\"Context7\",\"group\":\"Docs & search\",\"about\":\"Up-to-date library docs\",\"enabled\":false,\"missing\":[],\"needs_node\":false,\"status\":null}]}",
    };

    /** The task HUD panel and a toast, outside a world. */
    static final class HudPreview extends Screen {
        HudPreview() { super(Component.literal("hud")); }
        @Override public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float delta) {
            super.extractRenderState(g, mouseX, mouseY, delta);
            Notify.preview(g);
        }
    }

    /** Draws ChatStyle output like the chat HUD, without needing a world. */
    static final class ChatPreview extends Screen {
        private final String[] events;
        ChatPreview(String[] events) { super(Component.literal("preview")); this.events = events; }

        @Override public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float delta) {
            super.extractRenderState(g, mouseX, mouseY, delta);
            int width = 330, x = 4, lineHeight = 9;
            List<FormattedCharSequence> wrapped = new ArrayList<>();
            for (String e : events)
                for (Component line : ChatStyle.render(parse(e))) wrapped.addAll(font.split(line, width));
            int y = Math.max(2, height - 4 - wrapped.size() * lineHeight);
            g.fill(0, y - 2, width + 8, y + wrapped.size() * lineHeight + 2, 0x80000000);
            for (FormattedCharSequence line : wrapped) { g.text(font, line, x, y, 0xFFFFFFFF); y += lineHeight; }
        }
    }
}
