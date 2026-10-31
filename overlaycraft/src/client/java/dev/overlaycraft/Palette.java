package dev.overlaycraft;

import net.fabricmc.loader.api.FabricLoader;
import java.lang.reflect.Field;

/**
 * The cards' colours follow Claudecraft's palette: its chat colours (ChatStyle) and the
 * window background of its theme (Theme, /claude theme dark|light|contrast). Read through
 * reflection, so Overlaycraft still runs alone (then it keeps the same defaults, which are
 * Claudecraft's dark theme). Re-read every two seconds: a theme switch shows up by itself.
 */
final class Palette {
    private static final boolean CLAUDECRAFT = FabricLoader.getInstance().isModLoaded("claude_chat");
    private static long next;

    private Palette() { }

    static void tick() {
        if (!CLAUDECRAFT || System.currentTimeMillis() < next) return;
        next = System.currentTimeMillis() + 2000;
        try {
            Class<?> chat = Class.forName("dev.claude" + "chat.ChatStyle");
            Hud.TEXT = 0xFF000000 | rgb(chat, "TEXT", Hud.TEXT);
            Hud.DIM = 0xFF000000 | rgb(chat, "DIM", Hud.DIM);
            Hud.FAINT = 0xFF000000 | rgb(chat, "FAINT", Hud.FAINT);
            Hud.GOLD = 0xFF000000 | rgb(chat, "GOLD", Hud.GOLD);
            Browser.ACCENT = 0xFF000000 | rgb(chat, "CLAUDE", Browser.ACCENT);
            Class<?> theme = Class.forName("dev.claude" + "chat.Theme");
            // Claudecraft keeps chat text light in every theme, so the cards stay dark on "light" too
            Field current = theme.getDeclaredField("current");
            current.setAccessible(true);
            boolean light = "light".equals(current.get(null));
            Hud.CARD = light ? 0x151518 : rgb(theme, "SCREEN_BG", Hud.CARD);
        } catch (ReflectiveOperationException | RuntimeException | LinkageError e) {
            next = Long.MAX_VALUE;  // another Claudecraft version: keep the defaults
        }
    }

    private static java.lang.reflect.Method claims;
    private static boolean noClaims;

    /**
     * True when Claudecraft's chat frame wants the click at x, y (its edges, corner or the
     * "< >" / ↺ toolbar): a card lying over them lets the chat have it.
     */
    static boolean chatClaims(double x, double y) {
        if (!CLAUDECRAFT || noClaims) return false;
        try {
            if (claims == null) claims = Class.forName("dev.claude" + "chat.ChatSize").getMethod("claims", double.class, double.class);
            return (Boolean) claims.invoke(null, x, y);
        } catch (ReflectiveOperationException | RuntimeException | LinkageError e) {
            noClaims = true;  // another Claudecraft version without it
            return false;
        }
    }

    private static int rgb(Class<?> owner, String name, int fallback) throws ReflectiveOperationException {
        Field f = owner.getDeclaredField(name);
        f.setAccessible(true);
        return f.getType() == int.class ? f.getInt(null) & 0xFFFFFF : fallback & 0xFFFFFF;
    }
}
