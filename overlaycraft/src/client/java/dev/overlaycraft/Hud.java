package dev.overlaycraft;

import net.fabricmc.fabric.api.client.rendering.v1.hud.HudElementRegistry;
import net.fabricmc.fabric.api.client.screen.v1.ScreenEvents;
import net.fabricmc.fabric.api.client.screen.v1.ScreenMouseEvents;
import net.minecraft.client.DeltaTracker;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.Font;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.screens.ChatScreen;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.resources.Identifier;
import java.util.ArrayList;
import java.util.List;

/**
 * The overlay: translucent cards drawn over the game.
 *
 * Playing: drawn, nothing to click. Chat open (T): the mouse is free, so the cards
 * get buttons, a card drags anywhere by its body, right click folds it to one line,
 * ✖ hides it (/ov show brings it back). Any other screen (inventory, menus): hidden,
 * or with /ov screens behind, left under the screen's blur.
 */
final class Hud {
    static final int W = 176;
    // Claudecraft's palette (dark theme); Palette.tick() follows its current theme when it is loaded
    static int TEXT = 0xFFE8E8E8, DIM = 0xFFA8A8A8, FAINT = 0xFF8C8C8C, GOLD = 0xFFF2C14E;
    /** Card background, RGB: Claudecraft's window background; the alpha comes from /ov opacity. */
    static int CARD = 0x151518;

    private record Hit(int x1, int y1, int x2, int y2, Runnable action) {
        boolean in(double x, double y) { return x >= x1 && x < x2 && y >= y1 && y < y2; }
    }
    private record Card(String name, int x, int y, int w, int h) {
        boolean in(double mx, double my) { return mx >= x && mx < x + w && my >= y && my < y + h; }
    }

    /** What the last frame drew, for clicks (main thread). */
    private static final List<Hit> HITS = new ArrayList<>();
    private static final List<Card> CARDS = new ArrayList<>();
    private static String dragging;
    private static double grabX, grabY;
    static int mouseX = -1, mouseY = -1;

    private Hud() { }

    static void register() {
        HudElementRegistry.addLast(Identifier.fromNamespaceAndPath("overlaycraft", "overlay"), Hud::render);
        ScreenEvents.AFTER_INIT.register((client, screen, sw, sh) -> {
            if (!(screen instanceof ChatScreen)) return;
            ScreenEvents.afterExtract(screen).register((s, g, mx, my, delta) -> {
                mouseX = mx;
                mouseY = my;
                // After a click a mod took, the game marks no button as held and sends no drag
                // events (MouseHandler.onButton returns early), so the card follows the cursor
                // here, every frame, from the press until the release event
                if (dragging != null) drag(mx, my);
            });
            ScreenMouseEvents.allowMouseClick(screen).register((s, e) -> !press(e.x(), e.y(), e.button()));
            ScreenMouseEvents.allowMouseDrag(screen).register((s, e, dx, dy) -> !drag(e.x(), e.y()));
            ScreenMouseEvents.allowMouseRelease(screen).register((s, e) -> !release());
            ScreenEvents.remove(screen).register(s -> { dragging = null; mouseX = mouseY = -1; HITS.clear(); });
        });
    }

    private static void render(GuiGraphicsExtractor g, DeltaTracker delta) {
        Minecraft mc = Minecraft.getInstance();
        if (!Config.enabled || mc.gui.hud.isHidden()) return;
        Screen screen = mc.gui.screen();
        boolean chat = screen instanceof ChatScreen;
        if (screen != null && !chat && !Config.screens.equals("behind")) return;
        draw(g, chat, screen != null && !chat);
    }

    /** Draws every card; with chat = true, also their buttons (and records where they are). */
    static void draw(GuiGraphicsExtractor g, boolean chat) { draw(g, chat, false); }

    /** faded: under another screen (/ov screens behind) — dimmed, so it stays in the background. */
    static void draw(GuiGraphicsExtractor g, boolean chat, boolean faded) {
        Minecraft mc = Minecraft.getInstance();
        int sw = mc.getWindow().getGuiScaledWidth(), sh = mc.getWindow().getGuiScaledHeight();
        HITS.clear();
        CARDS.clear();
        // 1. measure every card and put it where its settings say
        List<Card> wanted = new ArrayList<>();
        for (var entry : Config.WIDGETS.entrySet()) {
            Config.Widget w = entry.getValue();
            Painter p = painter(entry.getKey());
            if (!w.visible || p == null) continue;
            int full = p.paint(null, mc.font, 0, 0, w.compact, chat);  // nothing is drawn while g is null
            if (full <= 0) continue;
            int x = (int) Math.round(w.fx * Math.max(0, sw - W));
            int y = (int) Math.round(w.fy * Math.max(0, sh - full));
            wanted.add(new Card(entry.getKey(), x, Math.max(0, Math.min(y, sh - full)), W, full));
        }
        // 2. no overlaps: the card being dragged stays under the mouse, the others, top first,
        //    move down below a card they would cover (so cards that grow in chat push the next one)
        wanted.sort((a, b) -> a.name().equals(dragging) ? -1 : b.name().equals(dragging) ? 1 : Double.compare(Config.widget(a.name()).fy, Config.widget(b.name()).fy));
        List<Card> placed = new ArrayList<>();
        for (Card c : wanted) {
            int y = c.y();
            for (boolean moved = true; moved; ) {
                moved = false;
                for (Card o : placed)
                    if (c.x() < o.x() + o.w() && o.x() < c.x() + c.w() && y < o.y() + o.h() && o.y() < y + c.h()) {
                        y = o.y() + o.h() + 2;
                        moved = true;
                    }
            }
            placed.add(new Card(c.name(), c.x(), Math.min(y, sh - c.h()), c.w(), c.h()));
        }
        // 3. draw
        for (Card c : placed) {
            String name = c.name();
            Config.Widget w = Config.widget(name);
            int x = c.x(), y = c.y(), full = c.h();
            Painter p = painter(name);
            boolean hot = chat && (name.equals(dragging) || (mouseX >= x && mouseX < x + W && mouseY >= y && mouseY < y + full));
            g.fill(x, y, x + W, y + full, (Math.round(Config.opacity * 2.55f) << 24) | CARD);
            if (hot) outline(g, x, y, W, full, name.equals(dragging) ? GOLD : 0x50FFFFFF);
            p.paint(g, mc.font, x, y, w.compact, chat);
            if (faded) g.fill(x, y, x + W, y + full, 0xA8000000);
            if (chat) {
                CARDS.add(new Card(name, x, y, W, full));
                if (hot && dragging == null) {
                    g.text(mc.font, "✖", x + W - 9, y + 3, mouseX >= x + W - 11 && mouseY < y + 12 ? TEXT : FAINT);
                    HITS.add(new Hit(x + W - 11, y, x + W, y + 12, () -> {
                        w.visible = false;
                        Config.save();
                        Overlaycraft.say("Скрыто. Вернуть: /ov show " + name);
                    }));
                }
            }
        }
    }

    /** Draws a card at x, y (or only measures it when g is null) and returns its height; 0 = nothing to show. */
    private interface Painter { int paint(GuiGraphicsExtractor g, Font font, int x, int y, boolean compact, boolean chat); }

    private static Painter painter(String name) {
        return switch (name) {
            case "music" -> Hud::music;
            case "food" -> Hud::food;
            case "tabs" -> Hud::tabs;
            default -> null;
        };
    }

    /** Right edge for a card's first row: in chat, room is left for the ✖. */
    private static int right(int x, boolean chat) { return x + W - (chat ? 16 : 6); }

    // ---- music ------------------------------------------------------------------

    private static int music(GuiGraphicsExtractor g, Font font, int x, int y, boolean compact, boolean chat) {
        Media.Session s = Media.shown();
        if (s == null) {
            if (!chat) return 0;
            if (g != null) {
                bar(g, x, y, 16, FAINT);
                String text = !Media.supported() ? "Музыка: только Windows" : !Media.error.isEmpty() ? "Музыка: " + Media.error : "Ничего не играет";
                g.text(font, cut(font, text, right(x, chat) - x - 7), x + 7, y + 4, DIM);
            }
            return 16;
        }
        Browser.Source src = Browser.source(s);
        String time = Media.time(s.position()) + (s.dur > 0 ? " / " + Media.time(s.dur) : "");
        if (compact) {
            if (g != null) {
                bar(g, x, y, 16, src.color());
                icon(g, s.playing() ? "play" : "pause", x + 7, y + 4, src.color());
                String line = s.title + (s.artist.isEmpty() ? "" : " — " + s.artist);
                int tw = font.width(time);
                g.text(font, cut(font, line, right(x, chat) - x - 19 - tw), x + 15, y + 4, TEXT);
                g.text(font, time, right(x, chat) - tw, y + 4, DIM);
                if (chat) HITS.add(new Hit(x + 4, y, x + 14, y + 16, () -> Media.control("toggle", s.id)));
            }
            return 16;
        }
        int h = chat ? 48 : 34;
        if (g == null) return h;
        bar(g, x, y, h, src.color());
        int sw = font.width(src.name());
        g.text(font, cut(font, s.title.isEmpty() ? "—" : s.title, right(x, chat) - x - 11 - sw), x + 7, y + 5, TEXT);
        g.text(font, src.name(), right(x, chat) - sw, y + 5, src.color());
        int tw = font.width(time);
        g.text(font, cut(font, s.artist, W - 20 - tw), x + 7, y + 16, DIM);
        g.text(font, time, x + W - 6 - tw, y + 16, DIM);
        // progress
        int x1 = x + 7, x2 = x + W - 6;
        g.fill(x1, y + 28, x2, y + 30, 0x30FFFFFF);
        if (s.dur > 0) g.fill(x1, y + 28, x1 + (int) ((x2 - x1) * Math.min(1, s.position() / s.dur)), y + 30, src.color());
        if (!chat) return h;
        // controls
        int cx = x + W / 2, cy = y + 34;
        button(g, cx - 28, cy, "prev", s.canPrev ? TEXT : FAINT, () -> Media.control("prev", s.id));
        button(g, cx - 7, cy, s.playing() ? "pause" : "play", src.color(), () -> Media.control("toggle", s.id));
        button(g, cx + 14, cy, "next", s.canNext ? TEXT : FAINT, () -> Media.control("next", s.id));
        List<Media.Session> all = Media.sessions;
        if (all.size() > 1) {
            String n = (all.indexOf(s) + 1) + "/" + all.size();
            int nx = x + W - 7 - font.width(n);
            boolean over = mouseX >= nx - 2 && mouseX < x + W - 4 && mouseY >= cy && mouseY < cy + 12;
            g.text(font, n, nx, cy + 2, over ? TEXT : DIM);
            HITS.add(new Hit(nx - 2, cy, x + W - 4, cy + 12, Media::cycle));
        }
        return h;
    }

    // ---- food -------------------------------------------------------------------

    private static final String[] STEPS = {"Принят", "Готовят", "В пути", "Доставлен"};

    private static int food(GuiGraphicsExtractor g, Font font, int x, int y, boolean compact, boolean chat) {
        Browser.Order o = Browser.order;
        if (o == null || !o.active()) {
            if (!chat) return 0;
            if (g != null) {
                bar(g, x, y, 16, FAINT);
                if (Browser.connected() && Browser.edaState.equals("login")) {
                    int bw = font.width("Войти") + 8, bx = right(x, chat) - bw;
                    g.text(font, cut(font, "Еда: войди в браузере", bx - x - 11), x + 7, y + 4, DIM);
                    textButton(g, font, bx, y + 2, "Войти", () -> Browser.open("https://eda.yandex.ru/"));
                } else {
                    String text = !Browser.connected() ? "Еда: подключи браузер" : "Еда: нет активного заказа";
                    g.text(font, cut(font, text, right(x, chat) - x - 7), x + 7, y + 4, DIM);
                }
            }
            return 16;
        }
        int color = o.step < 0 ? 0xFFF0626A : o.step >= 3 ? 0xFF6BD08B : GOLD;
        String status = o.status.isEmpty() ? (o.step >= 0 && o.step < 4 ? STEPS[o.step] : "Отменён") : o.status;
        int left = o.minutesLeft();
        String eta = o.step >= 3 || o.step < 0 || left < 0 ? "" : left == 0 ? "сейчас" : "~" + left + " мин";
        if (compact) {
            if (g != null) {
                bar(g, x, y, 16, color);
                int ew = font.width(eta);
                g.text(font, cut(font, status, right(x, chat) - x - 11 - ew), x + 7, y + 4, TEXT);
                g.text(font, eta, right(x, chat) - ew, y + 4, color);
            }
            return 16;
        }
        int h = chat ? 49 : 36;
        if (g == null) return h;
        bar(g, x, y, h, color);
        int ew = font.width(eta);
        String head = o.service + (o.place.isEmpty() ? "" : " · " + o.place);
        g.text(font, cut(font, head, right(x, chat) - x - 11 - ew), x + 7, y + 5, DIM);
        g.text(font, eta, right(x, chat) - ew, y + 5, color);
        g.text(font, cut(font, status, W - 13), x + 7, y + 16, TEXT);
        // four steps
        int x1 = x + 7, x2 = x + W - 6, gap = 3, seg = (x2 - x1 - gap * 3) / 4;
        for (int i = 0; i < 4; i++) {
            int sx = x1 + i * (seg + gap);
            boolean done = o.step >= 0 && i <= o.step;
            g.fill(sx, y + 29, sx + seg, y + 31, done ? color : 0x30FFFFFF);
        }
        if (!chat) return h;
        int by = y + 35;
        int bx = x + 7;
        if (!o.url.isEmpty()) bx = textButton(g, font, bx, by, "Открыть", () -> Browser.open(o.url)) + 8;
        textButton(g, font, bx, by, "Убрать", () -> Browser.order = null);
        return h;
    }

    // ---- browser tabs -----------------------------------------------------------------

    private static int tabs(GuiGraphicsExtractor g, Font font, int x, int y, boolean compact, boolean chat) {
        if (!Browser.connected()) {
            if (!chat) return 0;
            if (g != null) {
                bar(g, x, y, 16, FAINT);
                int bw = font.width("Подключить") + 8, bx = right(x, chat) - bw;
                g.text(font, cut(font, "Браузер не подключён", bx - x - 11), x + 7, y + 4, DIM);
                textButton(g, font, bx, y + 2, "Подключить", Overlaycraft::connectBrowser);
            }
            return 16;
        }
        List<Browser.Tab> list = shownTabs();
        if (compact || list.isEmpty()) {
            if (g != null) {
                bar(g, x, y, 16, Browser.ACCENT);
                Browser.Tab playing = list.stream().filter(t -> t.audible).findFirst().orElse(null);
                String text = playing != null ? playing.title : "Вкладок: " + Browser.tabs.size();
                if (playing != null) icon(g, "play", x + 7, y + 4, color(playing));
                g.text(font, cut(font, text, right(x, chat) - x - 15), x + (playing != null ? 15 : 7), y + 4, TEXT);
                if (chat && playing != null) HITS.add(new Hit(x, y, x + W - 12, y + 16, () -> Browser.focus(playing.id)));
            }
            return 16;
        }
        int h = 17 + list.size() * 11;
        if (g == null) return h;
        bar(g, x, y, h, Browser.ACCENT);
        g.text(font, "Браузер", x + 7, y + 5, Browser.ACCENT);
        String count = Browser.tabs.size() + " вкл.";
        g.text(font, count, right(x, chat) - font.width(count), y + 5, FAINT);
        int ry = y + 16;
        for (Browser.Tab t : list) {
            int c = color(t);
            boolean over = chat && mouseX >= x && mouseX < x + W && mouseY >= ry - 1 && mouseY < ry + 10;
            if (over) g.fill(x + 2, ry - 1, x + W, ry + 10, 0x18FFFFFF);
            if (t.audible) icon(g, "play", x + 7, ry + 1, c);
            else g.fill(x + 8, ry + 3, x + 11, ry + 6, t.focused && t.active ? c : FAINT);
            String host = Browser.site(t.host()) != null ? Browser.site(t.host()).name() : t.host();
            int hw = Math.min(font.width(host), 44);
            g.text(font, cut(font, t.title.isEmpty() ? t.url : t.title, W - 26 - hw), x + 15, ry + 1, over || t.audible ? TEXT : DIM);
            g.text(font, cut(font, host, hw), x + W - 6 - hw, ry + 1, FAINT);
            if (chat) {
                int id = t.id;
                HITS.add(new Hit(x + 2, ry - 1, x + W, ry + 10, () -> Browser.focus(id)));
            }
            ry += 11;
        }
        return h;
    }

    /** Tabs that play sound first, then the open tab of each window; five at most. */
    private static List<Browser.Tab> shownTabs() {
        List<Browser.Tab> list = new ArrayList<>();
        for (Browser.Tab t : Browser.tabs) if (t.audible) list.add(t);
        for (Browser.Tab t : Browser.tabs) if (t.active && !t.audible) list.add(t);
        return list.size() > 5 ? list.subList(0, 5) : list;
    }

    private static int color(Browser.Tab t) {
        Browser.Source s = Browser.site(t.host());
        return s == null ? Browser.ACCENT : s.color();
    }

    // ---- input (chat open) -----------------------------------------------------------

    static boolean press(double mx, double my, int button) {
        if (!Config.enabled) return false;
        for (int i = CARDS.size() - 1; i >= 0; i--) {
            Card c = CARDS.get(i);
            if (!c.in(mx, my)) continue;
            if (button == 1) {  // right click: fold / unfold
                Config.Widget w = Config.widget(c.name());
                w.compact = !w.compact;
                Config.save();
                return true;
            }
            if (button != 0) return true;
            for (Hit h : HITS) if (h.in(mx, my)) { h.action().run(); return true; }
            dragging = c.name();
            grabX = mx - c.x();
            grabY = my - c.y();
            return true;
        }
        return false;
    }

    static boolean release() {
        if (dragging == null) return false;
        dragging = null;
        Config.save();
        return true;
    }

    /** Where a card was drawn last frame (chat open), for the live test. */
    static int[] cardRect(String name) {
        for (Card c : CARDS) if (c.name().equals(name)) return new int[]{c.x(), c.y(), c.w(), c.h()};
        return null;
    }

    static boolean drag(double mx, double my) {
        if (dragging == null) return false;
        Minecraft mc = Minecraft.getInstance();
        int sw = mc.getWindow().getGuiScaledWidth(), sh = mc.getWindow().getGuiScaledHeight();
        Card card = CARDS.stream().filter(c -> c.name().equals(dragging)).findFirst().orElse(null);
        int h = card == null ? 16 : card.h();
        Config.Widget w = Config.widget(dragging);
        // snap to the screen's edges within 6 pixels
        double nx = mx - grabX, ny = my - grabY;
        if (nx < 6) nx = 0;
        if (nx > sw - W - 6) nx = sw - W;
        if (ny < 6) ny = 0;
        if (ny > sh - h - 6) ny = sh - h;
        w.fx = Config.clamp(nx / Math.max(1, sw - W));
        w.fy = Config.clamp(ny / Math.max(1, sh - h));
        return true;
    }

    // ---- drawing helpers ---------------------------------------------------------------

    private static void bar(GuiGraphicsExtractor g, int x, int y, int h, int color) {
        g.fill(x, y, x + 2, y + h, color);
    }

    private static void outline(GuiGraphicsExtractor g, int x, int y, int w, int h, int c) {
        g.fill(x, y, x + w, y + 1, c);
        g.fill(x, y + h - 1, x + w, y + h, c);
        g.fill(x, y, x + 1, y + h, c);
        g.fill(x + w - 1, y, x + w, y + h, c);
    }

    /** A 14×12 icon button. */
    private static void button(GuiGraphicsExtractor g, int x, int y, String icon, int color, Runnable action) {
        boolean over = mouseX >= x && mouseX < x + 14 && mouseY >= y && mouseY < y + 12;
        if (over) g.fill(x, y, x + 14, y + 12, 0x28FFFFFF);
        icon(g, icon, x + 4, y + 2, over ? TEXT : color);
        HITS.add(new Hit(x, y, x + 14, y + 12, action));
    }

    /** A text button; returns its right edge. */
    private static int textButton(GuiGraphicsExtractor g, Font font, int x, int y, String label, Runnable action) {
        int w = font.width(label) + 8;
        boolean over = mouseX >= x && mouseX < x + w && mouseY >= y && mouseY < y + 11;
        g.fill(x, y, x + w, y + 11, over ? 0x38FFFFFF : 0x18FFFFFF);
        g.text(font, label, x + 4, y + 2, over ? TEXT : DIM);
        HITS.add(new Hit(x, y, x + w, y + 11, action));
        return x + w;
    }

    /** Pixel icons, 7 px tall: play, pause, next, prev. */
    static void icon(GuiGraphicsExtractor g, String kind, int x, int y, int c) {
        switch (kind) {
            case "play" -> triangle(g, x, y, c, true);
            case "pause" -> { g.fill(x, y, x + 2, y + 7, c); g.fill(x + 4, y, x + 6, y + 7, c); }
            case "next" -> { triangle(g, x, y, c, true); g.fill(x + 4, y, x + 6, y + 7, c); }
            case "prev" -> { g.fill(x, y, x + 2, y + 7, c); triangle(g, x + 2, y, c, false); }
            default -> { }
        }
    }

    private static void triangle(GuiGraphicsExtractor g, int x, int y, int c, boolean right) {
        for (int r = 0; r < 7; r++) {
            int len = 4 - Math.abs(r - 3);
            if (right) g.fill(x, y + r, x + len, y + r + 1, c);
            else g.fill(x + 4 - len, y + r, x + 4, y + r + 1, c);
        }
    }

    static String cut(Font font, String s, int room) {
        if (s == null) return "";
        if (room <= 6) return "";
        if (font.width(s) <= room) return s;
        return font.plainSubstrByWidth(s, room - font.width("…")) + "…";
    }
}
