package dev.claudechat;

import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.Screen;

/**
 * Themes and the window scale of the mod's screens (/claude theme dark|light|contrast,
 * /claude scale auto|1..4). Stored in client.json.
 *
 * The chat palette (ChatStyle colours) and the colours of the full-screen windows
 * (editor, terminal, palette) switch together. Chat lines keep light text in every
 * theme because Minecraft draws chat on a dark background; "light" changes the
 * windows, "contrast" makes everything brighter. The scale is a GUI scale used only
 * while one of the mod's windows is open; the game's own scale comes back after.
 */
final class Theme {
    static final String[] NAMES = {"dark", "light", "contrast"};
    // window colours (ARGB)
    static int SCREEN_BG = 0xE0151518, PANEL = 0xFF1E1E22, GUTTER = 0xFF19191D, CURRENT_LINE = 0xFF26262C,
        LINE_NUMBER = 0xFF5A5A5A, LINE_NUMBER_ON = 0xFFC6C6C6, CARET = 0xFFFFFFFF, SELECTION = 0xFF264F78,
        TERMINAL_BG = 0xF00C0C0C, TERMINAL_FG = 0xCCCCCC, PALETTE_BG = 0xF01E1E22, PALETTE_SELECTED = 0xFF2D3A4F,
        WINDOW_TEXT = 0xFFE8E8E8, WINDOW_DIM = 0xFF6A6A6A;
    private static String current = "dark";
    private static int savedScale = -1;

    private Theme() { }

    static String current() { return current; }

    static void load() {
        ClaudeChat mod = ClaudeChat.instance();
        if (mod == null || !mod.launcher.settings.has("theme")) return;
        apply(mod.launcher.settings.get("theme").getAsString());
    }

    static void set(String name) {
        apply(name);
        ClaudeChat mod = ClaudeChat.instance();
        mod.launcher.settings.addProperty("theme", current);
        mod.launcher.saveAll();
    }

    static void apply(String name) {
        current = java.util.List.of(NAMES).contains(name) ? name : "dark";
        // chat palette
        boolean contrast = current.equals("contrast");
        ChatStyle.TEXT = contrast ? 0xFFFFFF : 0xE8E8E8;
        ChatStyle.DIM = contrast ? 0xD0D0D0 : 0x8A8A8A;
        ChatStyle.FAINT = contrast ? 0xB0B0B0 : 0x5E5E5E;
        ChatStyle.GREEN = contrast ? 0x7CFF9E : 0x6BD08B;
        ChatStyle.RED = contrast ? 0xFF6B6B : 0xF0626A;
        ChatStyle.GOLD = contrast ? 0xFFD84D : 0xF2C14E;
        ChatStyle.AQUA = contrast ? 0x6FE3FF : 0x5CC8E6;
        // windows and syntax colours
        if (current.equals("light")) {
            SCREEN_BG = 0xF0F3F3F3; PANEL = 0xFFFFFFFF; GUTTER = 0xFFEDEDED; CURRENT_LINE = 0xFFF0F4FA;
            LINE_NUMBER = 0xFF9A9A9A; LINE_NUMBER_ON = 0xFF303030; CARET = 0xFF000000; SELECTION = 0xFFB4D5FE;
            TERMINAL_BG = 0xF8FAFAFA; TERMINAL_FG = 0x202020; PALETTE_BG = 0xF8F5F5F5; PALETTE_SELECTED = 0xFFD6E4FA;
            WINDOW_TEXT = 0xFF202020; WINDOW_DIM = 0xFF707070;
            Highlighter.palette(0x24292F, 0xCF222E, 0x0A3069, 0x0550AE, 0x6E7781, 0x8250DF, 0x953800, 0x57606A, 0x0550AE, 0x9A6700);
        } else {
            SCREEN_BG = contrast ? 0xF0000000 : 0xE0151518; PANEL = contrast ? 0xFF000000 : 0xFF1E1E22;
            GUTTER = contrast ? 0xFF0A0A0A : 0xFF19191D; CURRENT_LINE = contrast ? 0xFF202430 : 0xFF26262C;
            LINE_NUMBER = contrast ? 0xFF9A9A9A : 0xFF5A5A5A; LINE_NUMBER_ON = 0xFFFFFFFF; CARET = 0xFFFFFFFF;
            SELECTION = contrast ? 0xFF1F5FA0 : 0xFF264F78; TERMINAL_BG = contrast ? 0xFF000000 : 0xF00C0C0C;
            TERMINAL_FG = contrast ? 0xFFFFFF : 0xCCCCCC; PALETTE_BG = contrast ? 0xF8000000 : 0xF01E1E22;
            PALETTE_SELECTED = contrast ? 0xFF1F5FA0 : 0xFF2D3A4F; WINDOW_TEXT = contrast ? 0xFFFFFFFF : 0xFFE8E8E8;
            WINDOW_DIM = contrast ? 0xFFB0B0B0 : 0xFF6A6A6A;
            if (contrast) Highlighter.palette(0xFFFFFF, 0xFF7AF2, 0xFFB38A, 0xC8FFB0, 0x8FD17A, 0x5CF5DA, 0xFFFF8A, 0xC8C8C8, 0xA8E4FF, 0xFFD84D);
            else Highlighter.palette(0xD4D4D4, 0xC586C0, 0xCE9178, 0xB5CEA8, 0x6A9955, 0x4EC9B0, 0xDCDCAA, 0xA0A0A0, 0x9CDCFE, 0xF2C14E);
        }
        ConversationScreen.changed();
    }

    // ---- window scale ---------------------------------------------------------
    static int scaleSetting() {
        ClaudeChat mod = ClaudeChat.instance();
        return mod != null && mod.launcher.settings.has("uiScale") ? mod.launcher.settings.get("uiScale").getAsInt() : 0;
    }

    static void setScale(int scale) {
        ClaudeChat mod = ClaudeChat.instance();
        mod.launcher.settings.addProperty("uiScale", Math.max(0, Math.min(6, scale)));
        mod.launcher.saveAll();
    }

    private static boolean ours(Screen s) {
        return s instanceof ConversationScreen || s instanceof EditorScreen || s instanceof TerminalScreen
            || s instanceof FileScreen || s instanceof PaletteScreen || s instanceof IdeScreen;
    }

    /** Called every tick: our scale while our window is open, the game's scale otherwise. */
    static void tick(Minecraft client) {
        int want = scaleSetting();
        Screen screen = client.gui.screen();
        var window = client.getWindow();
        if (want > 0 && ours(screen)) {
            int scale = window.calculateScale(want, client.isEnforceUnicode());
            if (window.getGuiScale() != scale) {
                if (savedScale < 0) savedScale = window.getGuiScale();
                window.setGuiScale(scale);
                screen.resize(window.getGuiScaledWidth(), window.getGuiScaledHeight());
            }
        } else if (savedScale >= 0 && !ours(screen)) {
            window.setGuiScale(savedScale);
            savedScale = -1;
            if (screen != null) screen.resize(window.getGuiScaledWidth(), window.getGuiScaledHeight());
        }
    }
}
