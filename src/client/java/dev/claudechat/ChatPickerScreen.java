package dev.claudechat;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.mojang.blaze3d.platform.InputConstants;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.components.Tooltip;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.client.input.MouseButtonEvent;
import net.minecraft.network.chat.Component;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.CompletableFuture;

/**
 * /c chats, /cc chats: a window to switch chats. Two tabs (Claude / Claude Code),
 * search by title and last message, ↑/↓ + Enter, a click, or Tab for the other tab.
 * Each row: title, how long ago, last line of the chat, model, cost and whether
 * Claude is working on it. `/c chats list` still prints the old list into the chat.
 */
final class ChatPickerScreen extends Screen {
    private static final int ROW = 26, TOP = 64;
    private final ClaudeChat mod;
    private final Screen parent;
    private String mode;
    private EditBox query;
    private JsonObject state;
    private final List<JsonObject> shown = new ArrayList<>();
    private int selected, scroll, hover = -1;
    private String status = "";
    private long nextFetch;
    private boolean fixed;  // DevShots: sample data, no bridge

    static ChatPickerScreen preview(ClaudeChat mod, JsonObject sample) {
        ChatPickerScreen s = new ChatPickerScreen(mod, null, "cc");
        s.state = sample;
        s.fixed = true;
        return s;
    }

    ChatPickerScreen(ClaudeChat mod, Screen parent, String mode) {
        super(Component.literal("Chats"));
        this.mod = mod;
        this.parent = parent;
        this.mode = mode.equals("c") ? "c" : "cc";
    }

    // ---- data ---------------------------------------------------------------
    private void fetch() {
        if (fixed) return;
        nextFetch = System.currentTimeMillis() + 2000;
        CompletableFuture.supplyAsync(() -> {
            try { return mod.request("/state", null); } catch (Exception e) { return null; }
        }, mod.io).thenAccept(r -> minecraft.execute(() -> {
            if (r == null) { status = L.t("Мост недоступен — /claude"); return; }
            String keep = selectedId();
            state = r;
            status = "";
            filter(keep);
        }));
    }

    private String selectedId() {
        return selected >= 0 && selected < shown.size() ? shown.get(selected).get("id").getAsString() : "";
    }

    private String current() {
        if (state == null || !state.has("selected")) return "";
        JsonObject sel = state.getAsJsonObject("selected");
        return sel.has(mode) ? sel.get(mode).getAsString() : "";
    }

    private static String str(JsonObject o, String k) {
        return o.has(k) && !o.get(k).isJsonNull() ? o.get(k).getAsString() : "";
    }

    private void filter(String keep) {
        shown.clear();
        if (state == null) return;
        String q = query == null ? "" : query.getValue().strip().toLowerCase(Locale.ROOT);
        for (JsonElement c : state.getAsJsonArray("chats")) {
            JsonObject o = c.getAsJsonObject();
            if (!str(o, "mode").equals(mode)) continue;
            String hay = (str(o, "title") + " " + str(o, "preview") + " " + str(o, "project")).toLowerCase(Locale.ROOT);
            if (!q.isEmpty() && !hay.contains(q)) continue;
            shown.add(o);
        }
        shown.sort((a, b) -> Double.compare(b.get("updated").getAsDouble(), a.get("updated").getAsDouble()));
        String target = keep.isEmpty() ? current() : keep;
        selected = 0;
        for (int i = 0; i < shown.size(); i++) if (str(shown.get(i), "id").equals(target)) selected = i;
        reveal();
    }

    private void open(JsonObject chat) {
        mod.dispatch(mode, "select " + str(chat, "id") + " quiet");
        minecraft.setScreenAndShow(parent);
    }

    static String ago(double epochSeconds) {
        if (epochSeconds <= 0) return "";
        long s = Math.max(0, System.currentTimeMillis() / 1000 - (long) epochSeconds);
        if (s < 60) return L.t("только что");
        if (s < 3600) return s / 60 + L.t(" мин");
        if (s < 86400) return s / 3600 + L.t(" ч");
        if (s < 86400 * 30) return s / 86400 + L.t(" дн");
        return s / (86400 * 30) + L.t(" мес");
    }

    // ---- ui -----------------------------------------------------------------
    private int panelW() { return Math.min(480, width - 32); }
    private int panelX() { return (width - panelW()) / 2; }
    private int visible() { return Math.max(2, (height - TOP - 40) / ROW); }
    private int accent() { return 0xFF000000 | (mode.equals("cc") ? ChatStyle.CODE_MODE : ChatStyle.CLAUDE); }

    @Override protected void init() {
        int x = panelX(), w = panelW();
        String value = query == null ? "" : query.getValue();
        addRenderableWidget(tab("Claude", "c", x, 22));
        addRenderableWidget(tab("Claude Code", "cc", x + 64, 22));
        query = new EditBox(font, x, 42, w, 16, Component.literal("search"));
        query.setHint(Component.literal(L.t("Поиск по названию и последнему сообщению…")));
        query.setMaxLength(100);
        query.setValue(value);
        query.setResponder(v -> filter(""));
        addRenderableWidget(query);
        setInitialFocus(query);
        int by = height - 24;
        addRenderableWidget(Button.builder(Component.literal("✚ " + L.t("Новый чат")), b -> {
                mod.dispatch(mode, "new local");
                nextFetch = System.currentTimeMillis() + 400;
            }).bounds(x, by, 90, 18).build());
        if (mode.equals("c"))
            addRenderableWidget(Button.builder(Component.literal("✚ API"), b -> { mod.dispatch("c", "new api"); nextFetch = System.currentTimeMillis() + 400; })
                .bounds(x + 94, by, 56, 18).tooltip(Tooltip.create(Component.literal(L.t("Чат через Messages API")))).build());
        else
            addRenderableWidget(Button.builder(Component.literal(L.t("Синхр. с ПК")), b -> { mod.runLine("cc", "/cc sync"); nextFetch = System.currentTimeMillis() + 1500; })
                .bounds(x + 94, by, 96, 18).tooltip(Tooltip.create(Component.literal(L.t("Подтянуть сессии Claude Code этой папки с ПК")))).build());
        addRenderableWidget(Button.builder(Component.literal(L.t("Открыть")), b -> { if (!shown.isEmpty()) open(shown.get(selected)); })
            .bounds(x + w - 70, by, 70, 18).build());
        if (state == null) fetch(); else filter(selectedId());
    }

    private Button tab(String label, String m, int x, int y) {
        Button b = Button.builder(Component.literal(label), btn -> switchMode(m)).bounds(x, y, m.equals("c") ? 60 : 84, 16).build();
        b.active = !mode.equals(m);
        return b;
    }

    private void switchMode(String m) {
        if (m.equals(mode)) return;
        mode = m;
        selected = 0;
        scroll = 0;
        rebuildWidgets();
    }

    @Override public void tick() { if (System.currentTimeMillis() >= nextFetch) fetch(); }
    @Override public boolean isPauseScreen() { return false; }
    @Override public void onClose() { minecraft.setScreenAndShow(parent); }

    private void reveal() {
        if (selected < scroll) scroll = selected;
        if (selected >= scroll + visible()) scroll = selected - visible() + 1;
        scroll = Math.max(0, Math.min(scroll, Math.max(0, shown.size() - visible())));
    }

    @Override public boolean keyPressed(KeyEvent event) {
        int key = event.key();
        if (key == InputConstants.KEY_ESCAPE) { onClose(); return true; }
        if (key == InputConstants.KEY_TAB) { switchMode(mode.equals("c") ? "cc" : "c"); return true; }
        if (key == InputConstants.KEY_DOWN || key == InputConstants.KEY_UP || key == InputConstants.KEY_PAGEDOWN || key == InputConstants.KEY_PAGEUP) {
            int step = key == InputConstants.KEY_DOWN ? 1 : key == InputConstants.KEY_UP ? -1
                : key == InputConstants.KEY_PAGEDOWN ? visible() : -visible();
            selected = Math.max(0, Math.min(shown.size() - 1, selected + step));
            reveal();
            return true;
        }
        if ((key == InputConstants.KEY_RETURN || key == InputConstants.KEY_NUMPADENTER) && !shown.isEmpty()) {
            open(shown.get(selected));
            return true;
        }
        return super.keyPressed(event);
    }

    private int rowAt(double mx, double my) {
        int x = panelX();
        if (mx < x || mx >= x + panelW() || my < TOP) return -1;
        int row = (int) ((my - TOP) / ROW);
        return row < visible() && scroll + row < shown.size() ? scroll + row : -1;
    }

    @Override public boolean mouseClicked(MouseButtonEvent event, boolean doubleClick) {
        int row = rowAt(event.x(), event.y());
        if (row >= 0 && event.button() == 0) { selected = row; open(shown.get(row)); return true; }
        return super.mouseClicked(event, doubleClick);
    }

    @Override public boolean mouseScrolled(double x, double y, double h, double v) {
        scroll = Math.max(0, Math.min(Math.max(0, shown.size() - visible()), scroll - (int) Math.signum(v)));
        return true;
    }

    @Override public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float delta) {
        int x = panelX(), w = panelW();
        int bottom = Math.min(height - 30, TOP + visible() * ROW + 4);
        g.fill(0, 0, width, height, 0xB0101014);
        g.fill(x - 8, 8, x + w + 8, height - 2, Theme.PALETTE_BG);
        g.fill(x - 8, 8, x + w + 8, 10, accent());  // accent bar of the current tab
        g.text(font, "☰ " + L.t("Чаты"), x, 12, accent(), Theme.shadow());
        String hint = L.t("↑↓ выбор · Enter открыть · Tab — другой режим · Esc назад");
        g.text(font, font.plainSubstrByWidth(hint, w - 60), x + w - Math.min(w - 60, font.width(hint)), 12, Theme.WINDOW_DIM, Theme.shadow());
        if (mode.equals("cc") && state != null && state.has("project") && !state.get("project").isJsonNull()) {
            String p = "✦ " + state.get("project").getAsString();
            g.text(font, font.plainSubstrByWidth(p, w - 160), x + w - Math.min(w - 160, font.width(p)), 26, 0xFF000000 | ChatStyle.CODE_MODE, Theme.shadow());
        }
        super.extractRenderState(g, mouseX, mouseY, delta);
        // underline under the current tab (the inactive one is the clickable button)
        if (mode.equals("c")) g.fill(x, 38, x + 60, 40, accent()); else g.fill(x + 64, 38, x + 148, 40, accent());
        hover = rowAt(mouseX, mouseY);
        String now = current();
        long blink = System.currentTimeMillis() / 500 % 2;
        for (int i = 0; i < visible() && scroll + i < shown.size(); i++) {
            int index = scroll + i;
            JsonObject c = shown.get(index);
            int y = TOP + i * ROW;
            boolean sel = index == selected, isCurrent = str(c, "id").equals(now);
            if (sel) g.fill(x - 4, y - 1, x + w + 4, y + ROW - 3, Theme.PALETTE_SELECTED);
            else if (index == hover) g.fill(x - 4, y - 1, x + w + 4, y + ROW - 3, 0x20FFFFFF);
            if (sel) g.fill(x - 4, y - 1, x - 2, y + ROW - 3, accent());
            boolean running = c.has("running") && c.get("running").getAsBoolean();
            String dot = running ? "▶" : isCurrent ? "●" : "○";
            int dotColor = running ? (blink == 0 ? 0xFFF2C14E : 0xFFB08A30) : isCurrent ? 0xFF6BD08B : 0xFF6A6A6A;
            g.text(font, dot, x + 2, y + 3, dotColor, Theme.shadow());
            // right column: time, below it model · cost · queue
            String when = ago(c.get("updated").getAsDouble());
            List<String> badges = new ArrayList<>();
            if (!str(c, "model").isEmpty()) badges.add(str(c, "model"));
            else if (!str(c, "backend").isEmpty() && !str(c, "backend").equals("local")) badges.add(str(c, "backend"));
            if (c.has("cost") && c.get("cost").getAsDouble() >= 0.01) badges.add(String.format(Locale.ROOT, "$%.2f", c.get("cost").getAsDouble()));
            if (c.has("queue") && c.get("queue").getAsInt() > 0) badges.add("+" + c.get("queue").getAsInt() + L.t(" в очереди"));
            if (str(c, "origin").equals("pc")) badges.add(L.t("с ПК"));
            String badge = String.join(" · ", badges);
            int right = Math.max(font.width(when), font.width(badge));
            int textW = w - 20 - right - 10;
            String title = str(c, "title");
            if (mode.equals("cc") && !str(c, "project").isEmpty() && state != null && !str(c, "project").equals(str(state, "project")))
                title += "  ✦ " + str(c, "project");
            g.text(font, font.plainSubstrByWidth(title, textW), x + 14, y + 3, isCurrent ? 0xFFFFFFFF : Theme.WINDOW_TEXT, Theme.shadow());
            g.text(font, when, x + w - font.width(when), y + 3, Theme.WINDOW_DIM, Theme.shadow());
            String preview = str(c, "preview");
            if (running && preview.isEmpty()) preview = L.t("работает…");
            if (!preview.isEmpty()) {
                String who = str(c, "who").equals("you") ? L.t("ты: ") : "✦ ";
                int whoColor = str(c, "who").equals("you") ? 0xFF8C8C8C : accent();
                g.text(font, who, x + 14, y + 14, whoColor, Theme.shadow());
                g.text(font, font.plainSubstrByWidth(preview, textW - font.width(who)), x + 14 + font.width(who), y + 14, Theme.WINDOW_DIM, Theme.shadow());
            }
            g.text(font, badge, x + w - font.width(badge), y + 14, 0xFF8C8C8C, Theme.shadow());
        }
        if (state != null && shown.isEmpty())
            g.text(font, query.getValue().isBlank() ? L.t("Чатов пока нет — «✚ Новый чат»") : L.t("ничего не найдено"), x + 14, TOP + 4, 0xFF8A8A8A, Theme.shadow());
        if (shown.size() > visible()) {  // scrollbar
            int track = bottom - TOP, thumb = Math.max(12, track * visible() / shown.size());
            int ty = TOP + (track - thumb) * scroll / Math.max(1, shown.size() - visible());
            g.fill(x + w + 5, ty, x + w + 7, ty + thumb, 0x80FFFFFF);
        }
        String foot = status.isEmpty() ? shown.size() + L.t(" чат(ов)") : status;
        g.text(font, foot, x + 200, height - 19, status.isEmpty() ? Theme.WINDOW_DIM : 0xFFF0626A, Theme.shadow());
    }

    // for LiveTest
    int countForTest() { return shown.size(); }
    String modeForTest() { return mode; }
}
