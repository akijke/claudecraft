package dev.claudechat;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.mojang.blaze3d.platform.InputConstants;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.MultiLineEditBox;
import net.minecraft.client.gui.components.Tooltip;
import net.minecraft.client.gui.screens.ConfirmLinkScreen;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.client.input.MouseButtonEvent;
import net.minecraft.network.chat.ClickEvent;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.HoverEvent;
import net.minecraft.network.chat.Style;
import net.minecraft.util.FormattedCharSequence;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.concurrent.CompletableFuture;

/**
 * Full-screen conversation window (key K): chat list per mode and project on the left,
 * the selected chat's log with scrolling, clickable buttons and copy (right click) in the
 * middle, and a multi-line input at the bottom (Ctrl+Enter sends, lines starting with
 * "/" run commands). The game chat keeps receiving the same messages.
 */
final class ConversationScreen extends Screen {
    private static volatile boolean open;
    private static volatile boolean dirty = true;
    private static String mode = "cc";
    private static String draft = "";

    private final ClaudeChat mod;
    private JsonObject state;
    private MultiLineEditBox input;
    private InputHistory history = InputHistory.cursor();
    private final List<FormattedCharSequence> lines = new ArrayList<>();
    private String shownChat = "";
    private int scroll;          // lines scrolled up from the bottom
    private long nextState;
    private boolean fetching;
    private final List<String> historyAsked = new ArrayList<>();
    private int left, mainX, mainY, mainW, mainH;

    private final boolean preview;

    ConversationScreen(ClaudeChat mod) { this(mod, null); }

    /** Dev screenshots: fixed state, no bridge calls. */
    ConversationScreen(ClaudeChat mod, JsonObject fixedState) {
        super(Component.literal("Claudecraft"));
        this.mod = mod;
        this.state = fixedState;
        this.preview = fixedState != null;
    }

    static boolean isOpen() { return open; }

    private static volatile String visibleChat = "";
    /** Chat currently shown in the window (notifications skip it). */
    static String shownChat() { return visibleChat; }

    /** Opens the next conversation window on `m` with `text` already in the input box. */
    static void prefill(String m, String text) { mode = m; draft = text; }
    static void changed() { dirty = true; }
    String inputValue() { return input == null ? "" : input.getValue(); }
    void inputForTest(String text) { input.setValue(text); setFocused(input); history = InputHistory.cursor(); }

    @Override public void added() { open = true; dirty = true; }
    @Override public void removed() {
        open = false;
        if (input != null) {
            draft = input.getValue();
            InputHistory.setDraft(mode, draft);  // survives closing the window and the game
        }
    }
    @Override public boolean isPauseScreen() { return false; }

    // ---- data ----------------------------------------------------------------
    private String selectedChat() {
        if (state == null || !state.has("selected")) return "";
        JsonObject sel = state.getAsJsonObject("selected");
        return sel.has(mode) ? sel.get(mode).getAsString() : "";
    }

    private void fetchState() {
        if (fetching || preview) return;
        fetching = true;
        CompletableFuture.supplyAsync(() -> {
            try { return mod.request("/state", null); }
            catch (Exception e) { return null; }
        }, mod.io).thenAccept(result -> minecraft.execute(() -> {
            fetching = false;
            if (result == null) { mod.notice = L.t("Мост недоступен — /claude"); return; }
            String before = selectedChat() + "|" + chatsKey(state);
            state = result;
            String chat = selectedChat();
            if (!chat.isEmpty() && !mod.log.containsKey(chat) && !historyAsked.contains(chat)) {
                historyAsked.add(chat);  // pull this chat's recent events once
                JsonObject data = mod.command(mode, "history", "");
                data.addProperty("chat", chat);
                mod.send(mode, data);
            }
            if (!before.equals(chat + "|" + chatsKey(state))) { dirty = true; rebuildWidgets(); }
        }));
    }

    private static String chatsKey(JsonObject s) {
        if (s == null) return "";
        StringBuilder key = new StringBuilder(s.has("project") ? s.get("project").getAsString() : "");
        for (JsonElement c : s.getAsJsonArray("chats")) {
            JsonObject o = c.getAsJsonObject();
            key.append(o.get("id").getAsString(), 0, 6).append(o.get("title").getAsString()).append(o.get("running").getAsBoolean());
        }
        return key.toString();
    }

    private List<JsonObject> chatsOfMode() {
        List<JsonObject> out = new ArrayList<>();
        if (state == null) return out;
        String project = state.has("project") ? state.get("project").getAsString() : "";
        for (JsonElement c : state.getAsJsonArray("chats")) {
            JsonObject o = c.getAsJsonObject();
            if (!o.get("mode").getAsString().equals(mode)) continue;
            if (mode.equals("cc") && !project.equals(o.has("project") && !o.get("project").isJsonNull()
                ? o.get("project").getAsString() : "")) continue;
            out.add(o);
        }
        out.sort((a, b) -> Double.compare(b.get("updated").getAsDouble(), a.get("updated").getAsDouble()));
        return out;
    }

    private void rebuildLines() {
        lines.clear();
        String chat = selectedChat();
        shownChat = chat;
        visibleChat = chat;
        Deque<JsonObject> events = mod.log.get(chat);
        if (events != null) {
            for (JsonObject e : events)
                for (Component c : ChatStyle.render(e)) lines.addAll(font.split(c, mainW - 10));
        }
        dirty = false;
    }

    // ---- layout ----------------------------------------------------------------
    @Override protected void init() {
        if (input != null) draft = input.getValue();
        left = 6;
        int panel = Math.min(150, width / 4);
        mainX = left + panel + 8;
        mainY = 8;
        mainW = width - mainX - 6;
        mainH = height - 82;  // log ends above the notice line and the input box

        addRenderableWidget(tab("Claude", "c", left, 22, panel / 2 - 1));
        addRenderableWidget(tab("Code", "cc", left + panel / 2 + 1, 22, panel / 2 - 1));
        int y = 46;
        if (mode.equals("cc") && state != null) {
            String project = state.get("project").getAsString();
            addRenderableWidget(Button.builder(Component.literal("✦ " + project).withColor(ChatStyle.CODE_MODE),
                    b -> mod.runLine(mode, "/cc project"))
                .bounds(left, y, panel - 24, 20)
                .tooltip(Tooltip.create(Component.literal(state.get("project_path").getAsString() + L.t("\nСменить проект")))).build());
            addRenderableWidget(Button.builder(Component.literal("+"), b -> FolderPicker.pick(mod))
                .bounds(left + panel - 22, y, 22, 20)
                .tooltip(Tooltip.create(Component.literal(L.t("Открыть папку на ПК как проект")))).build());
            y += 24;
        }
        String selected = selectedChat();
        int maxChats = Math.max(1, (height - y - 100) / 22);
        List<JsonObject> chats = chatsOfMode();
        for (JsonObject c : chats.subList(0, Math.min(chats.size(), maxChats))) {
            String id = c.get("id").getAsString();
            boolean running = c.get("running").getAsBoolean();
            String label = (running ? "▶ " : "") + font.plainSubstrByWidth(c.get("title").getAsString(), panel - 18);
            Button b = Button.builder(Component.literal(label).withColor(id.equals(selected) ? 0x6BD08B : 0xE8E8E8), btn -> {
                mod.send(mode, mod.command(mode, "select", id));
                scroll = 0;
                nextState = 0;
            }).bounds(left, y, panel, 20).build();
            b.active = !id.equals(selected);
            addRenderableWidget(b);
            y += 22;
        }
        addRenderableWidget(Button.builder(Component.literal(L.t("+ Новый чат")), b -> { mod.dispatch(mode, "new"); nextState = 0; })
            .bounds(left, y, panel, 20).build());

        int bw = (panel - 2) / 2;  // two per row so labels are not cut
        addRenderableWidget(Button.builder(Component.literal(L.t("Файлы")), b -> minecraft.setScreenAndShow(new FileScreen(mod, this)))
            .bounds(left, height - 70, bw, 20).tooltip(Tooltip.create(Component.literal(L.t("Файловый менеджер")))).build());
        addRenderableWidget(small(L.t("Агенты"), "/cc agents", L.t("Агенты-помощники"), left + bw + 2, height - 70, bw));
        addRenderableWidget(small(L.t("Задачи"), "/cc tasks", L.t("Все работающие задачи и агенты"), left, height - 48, bw));
        addRenderableWidget(small("Git", "/cc git", L.t("Git текущего проекта"), left + bw + 2, height - 48, bw));
        addRenderableWidget(small("MCP", "/cc connectors", L.t("Коннекторы"), left, height - 26, bw));
        addRenderableWidget(Button.builder(Component.literal("⚙"), b -> mod.runLine(mode, "/claude"))
            .bounds(left + bw + 2, height - 26, bw, 20).tooltip(Tooltip.create(Component.literal(L.t("Настройки")))).build());

        int inputW = mainW - 92;
        input = new MultiLineEditBox.Builder()
            .setPlaceholder(Component.literal(mode.equals("cc") ? L.t("Задача для Claude Code… (Ctrl+Enter — отправить, /cc … — команда)")
                : L.t("Сообщение Claude… (Ctrl+Enter — отправить)")))
            .build(font, inputW, 52, Component.literal("input"));
        input.setX(mainX);
        input.setY(height - 58);
        input.setCharacterLimit(8000);
        if (draft.isEmpty()) draft = InputHistory.draft(mode);
        input.setValue(draft);
        addRenderableWidget(input);
        int bx = mainX + inputW + 4;
        addRenderableWidget(Button.builder(Component.literal(L.t("Отправить")), b -> submit(""))
            .bounds(bx, height - 58, 88, 20).tooltip(Tooltip.create(Component.literal(L.t("В очередь (Ctrl+Enter)")))).build());
        addRenderableWidget(Button.builder(Component.literal("f"), b -> submit("f "))
            .bounds(bx, height - 36, 42, 16).tooltip(Tooltip.create(Component.literal(L.t("Дописать в текущую работу, без отмены")))).build());
        addRenderableWidget(Button.builder(Component.literal("⏹"), b -> mod.dispatch(mode, "cancel"))
            .bounds(bx + 46, height - 36, 42, 16).tooltip(Tooltip.create(Component.literal(L.t("Остановить и очистить очередь")))).build());
        addRenderableWidget(Button.builder(Component.literal("◎ " + L.t("Скриншот")), b -> {
                String question = input.getValue().strip();
                input.setValue("");
                draft = "";
                mod.screenshot(mode, question);
            }).bounds(bx, height - 18, 88, 16)
            .tooltip(Tooltip.create(Component.literal(L.t("Снимок игры без интерфейса → Claude; текст из поля уйдёт вопросом")))).build());
        setInitialFocus(input);
        dirty = true;
    }

    private Button tab(String label, String m, int x, int y, int w) {
        Button b = Button.builder(Component.literal(label).withColor(m.equals("cc") ? ChatStyle.CODE_MODE : ChatStyle.CLAUDE), btn -> {
            mode = m;
            scroll = 0;
            rebuildWidgets();
        }).bounds(x, y, w, 20).build();
        b.active = !mode.equals(m);
        return b;
    }

    private Button small(String label, String line, String tip, int x, int y, int w) {
        return Button.builder(Component.literal(label), b -> mod.runLine(mode, line))
            .bounds(x, y, w, 20).tooltip(Tooltip.create(Component.literal(tip))).build();
    }

    private void submit(String prefix) {
        String text = input.getValue().strip();
        if (text.isEmpty()) return;
        if (text.startsWith("/")) mod.runLine(mode, text);
        else mod.dispatch(mode, prefix.isEmpty() ? "q " + text : prefix + text);
        InputHistory.add(text);
        InputHistory.setDraft(mode, "");
        history = InputHistory.cursor();
        input.setValue("");
        draft = "";
        scroll = 0;
    }

    // ---- input -----------------------------------------------------------------
    @Override public boolean keyPressed(KeyEvent event) {
        boolean ctrl = (event.modifiers() & 2) != 0;  // GLFW_MOD_CONTROL
        if (event.key() == InputConstants.KEY_RETURN && ctrl) { submit(""); return true; }
        // Tab after "/name": complete a slash command
        if (getFocused() == input && event.key() == InputConstants.KEY_TAB && input.getValue().startsWith("/")
                && !input.getValue().contains(" ")) {
            mod.refreshCommands(mode);
            String typed = input.getValue().substring(1);
            List<String> names = new java.util.ArrayList<>();
            for (String[] c : ClaudeChat.slashCommands) if (c[0].startsWith(typed)) names.add(c[0]);
            if (names.size() == 1) input.setValue("/" + names.get(0) + " ");
            else if (!names.isEmpty()) {
                String common = names.get(0);
                for (String n : names) while (!n.startsWith(common)) common = common.substring(0, common.length() - 1);
                if (common.length() > typed.length()) input.setValue("/" + common);
                mod.notice = String.join("  ", names.stream().map(n -> "/" + n).limit(12).toList());
            }
            return true;
        }
        // ↑/↓ walk through sent prompts when the box is empty, while browsing, or with Ctrl
        if (getFocused() == input && (event.key() == InputConstants.KEY_UP || event.key() == InputConstants.KEY_DOWN)) {
            boolean up = event.key() == InputConstants.KEY_UP;
            if (ctrl || input.getValue().isEmpty() || history.browsing()) {
                String value = up ? history.older(input.getValue()) : history.newer();
                if (value != null) { input.setValue(value); return true; }
                if (up) return true;
            }
        }
        return super.keyPressed(event);
    }

    @Override public boolean mouseScrolled(double x, double y, double horizontal, double vertical) {
        if (x >= mainX && x < mainX + mainW && y >= mainY && y < mainY + mainH) {
            int visible = mainH / 9;
            scroll = Math.max(0, Math.min(Math.max(0, lines.size() - visible), scroll + (int) Math.signum(vertical) * 3));
            return true;
        }
        return super.mouseScrolled(x, y, horizontal, vertical);
    }

    @Override public boolean mouseClicked(MouseButtonEvent event, boolean doubleClick) {
        double x = event.x(), y = event.y();
        if (x >= mainX && x < mainX + mainW && y >= mainY && y < mainY + mainH) {
            FormattedCharSequence line = lineAt(y);
            if (line == null) return true;
            if (event.button() == 1) {  // right click: copy the line
                minecraft.keyboardHandler.setClipboard(plain(line));
                mod.notice = L.t("Строка скопирована");
                return true;
            }
            Style style = styleAt(line, (int) x - mainX - 4);
            if (style != null && style.getClickEvent() != null) handleClick(style.getClickEvent());
            return true;
        }
        return super.mouseClicked(event, doubleClick);
    }

    private void handleClick(ClickEvent click) {
        if (click instanceof ClickEvent.RunCommand run) mod.runLine(mode, run.command());
        else if (click instanceof ClickEvent.SuggestCommand suggest) { input.setValue(suggest.command()); setFocused(input); }
        else if (click instanceof ClickEvent.OpenUrl url) ConfirmLinkScreen.confirmLinkNow(this, url.uri());
        scroll = 0;
    }

    private int firstVisible() {
        int visible = mainH / 9;
        return Math.max(0, lines.size() - visible - scroll);
    }

    private FormattedCharSequence lineAt(double y) {
        int index = firstVisible() + (int) ((y - mainY - 3) / 9);
        return index >= 0 && index < lines.size() && index < firstVisible() + mainH / 9 ? lines.get(index) : null;
    }

    private Style styleAt(FormattedCharSequence line, int x) {
        int[] pos = {0};
        Style[] hit = {null};
        line.accept((index, style, codepoint) -> {
            int w = font.width(FormattedCharSequence.forward(new String(Character.toChars(codepoint)), style));
            if (x >= pos[0] && x < pos[0] + w) { hit[0] = style; return false; }
            pos[0] += w;
            return true;
        });
        return hit[0];
    }

    private static String plain(FormattedCharSequence line) {
        StringBuilder out = new StringBuilder();
        line.accept((index, style, codepoint) -> { out.appendCodePoint(codepoint); return true; });
        String text = out.toString();
        return text.startsWith("▍ ") ? text.substring(2) : text;
    }

    // ---- frame -----------------------------------------------------------------
    @Override public void tick() {
        long now = System.currentTimeMillis();
        if (now >= nextState) { nextState = now + 1500; fetchState(); }
        if (dirty || !selectedChat().equals(shownChat)) rebuildLines();
    }

    @Override public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float delta) {
        g.fill(0, 0, width, height, 0xB0101014);
        g.fill(mainX - 2, mainY - 2, mainX + mainW, mainY + mainH, 0x90000000);
        super.extractRenderState(g, mouseX, mouseY, delta);
        g.text(font, Component.literal("✦ Claudecraft").withColor(ChatStyle.CLAUDE), left, 8, 0xFFFFFFFF);

        int visible = mainH / 9, first = firstVisible(), y = mainY + 3;
        for (int i = first; i < Math.min(lines.size(), first + visible); i++) {
            g.text(font, lines.get(i), mainX + 4, y, 0xFFFFFFFF);
            y += 9;
        }
        if (lines.isEmpty())
            g.centeredText(font, mode.equals("cc") ? L.t("Напиши задачу внизу или открой проект слева") : L.t("Напиши сообщение внизу"),
                mainX + mainW / 2, mainY + mainH / 2, 0xFF707070);
        if (scroll > 0)
            g.text(font, L.t("↓ ещё ") + scroll + L.t(" строк ниже (колесо мыши)"), mainX + mainW - 170, mainY + mainH - 11, 0xFFF2C14E);
        // Hover text of buttons inside the log.
        if (mouseX >= mainX && mouseX < mainX + mainW && mouseY >= mainY && mouseY < mainY + mainH) {
            FormattedCharSequence line = lineAt(mouseY);
            Style style = line == null ? null : styleAt(line, mouseX - mainX - 4);
            if (style != null && style.getHoverEvent() instanceof HoverEvent.ShowText show)
                g.setTooltipForNextFrame(font, font.split(show.value(), 260), mouseX, mouseY);
        }
        String notice = mod.notice;
        if (!notice.isEmpty())
            g.text(font, font.plainSubstrByWidth("· " + notice, mainW), mainX, height - 70, 0xFF8A8A8A);
    }
}
