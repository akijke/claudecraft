package dev.claudechat;

import com.google.gson.JsonObject;
import net.fabricmc.fabric.api.client.screen.v1.ScreenEvents;
import net.fabricmc.fabric.api.client.screen.v1.ScreenMouseEvents;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.screens.ChatScreen;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.Component;

/**
 * Size of the vanilla chat (T), beyond the 320×180 limit of the game's own options, like an
 * app window that is always pinned to the bottom left corner: while the chat is open, its top
 * edge drags the height, its right edge the width and the top right corner both (double click
 * on the corner = back to the game's size). A small toolbar above the top right corner holds
 * "< >" (fill the screen) / "> <" (back) and ↺ (default size, only shown when the size differs).
 * Stored in client.json → "chatSize" {w, h, expanded}; sizes are in chat pixels
 * (before "Chat scale"). The mixin in dev.claudechat.mixin asks width()/height().
 */
public final class ChatSize {
    private static int w, h;            // 0 = the game's own setting
    private static boolean expanded, loaded, dragging, moved;
    private static int dragMode;        // TOP | RIGHT while dragging
    private static double pressX, pressY;
    private static long lastClick;
    private static final int GRIP = 7, EDGE = 2, TOP = 1, RIGHT = 2;
    private static final int BUTTON_H = 11, EXPAND_W = 15, RESET_W = 13;
    private static final int PILL = 0xB4101014, BORDER = 0x40FFFFFF, ICON = 0xFFE8E8E8, HOVER_BG = 0x30FFFFFF;
    /** The ↺ icon, 7×7: an open circle with the arrow head at the top left. */
    private static final String[] RESET_ICON = {
        "#.###..",
        "##...#.",
        "###...#",
        "......#",
        "#.....#",
        ".#...#.",
        "..###..",
    };

    private ChatSize() { }

    /** Chat width in chat pixels, or 0 to keep the game's. */
    public static int width(boolean focused) {
        load();
        if (expanded && focused) return maxW();
        return w;
    }

    /** Chat height in chat pixels, or 0 to keep the game's. Only the open chat changes size. */
    public static int height(boolean focused) {
        load();
        if (!focused) return 0;
        if (expanded) return maxH();
        return h;
    }

    static void register() {
        ScreenEvents.AFTER_INIT.register((client, screen, sw, sh) -> {
            if (!(screen instanceof ChatScreen)) return;
            rescale();  // the expanded size applies only while the chat is open
            ScreenEvents.afterExtract(screen).register((s, g, mx, my, delta) -> {
                // After a click this class took, the game marks no button as held and sends no
                // mouseDragged (MouseHandler.onButton returns early): follow the cursor every frame
                // from the press until the release event instead.
                if (dragging) dragTo(mx, my);
                drawFrame(g, mx, my);
                drawToolbar(g, mx, my);
            });
            ScreenMouseEvents.allowMouseClick(screen).register((s, event) -> {
                if (event.button() != 0) return true;
                int button = buttonAt(event.x(), event.y());
                if (button == 1) { toggle(); return false; }
                if (button == 2) { reset(); return false; }
                int mode = edgeAt(event.x(), event.y());
                if (mode == 0) return true;
                long now = System.currentTimeMillis();
                if (mode == (TOP | RIGHT) && now - lastClick < 350) { reset(); lastClick = 0; return false; }
                lastClick = now;
                dragging = true;
                dragMode = mode;
                moved = false;
                pressX = event.x();
                pressY = event.y();
                return false;
            });
            ScreenMouseEvents.allowMouseDrag(screen).register((s, event, dx, dy) -> {
                if (!dragging) return true;
                dragTo(event.x(), event.y());
                return false;
            });
            ScreenMouseEvents.allowMouseRelease(screen).register((s, event) -> {
                if (!dragging) return true;
                dragging = false;
                if (moved) save();
                return false;
            });
            ScreenEvents.remove(screen).register(s -> { dragging = false; Minecraft.getInstance().execute(ChatSize::rescale); });
        });
    }

    /** The edge held at x, y: the dragged edge(s) follow the cursor, the bottom left corner stays. */
    private static void dragTo(double x, double y) {
        // no change before the cursor really moves: a double click on the corner stays a reset
        if (!moved && Math.abs(x - pressX) < 2 && Math.abs(y - pressY) < 2) return;
        moved = true;
        double sc = scale();
        int[] box = box();
        int nw = (dragMode & RIGHT) != 0 ? clamp((int) Math.round(x / sc) - 8, 40, maxW()) : currentW();
        int nh = (dragMode & TOP) != 0 ? clamp((int) Math.round((box[3] - y) / sc), 20, maxH()) : currentH();
        if (nw == w && nh == h && !expanded) return;
        boolean rewrap = nw != currentW() || expanded;
        expanded = false;
        w = nw;
        h = nh;
        if (rewrap) rescale();
    }

    /** {left, top, right, bottom} of the open chat, for the live test. */
    static int[] boxForTest() { return box(); }

    /** Expand ↔ collapse, as a click on the "< >" icon does. */
    static void toggle() {
        expanded = !expanded;
        save();
        rescale();
    }

    /** Back to the game's own chat size, as the ↺ button does. */
    static void reset() {
        w = 0;
        h = 0;
        expanded = false;
        save();
        rescale();
    }

    static boolean expandedForTest() { load(); return expanded; }

    /** True when the chat has the game's own size (the ↺ button is hidden then). */
    static boolean isDefault() { load(); return w == 0 && h == 0 && !expanded; }

    /** {left, top, right, bottom} of the reset ↺ button, or null while it is hidden; for the live test. */
    static int[] resetButtonForTest() { return isDefault() ? null : resetButton(); }

    // ---- frame and edges ----------------------------------------------------------

    /** TOP, RIGHT, TOP|RIGHT (corner) or 0 for the point x, y. */
    private static int edgeAt(double x, double y) {
        int[] b = box();
        if (x >= b[2] - GRIP - 3 && x <= b[2] + 3 && y >= b[1] - 3 && y <= b[1] + GRIP + 3) return TOP | RIGHT;
        if (y >= b[1] - EDGE - 1 && y <= b[1] + EDGE && x >= b[0] && x < b[2]) return TOP;
        if (x >= b[2] - EDGE && x <= b[2] + EDGE + 1 && y >= b[1] && y < b[3]) return RIGHT;
        return 0;
    }

    /** A thin border along the free edges, the corner grip, and an accent line on the edge under the cursor. */
    private static void drawFrame(GuiGraphicsExtractor g, int mx, int my) {
        int[] b = box();
        int hot = dragging ? dragMode : (buttonAt(mx, my) != 0 ? 0 : edgeAt(mx, my));
        int accent = accent();
        // subtle frame: top and right edges (left and bottom are the screen's corner)
        g.fill(b[0], b[1] - 1, b[2], b[1], (hot & TOP) != 0 ? accent : BORDER);
        g.fill(b[2], b[1] - 1, b[2] + 1, b[3], (hot & RIGHT) != 0 ? accent : BORDER);
        // a soft second line on the highlighted edge, so it reads as "this edge moves"
        if ((hot & TOP) != 0) g.fill(b[0], b[1] - 2, b[2] + 1, b[1] - 1, 0x60000000 | (accent & 0xFFFFFF));
        if ((hot & RIGHT) != 0) g.fill(b[2] + 1, b[1] - 1, b[2] + 2, b[3], 0x60000000 | (accent & 0xFFFFFF));
        // corner grip: an L inside the top right corner
        int c = hot == (TOP | RIGHT) ? accent : 0xA0E8E8E8;
        g.fill(b[2] - GRIP, b[1], b[2], b[1] + 1, c);
        g.fill(b[2] - 1, b[1], b[2], b[1] + GRIP, c);
        if (dragging && moved) {
            var font = Minecraft.getInstance().font;
            String size = currentW() + " x " + currentH();
            int tx = Math.max(2, b[2] - GRIP - 4 - font.width(size)), ty = b[1] + 3;
            g.fill(tx - 2, ty - 2, tx + font.width(size) + 2, ty + 9, PILL);
            g.text(font, size, tx, ty, accent, false);
        } else if (hot != 0 && buttonAt(mx, my) == 0) {
            String tip = hot == TOP ? L.t("Тяните, чтобы изменить высоту") : hot == RIGHT ? L.t("Тяните, чтобы изменить ширину")
                : L.t("Тяните, чтобы изменить размер; двойной клик — обычный размер");
            g.setTooltipForNextFrame(Minecraft.getInstance().font, Component.literal(tip), mx, my);
        }
    }

    // ---- toolbar ------------------------------------------------------------------

    /** {left, top, right, bottom} of the whole toolbar: a small pill above the chat's top right corner. */
    private static int[] toolbar() {
        int[] b = box();
        int right = b[2] + 1, top = Math.max(1, b[1] - BUTTON_H - 4);
        int width = EXPAND_W + (isDefault() ? 0 : RESET_W + 1) + 2;
        return new int[]{right - width, top, right, top + BUTTON_H + 2};
    }

    private static int[] expandButton() {
        int[] t = toolbar();
        return new int[]{t[2] - 1 - EXPAND_W, t[1] + 1, t[2] - 1, t[3] - 1};
    }

    private static int[] resetButton() {
        int[] e = expandButton();
        return new int[]{e[0] - 1 - RESET_W, e[1], e[0] - 1, e[3]};
    }

    /** 1 = expand / collapse, 2 = reset, 0 = none. */
    private static int buttonAt(double x, double y) {
        if (inside(expandButton(), x, y)) return 1;
        if (!isDefault() && inside(resetButton(), x, y)) return 2;
        return 0;
    }

    private static boolean inside(int[] r, double x, double y) { return x >= r[0] && x < r[2] && y >= r[1] && y < r[3]; }

    /** The pill with "< >" / "> <" and ↺, drawn pixel by pixel so they stay crisp at any GUI scale. */
    private static void drawToolbar(GuiGraphicsExtractor g, int mx, int my) {
        int[] t = toolbar();
        pill(g, t, PILL);
        int hot = dragging ? 0 : buttonAt(mx, my);
        int accent = accent();
        var font = Minecraft.getInstance().font;

        int[] e = expandButton();
        if (hot == 1) pill(g, e, HOVER_BG);
        int c = hot == 1 ? accent : ICON;
        int x = e[0], y = e[1] + 3;
        for (int i = 0; i < 5; i++) {
            int d = Math.abs(2 - i);             // 2,1,0,1,2: the chevron's point is in the middle row
            int left = expanded ? x + 4 - d : x + 2 + d;      // ">" or "<"
            int right = expanded ? x + 10 + d : x + 12 - d;   // "<" or ">"
            g.fill(left, y + i, left + 1, y + i + 1, c);
            g.fill(right, y + i, right + 1, y + i + 1, c);
        }
        if (hot == 1) g.setTooltipForNextFrame(font, Component.literal(expanded ? L.t("Свернуть") : L.t("Развернуть")), mx, my);

        if (isDefault()) return;
        int[] r = resetButton();
        if (hot == 2) pill(g, r, HOVER_BG);
        c = hot == 2 ? accent : ICON;
        g.fill(r[2], r[1] + 2, r[2] + 1, r[3] - 2, 0x30FFFFFF);   // divider between the two buttons
        int ix = r[0] + 3, iy = r[1] + 2;
        for (int row = 0; row < RESET_ICON.length; row++)
            for (int col = 0; col < RESET_ICON[row].length(); col++)
                if (RESET_ICON[row].charAt(col) == '#') g.fill(ix + col, iy + row, ix + col + 1, iy + row + 1, c);
        if (hot == 2) g.setTooltipForNextFrame(font, Component.literal(L.t("Обычный размер чата")), mx, my);
    }

    /** A filled rectangle with its four corner pixels cut off: a tiny rounded pill. */
    private static void pill(GuiGraphicsExtractor g, int[] r, int color) {
        g.fill(r[0] + 1, r[1], r[2] - 1, r[1] + 1, color);
        g.fill(r[0], r[1] + 1, r[2], r[3] - 1, color);
        g.fill(r[0] + 1, r[3] - 1, r[2] - 1, r[3], color);
    }

    // ---- geometry -----------------------------------------------------------------

    /** {left, top, right, bottom} of the open chat on screen (GUI pixels). */
    private static int[] box() {
        double sc = scale();
        int bottom = gui().getGuiScaledHeight() - 40;
        return new int[]{0, (int) (bottom - currentH() * sc), (int) Math.ceil((currentW() + 8) * sc), bottom};
    }

    /** The open chat's width / height in chat pixels, whichever setting gives it. */
    private static int currentW() {
        int cw = width(true);
        return cw > 0 ? cw : net.minecraft.client.gui.components.ChatComponent.getWidth(Minecraft.getInstance().options.chatWidth().get());
    }

    private static int currentH() {
        int ch = height(true);
        return ch > 0 ? ch : net.minecraft.client.gui.components.ChatComponent.getHeight(Minecraft.getInstance().options.chatHeightFocused().get());
    }

    private static int maxW() { return Math.max(40, (int) ((gui().getGuiScaledWidth() - 14) / scale())); }
    private static int maxH() { return Math.max(20, (int) ((gui().getGuiScaledHeight() - 60) / scale())); }

    private static int accent() { return 0xFF000000 | ChatStyle.GOLD; }
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
