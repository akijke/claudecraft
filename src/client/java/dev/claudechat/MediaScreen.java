package dev.claudechat;

import com.mojang.blaze3d.platform.InputConstants;
import com.mojang.blaze3d.platform.NativeImage;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.Tooltip;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.client.input.MouseButtonEvent;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.Identifier;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/**
 * Full-screen viewer for a generated picture or video (/cc media view <id>, a click on a thumbnail).
 * Wheel — zoom at the cursor, drag — pan, double click / 0 — fit, ←/→ — previous/next in the gallery.
 * Video: the bridge turns the mp4 into a silent JPG frame sequence with ffmpeg; it plays here as a
 * texture that is re-uploaded per frame (no audio ever). Without ffmpeg — the poster and "open in player".
 */
final class MediaScreen extends Screen {
    private final ClaudeChat mod;
    private final Screen parent;
    private final List<MediaStore.Entry> items;
    private final String mode;
    private int index;
    private double zoom = 1, panX, panY;
    private long lastClick;
    private int areaX, areaY, areaW, areaH;
    // video
    private MediaStore.Frames frames;
    private MediaStore.SmoothTexture frameTexture;
    private Identifier frameId;
    private int frameW, frameH, shownFrame = -1;
    private volatile NativeImage nextImage;
    private volatile int nextIndex = -1;
    private volatile boolean decoding;
    private boolean playing = true, asked;
    private long playStart, nextFramesCheck;
    private double playOffset;
    private static int frameCounter;

    MediaScreen(ClaudeChat mod, Screen parent, List<MediaStore.Entry> items, int index, String mode) {
        super(Component.literal(L.t("Просмотр")));
        this.mod = mod;
        this.parent = parent;
        this.items = items;
        this.index = Math.max(0, Math.min(index, items.size() - 1));
        this.mode = mode == null ? "cc" : mode;
    }

    private MediaStore.Entry current() { return items.isEmpty() ? null : items.get(index); }

    @Override public boolean isPauseScreen() { return false; }
    @Override public void onClose() { closeVideo(); minecraft.setScreenAndShow(parent); }
    @Override public void removed() { closeVideo(); }

    @Override protected void init() {
        areaX = 8; areaY = 22; areaW = width - 16; areaH = height - 22 - 30;
        MediaStore.Entry e = current();
        int y = height - 25, x = 8;
        addRenderableWidget(Button.builder(Component.literal("◀"), b -> step(-1)).bounds(x, y, 20, 20)
            .tooltip(Tooltip.create(Component.literal(L.t("Предыдущий (←)")))).build());
        addRenderableWidget(Button.builder(Component.literal("▶"), b -> step(1)).bounds(x + 22, y, 20, 20)
            .tooltip(Tooltip.create(Component.literal(L.t("Следующий (→)")))).build());
        x += 48;
        if (e != null) {
            x = button(x, y, L.t("Открыть на ПК"), L.t("Программой по умолчанию (видео — в плеере)"), () -> bridge("open"));
            x = button(x, y, L.t("Папка"), L.t("Показать файл в проводнике"), () -> bridge("folder"));
            x = button(x, y, L.t("Копировать путь"), null, () -> {
                minecraft.keyboardHandler.setClipboard(e.path().toString());
                mod.notice = L.t("Путь скопирован");
            });
            Path still = e.still();
            if (still != null) x = button(x, y, L.t("Отправить в чат"), L.t("Приложить к следующему сообщению в окне K"), () -> {
                ConversationScreen.prefill(mode, "[[image:" + still + "]]\n");
                minecraft.setScreenAndShow(new ConversationScreen(mod));
            });
            if (e.video()) {
                if (MediaStore.frames(e) != null || frames != null)
                    x = button(x, y, playing ? L.t("Пауза") : L.t("Играть"), L.t("Пробел; видео без звука"), this::togglePlay);
                else x = button(x, y, L.t("Смотреть в игре"), L.t("Кадры через ffmpeg (без звука)"), () -> { asked = true; bridge("frames"); });
            }
        }
        button(x, y, L.t("Галерея"), null, () -> minecraft.setScreenAndShow(new MediaGalleryScreen(mod, parent, mode)));
        addRenderableWidget(Button.builder(Component.literal("✖"), b -> onClose()).bounds(width - 28, y, 20, 20).build());
    }

    private int button(int x, int y, String label, String tip, Runnable action) {
        int w = font.width(label) + 12;
        if (x + w > width - 32) return x;  // narrow window: drop what does not fit
        Button.Builder b = Button.builder(Component.literal(label), btn -> action.run()).bounds(x, y, w, 20);
        if (tip != null) b.tooltip(Tooltip.create(Component.literal(tip)));
        addRenderableWidget(b.build());
        return x + w + 2;
    }

    private void bridge(String sub) {
        MediaStore.Entry e = current();
        if (e == null) return;
        if (e.id().isEmpty()) { mod.notice = L.t("Файл не из галереи моста"); return; }
        mod.send(mode, mod.command(mode, "media", sub + " " + e.id()));
        if (!sub.equals("frames")) mod.notice = L.t("Открываю на ПК…");
    }

    private void step(int d) {
        if (items.size() < 2) return;
        closeVideo();
        index = (index + d + items.size()) % items.size();
        zoom = 1; panX = panY = 0; asked = false; playing = true;
        rebuildWidgets();
    }

    private void togglePlay() {
        if (playing) playOffset = position();
        else playStart = System.nanoTime();
        playing = !playing;
        rebuildWidgets();
    }

    private double position() { return playOffset + (playing ? (System.nanoTime() - playStart) / 1e9 : 0); }

    // ---- video frames --------------------------------------------------------
    private void closeVideo() {
        if (frameId != null) minecraft.getTextureManager().release(frameId);
        frameId = null; frameTexture = null; frames = null; shownFrame = -1;
        NativeImage pending = nextImage;
        nextImage = null;
        if (pending != null) pending.close();
    }

    private void tickVideo() {
        MediaStore.Entry e = current();
        if (e == null || !e.video()) return;
        if (frames == null) {
            long now = System.currentTimeMillis();
            if (now < nextFramesCheck) return;
            nextFramesCheck = now + 1000;
            frames = MediaStore.frames(e);
            if (frames == null) return;
            playStart = System.nanoTime(); playOffset = 0;
            rebuildWidgets();
        }
        int want = (int) (position() * frames.fps()) % frames.count();
        NativeImage ready = nextImage;
        if (ready != null && nextIndex >= 0) {
            nextImage = null;
            if (frameTexture == null) {
                frameW = ready.getWidth(); frameH = ready.getHeight();
                frameTexture = new MediaStore.SmoothTexture(ready);
                frameId = Identifier.fromNamespaceAndPath("claude_chat", "media/frame" + (frameCounter++));
                minecraft.getTextureManager().register(frameId, frameTexture);
            } else if (ready.getWidth() == frameW && ready.getHeight() == frameH) {
                frameTexture.setPixels(ready);
                frameTexture.upload();
            } else ready.close();
            shownFrame = nextIndex;
        }
        if (!decoding && want != shownFrame) {
            decoding = true;
            MediaStore.Frames f = frames;
            int target = want;
            MediaStore.decoder().execute(() -> {
                try {
                    NativeImage img = MediaStore.decode(f.frame(target), 1280);
                    minecraft.execute(() -> {
                        if (frames == f) { NativeImage old = nextImage; nextImage = img; nextIndex = target; if (old != null) old.close(); }
                        else img.close();
                    });
                } catch (Exception ignored) { }
                finally { decoding = false; }
            });
        }
    }

    // ---- input ---------------------------------------------------------------
    @Override public boolean keyPressed(KeyEvent event) {
        switch (event.key()) {
            case InputConstants.KEY_LEFT -> { step(-1); return true; }
            case InputConstants.KEY_RIGHT -> { step(1); return true; }
            case InputConstants.KEY_SPACE -> { if (frames != null) togglePlay(); return true; }
            case InputConstants.KEY_0, InputConstants.KEY_NUMPAD0 -> { zoom = 1; panX = panY = 0; return true; }
            case InputConstants.KEY_EQUALS, InputConstants.KEY_ADD -> { zoomAt(areaX + areaW / 2.0, areaY + areaH / 2.0, 1.25); return true; }
            case InputConstants.KEY_MINUS -> { zoomAt(areaX + areaW / 2.0, areaY + areaH / 2.0, 0.8); return true; }
            default -> { return super.keyPressed(event); }
        }
    }

    @Override public boolean mouseScrolled(double x, double y, double horizontal, double vertical) {
        if (inArea(x, y)) { zoomAt(x, y, vertical > 0 ? 1.25 : 0.8); return true; }
        return super.mouseScrolled(x, y, horizontal, vertical);
    }

    private void zoomAt(double x, double y, double factor) {
        double next = Math.max(0.25, Math.min(16, zoom * factor));
        double f = next / zoom;
        double cx = areaX + areaW / 2.0 + panX, cy = areaY + areaH / 2.0 + panY;  // image centre on screen
        panX += (x - cx) * (1 - f);
        panY += (y - cy) * (1 - f);
        zoom = next;
    }

    @Override public boolean mouseClicked(MouseButtonEvent event, boolean doubleClick) {
        if (super.mouseClicked(event, doubleClick)) return true;
        if (inArea(event.x(), event.y()) && event.button() == 0) {
            long now = System.currentTimeMillis();
            if (doubleClick || now - lastClick < 300) { zoom = 1; panX = panY = 0; }
            lastClick = now;
            return true;
        }
        return false;
    }

    @Override public boolean mouseDragged(MouseButtonEvent event, double dx, double dy) {
        if (inArea(event.x(), event.y())) { panX += dx; panY += dy; return true; }
        return super.mouseDragged(event, dx, dy);
    }

    private boolean inArea(double x, double y) { return x >= areaX && x < areaX + areaW && y >= areaY && y < areaY + areaH; }

    // ---- frame -----------------------------------------------------------------
    @Override public void tick() { tickVideo(); }

    @Override public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float delta) {
        tickVideo();
        g.fill(0, 0, width, height, 0xF00C0C10);
        g.fill(areaX, areaY, areaX + areaW, areaY + areaH, 0xFF060608);
        super.extractRenderState(g, mouseX, mouseY, delta);
        MediaStore.Entry e = current();
        if (e == null) {
            g.centeredText(font, L.t("Галерея пуста: /cc draw <описание> или /cc video <описание>"), width / 2, height / 2, 0xFF8A8A8A);
            return;
        }
        String head = (e.video() ? "▶ " : "◎ ") + e.label();
        String right = (index + 1) + "/" + items.size() + (e.provider().isEmpty() ? "" : "  ·  " + e.provider());
        g.text(font, font.plainSubstrByWidth(head, width - font.width(right) - 30), 8, 8, 0xFFE8E8E8);
        g.text(font, right, width - 8 - font.width(right), 8, 0xFF8A8A8A);

        Identifier id = null;
        int w = 0, h = 0;
        if (frameId != null) { id = frameId; w = frameW; h = frameH; }
        else {
            MediaStore.Tex tex = MediaStore.texture(e.still(), 2048);
            if (tex != null) { id = tex.id(); w = tex.width(); h = tex.height(); }
        }
        if (id == null) {
            String msg;
            if (!Files.isRegularFile(e.path())) msg = L.t("Файл не найден: ") + e.path();
            else if (e.video() && asked) msg = L.t("Готовлю кадры видео (ffmpeg)…");
            else if (e.video() && e.still() == null) msg = L.t("Видео: нажми «Смотреть в игре» (нужен ffmpeg) или «Открыть на ПК»");
            else if (e.still() != null && e.still().toString().toLowerCase().endsWith(".webp")) msg = L.t("WebP не показывается в игре — открой на ПК");
            else msg = L.t("Загрузка…");
            g.centeredText(font, font.plainSubstrByWidth(msg, areaW - 10), width / 2, areaY + areaH / 2, 0xFF8A8A8A);
            return;
        }
        double fit = Math.min(areaW / (double) w, areaH / (double) h);
        double scale = fit * zoom;
        double drawW = w * scale, drawH = h * scale;
        double x0 = areaX + areaW / 2.0 + panX - drawW / 2, y0 = areaY + areaH / 2.0 + panY - drawH / 2;
        g.enableScissor(areaX, areaY, areaX + areaW, areaY + areaH);
        g.pose().pushMatrix();
        g.pose().translate((float) x0, (float) y0);
        g.pose().scale((float) scale, (float) scale);
        g.blit(id, 0, 0, w, h, 0f, 1f, 0f, 1f);
        g.pose().popMatrix();
        g.disableScissor();
        String info = String.format(java.util.Locale.ROOT, "%d×%d  %.0f%%", w, h, scale * 100);
        if (frames != null) info += "  " + L.t("кадр ") + (shownFrame + 1) + "/" + frames.count() + (playing ? "" : L.t("  пауза"));
        g.text(font, info, areaX + 4, areaY + areaH - 11, 0xC0A0A0A0);
        String hint = L.t("колесо — масштаб · тяни — двигать · двойной клик — по размеру · ←/→");
        if (font.width(hint) + font.width(info) + 20 < areaW)
            g.text(font, hint, areaX + areaW - font.width(hint) - 4, areaY + areaH - 11, 0x80A0A0A0);
    }
}
