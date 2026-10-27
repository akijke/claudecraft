package dev.codexcraft;

import net.minecraft.client.gui.GuiGraphicsExtractor;

/** Codexcraft palette; component colours are RGB, GUI fills/text are ARGB. */
final class CodexTheme {
    static final int GPT = 0x53E0BA, CODEX = 0x64B5FF;
    static final int TEXT = 0xE0EEF5, DIM = 0x92AFBF, FAINT = 0x607E91;
    static final int SUCCESS = 0x7CE1B7, ERROR = 0xFF7D8A, WARNING = 0xF3CC78;
    static final int LINK = 0x70D9EF, CODE = 0xA5D9ED, BLOCK = 0xC2E8FF;
    static final int BACKDROP = 0xE508141E, PANEL = 0xEA0D2231, BORDER = 0xFF234658;

    private CodexTheme() { }

    static int opaque(int rgb) { return 0xFF000000 | rgb; }

    static void panel(GuiGraphicsExtractor g, int x, int y, int w, int h, int accent) {
        g.fill(x, y, x + w, y + h, BORDER);
        g.fill(x + 1, y + 1, x + w - 1, y + h - 1, PANEL);
        g.fill(x, y, x + w, y + 1, opaque(accent));
    }
}
