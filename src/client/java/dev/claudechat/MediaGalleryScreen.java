package dev.claudechat;

import com.mojang.blaze3d.platform.InputConstants;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.components.Tooltip;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.client.input.MouseButtonEvent;
import net.minecraft.network.chat.Component;
import java.util.List;

/**
 * /cc media: a grid of everything generated or made by agents (newest first), and a prompt box
 * to generate a new picture or video. A click opens MediaScreen; the grid scrolls with the wheel.
 */
final class MediaGalleryScreen extends Screen {
    private static final int CELL = 96, LABEL = 11, GAP = 6;
    private final ClaudeChat mod;
    private final Screen parent;
    private final String mode;
    private final boolean preview;
    private EditBox prompt;
    private int scrollRows, cols, gridX, gridY, gridH;
    private long nextRefresh;

    MediaGalleryScreen(ClaudeChat mod, Screen parent, String mode) { this(mod, parent, mode, false); }

    /** Dev screenshots: textures are decoded synchronously. */
    MediaGalleryScreen(ClaudeChat mod, Screen parent, String mode, boolean preview) {
        super(Component.literal(L.t("Галерея")));
        this.mod = mod;
        this.parent = parent;
        this.mode = mode == null ? "cc" : mode;
        this.preview = preview;
    }

    @Override public boolean isPauseScreen() { return false; }
    @Override public void onClose() { minecraft.setScreenAndShow(parent); }

    @Override protected void init() {
        int boxW = Math.max(120, width - 16 - 230);
        String keep = prompt == null ? "" : prompt.getValue();
        prompt = new EditBox(font, 8, 22, boxW, 20, Component.literal("prompt"));
        prompt.setMaxLength(4000);
        prompt.setHint(Component.literal(L.t("Опиши картинку или видео…")));
        prompt.setValue(keep);
        addRenderableWidget(prompt);
        int x = 8 + boxW + 4;
        addRenderableWidget(Button.builder(Component.literal(L.t("Картинка")), b -> generate("draw")).bounds(x, 22, 64, 20)
            .tooltip(Tooltip.create(Component.literal(L.t("OpenAI gpt-image-1 или Gemini, ≈ $0.04 (Enter)")))).build());
        addRenderableWidget(Button.builder(Component.literal(L.t("Видео")), b -> generate("video")).bounds(x + 66, 22, 56, 20)
            .tooltip(Tooltip.create(Component.literal(L.t("Sora или Veo, 4 с, ≈ $0.40–0.60; минуты ожидания")))).build());
        addRenderableWidget(Button.builder(Component.literal("🔑"), b -> mod.runLine(mode, "/cc media key")).bounds(x + 124, 22, 20, 20)
            .tooltip(Tooltip.create(Component.literal(L.t("Ключи OpenAI / Gemini для генерации")))).build());
        addRenderableWidget(Button.builder(Component.literal(L.t("Папка")), b -> mod.runLine(mode, "/cc media folder last")).bounds(x + 146, 22, 52, 20)
            .tooltip(Tooltip.create(Component.literal(L.t("Папка с файлами на ПК")))).build());
        addRenderableWidget(Button.builder(Component.literal("✖"), b -> onClose()).bounds(width - 28, 22, 20, 20).build());
        gridX = 8; gridY = 50; gridH = height - gridY - 6;
        cols = Math.max(1, (width - 16 + GAP) / (CELL + GAP));
        setInitialFocus(prompt);
    }

    private void generate(String action) {
        String text = prompt.getValue().strip();
        if (text.isEmpty()) { mod.notice = L.t("Сначала опиши, что сделать"); return; }
        mod.dispatch(mode, action + " " + text);
        prompt.setValue("");
        mod.notice = L.t("Генерация запущена — результат появится здесь и в чате");
    }

    @Override public boolean keyPressed(KeyEvent event) {
        boolean enter = event.key() == InputConstants.KEY_RETURN || event.key() == InputConstants.KEY_NUMPADENTER;
        if (enter && getFocused() == prompt) { generate((event.modifiers() & 1) != 0 ? "video" : "draw"); return true; }
        return super.keyPressed(event);
    }

    private List<MediaStore.Entry> items() { return MediaStore.list(); }

    @Override public boolean mouseScrolled(double x, double y, double horizontal, double vertical) {
        int rows = (items().size() + cols - 1) / cols, visible = Math.max(1, gridH / (CELL + LABEL + GAP));
        scrollRows = Math.max(0, Math.min(Math.max(0, rows - visible), scrollRows - (int) Math.signum(vertical)));
        return true;
    }

    @Override public boolean mouseClicked(MouseButtonEvent event, boolean doubleClick) {
        if (super.mouseClicked(event, doubleClick)) return true;
        int i = cellAt(event.x(), event.y());
        if (i >= 0) {
            minecraft.setScreenAndShow(new MediaScreen(mod, parent, items(), i, mode));
            return true;
        }
        return false;
    }

    private int cellAt(double x, double y) {
        if (y < gridY || x < gridX) return -1;
        int col = (int) ((x - gridX) / (CELL + GAP)), row = (int) ((y - gridY) / (CELL + LABEL + GAP));
        if (col >= cols || (x - gridX) % (CELL + GAP) > CELL) return -1;
        int i = (row + scrollRows) * cols + col;
        return i < items().size() ? i : -1;
    }

    @Override public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float delta) {
        g.fill(0, 0, width, height, 0xE80E0E12);
        super.extractRenderState(g, mouseX, mouseY, delta);
        List<MediaStore.Entry> items = items();
        g.text(font, Component.literal("◎ " + L.t("Галерея")).withColor(ChatStyle.CLAUDE), 8, 8, 0xFFFFFFFF);
        String count = items.size() + L.t(" файлов · Enter — картинка, Shift+Enter — видео");
        g.text(font, count, width - 8 - font.width(count), 8, 0xFF8A8A8A);
        if (items.isEmpty()) {
            g.centeredText(font, L.t("Пока пусто. Опиши картинку сверху или попроси агента нарисовать — файлы появятся здесь."),
                width / 2, gridY + 40, 0xFF8A8A8A);
            g.centeredText(font, L.t("Нужен ключ OpenAI или Gemini: кнопка 🔑"), width / 2, gridY + 54, 0xFF5E5E5E);
        }
        int hover = cellAt(mouseX, mouseY);
        g.enableScissor(gridX, gridY, width - 8, gridY + gridH);
        for (int i = scrollRows * cols; i < items.size(); i++) {
            int col = i % cols, row = i / cols - scrollRows;
            int x = gridX + col * (CELL + GAP), y = gridY + row * (CELL + LABEL + GAP);
            if (y > gridY + gridH) break;
            MediaStore.Entry e = items.get(i);
            g.fill(x - 1, y - 1, x + CELL + 1, y + CELL + 1, i == hover ? 0xFFD97757 : 0xFF2A2A30);
            g.fill(x, y, x + CELL, y + CELL, 0xFF141418);
            MediaStore.Tex tex = e.still() == null ? null
                : preview ? MediaStore.textureNow(e.still(), 256) : MediaStore.texture(e.still(), 256);
            if (tex != null) {
                double s = Math.min(CELL / (double) tex.width(), CELL / (double) tex.height());
                int w = (int) Math.round(tex.width() * s), h = (int) Math.round(tex.height() * s);
                g.blit(tex.id(), x + (CELL - w) / 2, y + (CELL - h) / 2, x + (CELL - w) / 2 + w, y + (CELL - h) / 2 + h, 0f, 1f, 0f, 1f);
            } else g.centeredText(font, e.video() ? "▶" : "…", x + CELL / 2, y + CELL / 2 - 4, 0xFF5E5E5E);
            if (e.video()) {
                String badge = "▶ " + L.t("видео");
                g.fill(x + 2, y + 2, x + 6 + font.width(badge), y + 13, 0xC0000000);
                g.text(font, badge, x + 4, y + 4, 0xFFF2C14E);
            }
            g.text(font, font.plainSubstrByWidth(e.label(), CELL), x, y + CELL + 2, i == hover ? 0xFFE8E8E8 : 0xFF8A8A8A);
        }
        g.disableScissor();
        if (hover >= 0) {
            MediaStore.Entry e = items.get(hover);
            g.setTooltipForNextFrame(font, font.split(Component.literal(e.label() + "\n" + e.path()
                + (e.provider().isEmpty() ? "" : "\n" + e.provider())), 260), mouseX, mouseY);
        }
        String notice = mod.notice;
        if (!notice.isEmpty() && preview == false)
            g.text(font, font.plainSubstrByWidth("· " + notice, width - 16), 8, height - 10, 0xFF8A8A8A);
    }

    @Override public void tick() {
        long now = System.currentTimeMillis();
        if (now > nextRefresh) { nextRefresh = now + 1000; MediaStore.list(); }
    }
}
