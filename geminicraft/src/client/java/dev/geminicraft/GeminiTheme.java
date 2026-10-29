package dev.geminicraft;

import net.minecraft.client.gui.GuiGraphicsExtractor;

/** Geminicraft palette (Google blue / violet); component colours are RGB, GUI fills/text are ARGB. */
final class GeminiTheme {
    // GPT / CODEX keep the names used by the shared build replacements: chat accent and code accent.
    static final int GPT = 0x8AB4F8, CODEX = 0xC58AF9;
    static final int TEXT = 0xE8EAED, DIM = 0x9AA0A6, FAINT = 0x5F6368;
    static final int SUCCESS = 0x81C995, ERROR = 0xF28B82, WARNING = 0xFDD663;
    static final int LINK = 0x78D9EC, CODE = 0xAECBFA, BLOCK = 0xD2E3FC;
    static final int BACKDROP = 0xE5101422, PANEL = 0xEA161C2E, BORDER = 0xFF2A3554;

    private GeminiTheme() { }

    static int opaque(int rgb) { return 0xFF000000 | rgb; }

    static void panel(GuiGraphicsExtractor g, int x, int y, int w, int h, int accent) {
        g.fill(x, y, x + w, y + h, BORDER);
        g.fill(x + 1, y + 1, x + w - 1, y + h - 1, PANEL);
        g.fill(x, y, x + w, y + 1, opaque(accent));
    }
}
