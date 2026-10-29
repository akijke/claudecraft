package dev.claudechat;

import com.mojang.blaze3d.platform.InputConstants;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.input.CharacterEvent;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.client.input.MouseButtonEvent;
import net.minecraft.network.chat.Component;

/**
 * IDE layout (/ide): chat | editor over terminal, in one window, with draggable
 * splitters. The panes are the normal K window, editor and terminal screens drawn
 * into their rectangles; the pane under the mouse gets clicks, the focused pane
 * (thin orange frame) gets the keyboard. Ctrl+P opens a file in the editor pane,
 * Ctrl+1/2/3 focus chat/editor/terminal, Esc closes the layout.
 */
final class IdeScreen extends Screen {
    private static float splitX = 0.38f, splitY = 0.62f;
    private static String lastFile;

    private final ClaudeChat mod;
    private Screen chat, editor, terminal;
    private int focus = 0;  // 0 chat, 1 editor, 2 terminal
    private int dragging = -1;  // 0 vertical splitter, 1 horizontal

    IdeScreen(ClaudeChat mod, String file) {
        super(Component.literal("IDE"));
        this.mod = mod;
        if (file != null) lastFile = file;
    }

    /** Opens `path` in the editor pane (from Ctrl+P or /ide <file>). */
    void openFile(String path) {
        lastFile = path;
        if (editor != null) editor.removed();
        editor = new EditorScreen(mod, this, path);
        layout();
        focus = 1;
    }

    private int[] rect(int pane) {
        int sx = Math.round(width * splitX), sy = Math.round(height * splitY);
        return switch (pane) {
            case 0 -> new int[]{0, 0, sx - 2, height};
            case 1 -> new int[]{sx + 2, 0, width - sx - 2, sy - 2};
            default -> new int[]{sx + 2, sy + 2, width - sx - 2, height - sy - 2};
        };
    }

    private Screen pane(int i) { return i == 0 ? chat : i == 1 ? editor : terminal; }

    private void layout() {
        for (int i = 0; i < 3; i++) {
            Screen s = pane(i);
            if (s == null) continue;
            int[] r = rect(i);
            s.init(Math.max(60, r[2]), Math.max(40, r[3]));
        }
    }

    @Override protected void init() {
        if (chat == null) {
            chat = new ConversationScreen(mod);
            chat.added();
            terminal = TerminalScreen.reopen(mod, this);
            editor = lastFile != null ? new EditorScreen(mod, this, lastFile) : new EditorScreen(mod, this, "README.md");
        } else chat.added();  // back from the palette: the K pane is visible again
        layout();
    }

    @Override public void removed() {
        for (int i = 0; i < 3; i++) if (pane(i) != null) pane(i).removed();
    }

    @Override public boolean isPauseScreen() { return false; }

    /** Live test: type into the terminal pane. */
    void typeForTest(String text) { if (terminal instanceof TerminalScreen t) t.typeForTest(text); }

    @Override public void tick() {
        for (int i = 0; i < 3; i++) if (pane(i) != null) pane(i).tick();
    }

    private int paneAt(double x, double y) {
        for (int i = 0; i < 3; i++) {
            int[] r = rect(i);
            if (x >= r[0] && x < r[0] + r[2] && y >= r[1] && y < r[1] + r[3]) return i;
        }
        return -1;
    }

    private static MouseButtonEvent shift(MouseButtonEvent e, int[] r) {
        return new MouseButtonEvent(e.x() - r[0], e.y() - r[1], e.buttonInfo());
    }

    // ---- input: route to panes -------------------------------------------------
    @Override public boolean keyPressed(KeyEvent event) {
        boolean ctrl = (event.modifiers() & 2) != 0, shift = (event.modifiers() & 1) != 0;
        if (event.key() == InputConstants.KEY_ESCAPE && !(focus == 2 && shift)) { onClose(); return true; }
        if (ctrl && event.key() >= InputConstants.KEY_1 && event.key() <= InputConstants.KEY_3) {
            focus = event.key() - InputConstants.KEY_1;
            return true;
        }
        if (ctrl && !shift && event.key() == InputConstants.KEY_P && focus != 2) {
            PaletteScreen palette = new PaletteScreen(mod, this, true);
            palette.onFile(this::openFile);
            minecraft.setScreenAndShow(palette);
            return true;
        }
        if (ctrl && shift && event.key() == InputConstants.KEY_P) {
            minecraft.setScreenAndShow(new PaletteScreen(mod, this, false));
            return true;
        }
        Screen s = pane(focus);
        return s != null && s.keyPressed(event);
    }

    @Override public boolean charTyped(CharacterEvent event) {
        Screen s = pane(focus);
        return s != null && s.charTyped(event);
    }

    @Override public boolean mouseClicked(MouseButtonEvent event, boolean doubleClick) {
        int sx = Math.round(width * splitX), sy = Math.round(height * splitY);
        if (Math.abs(event.x() - sx) <= 3) { dragging = 0; return true; }
        if (event.x() > sx && Math.abs(event.y() - sy) <= 3) { dragging = 1; return true; }
        int p = paneAt(event.x(), event.y());
        if (p < 0) return false;
        focus = p;
        return pane(p).mouseClicked(shift(event, rect(p)), doubleClick);
    }

    @Override public boolean mouseDragged(MouseButtonEvent event, double dx, double dy) {
        if (dragging == 0) { splitX = Math.max(0.2f, Math.min(0.75f, (float) (event.x() / width))); layout(); return true; }
        if (dragging == 1) { splitY = Math.max(0.25f, Math.min(0.85f, (float) (event.y() / height))); layout(); return true; }
        return pane(focus).mouseDragged(shift(event, rect(focus)), dx, dy);
    }

    @Override public boolean mouseReleased(MouseButtonEvent event) {
        if (dragging >= 0) { dragging = -1; return true; }
        return pane(focus).mouseReleased(shift(event, rect(focus)));
    }

    @Override public boolean mouseScrolled(double x, double y, double h, double v) {
        int p = paneAt(x, y);
        if (p < 0) return false;
        int[] r = rect(p);
        return pane(p).mouseScrolled(x - r[0], y - r[1], h, v);
    }

    // ---- render -----------------------------------------------------------------
    @Override public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float delta) {
        g.fill(0, 0, width, height, 0xFF101014);
        for (int i = 0; i < 3; i++) {
            Screen s = pane(i);
            if (s == null) continue;
            int[] r = rect(i);
            g.pose().pushMatrix();
            g.pose().translate(r[0], r[1]);
            g.enableScissor(0, 0, r[2], r[3]);
            s.extractRenderState(g, mouseX - r[0], mouseY - r[1], delta);
            g.disableScissor();
            g.pose().popMatrix();
        }
        int sx = Math.round(width * splitX), sy = Math.round(height * splitY);
        boolean overV = dragging < 0 && Math.abs(mouseX - sx) <= 3, overH = dragging < 0 && !overV && mouseX > sx && Math.abs(mouseY - sy) <= 3;
        g.fill(sx - 1, 0, sx + 1, height, dragging == 0 ? Theme.ACCENT : overV ? 0xFF5C5C66 : 0xFF2A2A30);
        g.fill(sx + 1, sy - 1, width, sy + 1, dragging == 1 ? Theme.ACCENT : overH ? 0xFF5C5C66 : 0xFF2A2A30);
        int[] f = rect(focus);
        int c = (Theme.ACCENT & 0x00FFFFFF) | 0x90000000;
        g.fill(f[0], f[1], f[0] + f[2], f[1] + 1, c);
        g.fill(f[0], f[1] + f[3] - 1, f[0] + f[2], f[1] + f[3], c);
        g.fill(f[0], f[1], f[0] + 1, f[1] + f[3], c);
        g.fill(f[0] + f[2] - 1, f[1], f[0] + f[2], f[1] + f[3], c);
    }
}
