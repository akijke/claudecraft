package dev.claudechat;

import com.google.gson.JsonObject;
import net.fabricmc.fabric.api.client.screen.v1.ScreenEvents;
import net.fabricmc.fabric.api.client.screen.v1.ScreenMouseEvents;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.Tooltip;
import net.minecraft.client.gui.screens.ChatScreen;
import net.minecraft.network.chat.Component;

/**
 * Size of the vanilla chat (T), beyond the 320×180 limit of the game's own options:
 * "Expand" fills the screen while the chat is open, the corner grip at the top right
 * of the chat drags it to any size (double click = back to the game's size).
 * Stored in client.json → "chatSize" {w, h, expanded}; sizes are in chat pixels
 * (before "Chat scale"). The mixin in dev.claudechat.mixin asks width()/height().
 */
public final class ChatSize {
    private static int w, h;            // 0 = the game's own setting
    private static boolean expanded, loaded, dragging;
    private static long lastClick;
    private static final int GRIP = 7;

    private ChatSize() { }

    /** Chat width in chat pixels, or 0 to keep the game's. */
    public static int width(boolean focused) {
        load();
        if (expanded && focused) return Math.max(40, (int) ((gui().getGuiScaledWidth() - 14) / scale()));
        return w;
    }

    /** Chat height in chat pixels, or 0 to keep the game's. Only the open chat changes size. */
    public static int height(boolean focused) {
        load();
        if (!focused) return 0;
        if (expanded) return Math.max(20, (int) ((gui().getGuiScaledHeight() - 60) / scale()));
        return h;
    }

    static void register() {
        ScreenEvents.AFTER_INIT.register((client, screen, sw, sh) -> {
            if (!(screen instanceof ChatScreen)) return;
            rescale();  // the expanded size applies only while the chat is open
            Button toggle = Button.builder(Component.literal(label()), b -> {
                    expanded = !expanded;
                    b.setMessage(Component.literal(label()));
                    save();
                    rescale();
                }).bounds(0, 0, 66, 14)
                .tooltip(Tooltip.create(Component.literal(L.t("Чат на весь экран и обратно. Размер — тяни за уголок справа сверху, двойной клик по нему — обычный размер")))).build();
            net.fabricmc.fabric.api.client.screen.v1.Screens.getWidgets(screen).add(toggle);
            ScreenEvents.afterTick(screen).register(s -> {
                int[] box = box();
                toggle.setX(Math.min(s.width - 70, box[2] + 4));
                toggle.setY(Math.max(2, box[1]));
            });
            ScreenEvents.afterExtract(screen).register((s, g, mx, my, delta) -> {
                int[] box = box();
                boolean hot = dragging || near(box, mx, my);
                int c = hot ? 0xFFF2C14E : 0xA0E8E8E8;
                // corner grip: an L in the top right corner of the chat
                g.fill(box[2] - GRIP, box[1], box[2], box[1] + 1, c);
                g.fill(box[2] - 1, box[1], box[2], box[1] + GRIP, c);
            });
            ScreenMouseEvents.allowMouseClick(screen).register((s, event) -> {
                if (event.button() != 0 || !near(box(), event.x(), event.y())) return true;
                long now = System.currentTimeMillis();
                if (now - lastClick < 350) { w = 0; h = 0; expanded = false; toggle.setMessage(Component.literal(label())); save(); rescale(); }
                lastClick = now;
                dragging = true;
                return false;
            });
            ScreenMouseEvents.allowMouseDrag(screen).register((s, event, dx, dy) -> {
                if (!dragging) return true;
                double sc = scale();
                int[] box = box();
                int nw = clamp((int) ((event.x() - 4) / sc) - 8, 40, (int) ((s.width - 14) / sc));
                int nh = clamp((int) ((box[3] - event.y()) / sc), 20, (int) ((s.height - 60) / sc));
                if (expanded) { expanded = false; toggle.setMessage(Component.literal(label())); }
                boolean rewrap = nw != w;
                w = nw;
                h = nh;
                if (rewrap) rescale();
                return false;
            });
            ScreenMouseEvents.allowMouseRelease(screen).register((s, event) -> {
                if (!dragging) return true;
                dragging = false;
                save();
                return false;
            });
            ScreenEvents.remove(screen).register(s -> { dragging = false; Minecraft.getInstance().execute(ChatSize::rescale); });
        });
    }

    private static String label() { return expanded ? L.t("Свернуть") : L.t("Развернуть"); }

    /** {left, top, right, bottom} of the open chat on screen (GUI pixels). */
    private static int[] box() {
        var chat = Minecraft.getInstance().gui.hud.getChat();
        double sc = scale();
        int cw = width(true) > 0 ? width(true) : net.minecraft.client.gui.components.ChatComponent.getWidth(Minecraft.getInstance().options.chatWidth().get());
        int ch = height(true) > 0 ? height(true) : net.minecraft.client.gui.components.ChatComponent.getHeight(Minecraft.getInstance().options.chatHeightFocused().get());
        int bottom = gui().getGuiScaledHeight() - 40;
        return new int[]{0, (int) (bottom - ch * sc), (int) Math.ceil((cw + 8) * sc), bottom};
    }

    private static boolean near(int[] box, double x, double y) {
        return x >= box[2] - GRIP - 3 && x <= box[2] + 3 && y >= box[1] - 3 && y <= box[1] + GRIP + 3;
    }

    private static int clamp(int v, int lo, int hi) { return Math.max(lo, Math.min(Math.max(lo, hi), v)); }
    private static double scale() { return Math.max(0.1, Minecraft.getInstance().options.chatScale().get()); }
    private static com.mojang.blaze3d.platform.Window gui() { return Minecraft.getInstance().getWindow(); }
    private static void rescale() { Minecraft.getInstance().gui.hud.getChat().rescaleChat(); }

    private static void load() {
        if (loaded) return;
        loaded = true;
        ClaudeChat mod = ClaudeChat.instance();
        if (mod == null || !mod.launcher.settings.has("chatSize") || !mod.launcher.settings.get("chatSize").isJsonObject()) return;
        JsonObject o = mod.launcher.settings.getAsJsonObject("chatSize");
        try {
            w = o.has("w") ? o.get("w").getAsInt() : 0;
            h = o.has("h") ? o.get("h").getAsInt() : 0;
            expanded = o.has("expanded") && o.get("expanded").getAsBoolean();
        } catch (Exception ignored) { }
    }

    private static void save() {
        ClaudeChat mod = ClaudeChat.instance();
        if (mod == null) return;
        JsonObject o = new JsonObject();
        o.addProperty("w", w);
        o.addProperty("h", h);
        o.addProperty("expanded", expanded);
        mod.launcher.settings.add("chatSize", o);
        mod.launcher.saveAll();
    }
}
