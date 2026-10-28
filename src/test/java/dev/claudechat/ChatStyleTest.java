package dev.claudechat;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.minecraft.network.chat.ClickEvent;
import net.minecraft.network.chat.Component;
import org.junit.jupiter.api.Test;
import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.List;
import static org.junit.jupiter.api.Assertions.*;

class ChatStyleTest {
    private static String text(List<Component> lines) {
        StringBuilder b = new StringBuilder();
        for (Component c : lines) b.append(c.getString()).append('\n');
        return b.toString();
    }

    /** Every click command found in a component tree. */
    private static List<String> commands(Component c) {
        List<String> out = new ArrayList<>();
        ClickEvent click = c.getStyle().getClickEvent();
        if (click instanceof ClickEvent.RunCommand r) out.add(r.command());
        if (click instanceof ClickEvent.SuggestCommand s) out.add(s.command());
        if (click instanceof ClickEvent.CopyToClipboard copy) out.add("copy:" + copy.value());
        for (Component s : c.getSiblings()) out.addAll(commands(s));
        return out;
    }

    private static List<Component> render(String json) { return ChatStyle.render(JsonParser.parseString(json).getAsJsonObject()); }

    @Test void markdownHeadingsListsCodeAndCopy() {
        boolean[] code = {false};
        assertEquals("Title", ChatStyle.markdownLine("## Title", code).getString());
        assertEquals("• item", ChatStyle.markdownLine("- item", code).getString());
        assertNull(ChatStyle.markdownLine("```", code));               // a fence without language prints nothing
        assertTrue(code[0]);
        ChatStyle.markdownLine("int x = 1;", code);
        ChatStyle.markdownLine("int y = 2;", code);
        Component close = ChatStyle.markdownLine("```", code);
        assertFalse(code[0]);
        assertEquals(List.of("copy:int x = 1;\nint y = 2;"), commands(close));
    }

    @Test void tablesAreAlignedAndHeaderSeparated() {
        List<Component> out = new ArrayList<>();
        ChatStyle.markdown("cc", "| a | bbbbbb |\n|---|---|\n| cccc | d |", out);
        String all = text(out);
        assertTrue(all.contains("a") && all.contains("bbbbbb") && all.contains("cccc"), all);
        assertEquals(3, out.size());  // header, rule, row
        assertTrue(out.get(1).getString().contains("├"));
    }

    @Test void permissionCardHasButtonsAndDiff() {
        List<Component> lines = render("{\"kind\":\"permission\",\"mode\":\"cc\",\"chat\":\"x\",\"request\":\"r9\",\"text\":\"Edit\\n{}\","
            + "\"summary\":\"✎ Editing a.txt\",\"choices\":[\"Allow\",\"Deny\",\"Always: Edit(src/**)\"],"
            + "\"diff\":{\"lines\":[\"@@ -1 +1 @@\",\"-old\",\"+new\"],\"added\":1,\"removed\":1}}");
        String all = text(lines);
        assertTrue(all.contains("-old") && all.contains("+new"), all);
        List<String> cmds = new ArrayList<>();
        for (Component c : lines) cmds.addAll(commands(c));
        assertTrue(cmds.containsAll(List.of("/cc reply r9 1", "/cc reply r9 2", "/cc reply r9 3")), cmds.toString());
    }

    @Test void doneLineShowsContextMeter() {
        String all = text(render("{\"kind\":\"done\",\"mode\":\"cc\",\"chat\":\"x\",\"text\":\"Done\",\"seconds\":2.0,"
            + "\"input\":1000,\"output\":10,\"context\":150000,\"window\":200000}"));
        assertTrue(all.contains("75%"), all);
    }

    /** Every sample event used for the screenshots renders without an exception. */
    @Test void allDevShotSamplesRender() throws Exception {
        int rendered = 0;
        for (Field f : DevShots.class.getDeclaredFields()) {
            if (!Modifier.isStatic(f.getModifiers()) || f.getType() != String[].class) continue;
            f.setAccessible(true);
            for (String json : (String[]) f.get(null)) {
                JsonObject e = JsonParser.parseString(json).getAsJsonObject();
                if (!e.has("kind")) continue;  // file-window data, not chat events
                assertNotNull(ChatStyle.render(e), json);
                rendered++;
            }
        }
        assertTrue(rendered > 20, "rendered " + rendered);
    }
}
