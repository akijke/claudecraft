package dev.claudechat;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.mojang.blaze3d.platform.InputConstants;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.client.input.MouseButtonEvent;
import net.minecraft.network.chat.Component;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.CompletableFuture;

/**
 * Ctrl+Shift+P — every command of the mod with fuzzy search; Ctrl+P — jump to a file
 * of the project (opens the in-game editor). ↑/↓ choose, Enter runs, Esc goes back.
 * Typing ">" in the file palette switches to commands, like in VS Code.
 */
final class PaletteScreen extends Screen {
    record Entry(String label, String run, String hint, boolean suggest) { }

    private static final int ROW = 12;
    private final ClaudeChat mod;
    private final Screen parent;
    private boolean files;
    private EditBox query;
    private List<Entry> all = new ArrayList<>();
    private final List<Entry> shown = new ArrayList<>();
    private int selected, scroll;
    private String root = "";
    private String status = "";
    private java.util.function.Consumer<String> onFile;  // the IDE layout opens files in its editor pane

    void onFile(java.util.function.Consumer<String> target) { onFile = target; }

    PaletteScreen(ClaudeChat mod, Screen parent, boolean files) {
        super(Component.literal("Palette"));
        this.mod = mod;
        this.parent = parent;
        this.files = files;
    }

    /** Opens the palette from any screen on Ctrl+Shift+P / Ctrl+P; true when handled. */
    static boolean handleKey(ClaudeChat mod, Screen from, KeyEvent event, boolean allowCtrlP) {
        boolean ctrl = (event.modifiers() & 2) != 0, shift = (event.modifiers() & 1) != 0;
        if (!ctrl || event.key() != InputConstants.KEY_P) return false;
        if (!shift && !allowCtrlP) return false;
        net.minecraft.client.Minecraft.getInstance().setScreenAndShow(new PaletteScreen(mod, from, !shift));
        return true;
    }

    private static List<Entry> commands() {
        List<Entry> e = new ArrayList<>();
        String[][] table = {
            {L.t("Окно переписки"), "/cc ui", "K"}, {L.t("IDE: чат | редактор | терминал"), "/ide", ""}, {L.t("Файлы"), "/files", ""}, {L.t("Терминал"), "/pty", ""},
            {L.t("Новый терминал"), "/pty new", ""}, {L.t("Настройки"), "/claude", ""},
            {L.t("Перейти к файлу…"), "@files", "Ctrl+P"},
            {L.t("Новый чат Claude Code"), "/cc new", ""}, {L.t("Чаты"), "/cc c", ""}, {L.t("Задачи"), "/cc tasks", ""},
            {L.t("Остановить работу"), "/cc cancel", ""}, {L.t("Очистить очередь"), "/cc cq", ""},
            {L.t("План задачи…"), "/cc plan ", "plan mode"}, {L.t("Параллельная задача…"), "/cc spawn ", "worktree"},
            {L.t("Модель и усилие"), "/cc model", ""}, {L.t("Токены"), "/cc tokens", ""}, {L.t("Контекст"), "/cc context", ""},
            {L.t("Сжать историю"), "/cc compact", ""}, {L.t("Бюджет"), "/cc budget", ""},
            {L.t("Откатить правки Claude"), "/cc undo", ""}, {L.t("История правок"), "/cc undo list", ""},
            {L.t("Правила разрешений"), "/cc perms", ""}, {L.t("Автоодобрение"), "/cc auto", ""},
            {L.t("Скриншот → Claude"), "/cc shot", ""}, {L.t("Картинка → Claude…"), "/cc img ", ""},
            {L.t("Заменить в проекте…"), "/cc replace ", "-r -c --in"}, {L.t("Поиск в файлах"), "/files", ""},
            {"Git: status", "/cc git", ""}, {"Git: diff", "/cc git diff", ""}, {L.t("Git: куски (stage)"), "/cc git hunks", ""},
            {"Git: log", "/cc git log", ""}, {"Git: commit…", "/cc git commit ", ""}, {"Git: push", "/cc git push", ""},
            {"Git: pull", "/cc git pull", ""}, {L.t("Git: ветки"), "/cc git branch", ""}, {"Worktrees", "/cc worktree", ""},
            {L.t("Проекты"), "/cc project", ""}, {L.t("Выбрать папку проекта…"), "/cc project pick", ""},
            {L.t("Агенты"), "/cc agents", ""}, {L.t("Коннекторы"), "/cc connectors", ""}, {L.t("Slash-команды"), "/cc commands", ""},
            {L.t("Диагностика"), "/cc doctor", ""}, {L.t("Разобрать крэш Minecraft"), "/cc crash", ""},
            {L.t("Синхронизация с Claude Code на ПК"), "/cc sync", ""}, {L.t("Звуки"), "/claude sound", ""},
            {L.t("Индикатор задач вкл/выкл"), "/claude hud", ""}, {L.t("Справка"), "/cc help", ""},
            {L.t("Экспорт чата в Markdown"), "/cc export", ""}, {L.t("Экспорт чата в HTML"), "/cc export html", ""},
        };
        for (String[] t : table) e.add(new Entry(t[0], t[1], t[2], t[1].endsWith(" ")));
        for (String[] c : ClaudeChat.slashCommands)
            e.add(new Entry("/" + c[0] + (c[1].isEmpty() ? "" : " " + c[1]), "/cc /" + c[0] + (c[1].isEmpty() ? "" : " "),
                c[2], !c[1].isEmpty()));
        return e;
    }

    // ---- data -------------------------------------------------------------
    private void loadFiles() {
        status = L.t("Загрузка списка файлов…");
        CompletableFuture.supplyAsync(() -> {
            try { return mod.request("/index?mode=cc", null); }
            catch (Exception e) { JsonObject o = new JsonObject(); o.addProperty("error", e.getMessage()); return o; }
        }, mod.io).thenAccept(r -> minecraft.execute(() -> {
            if (r.has("error")) { status = r.get("error").getAsString(); return; }
            root = r.get("root").getAsString();
            List<Entry> list = new ArrayList<>();
            for (JsonElement f : r.getAsJsonArray("files")) list.add(new Entry(f.getAsString(), "", "", false));
            if (files) { all = list; filter(); }
            status = list.size() + L.t(" файлов") + (r.get("truncated").getAsBoolean() ? "+" : "") + " · " + root;
        }));
    }

    /** Fuzzy score: every query char in order; bonuses for runs, word starts and the file name. */
    static int score(String text, String q) {
        if (q.isEmpty()) return 1;
        String t = text.toLowerCase(Locale.ROOT);
        int ti = 0, score = 0, run = 0, nameStart = Math.max(t.lastIndexOf('/'), t.lastIndexOf('\\')) + 1;
        for (int qi = 0; qi < q.length(); qi++) {
            char c = q.charAt(qi);
            int found = t.indexOf(c, ti);
            if (found < 0) return 0;
            run = found == ti ? run + 1 : 0;
            score += 1 + run * 3;
            if (found == 0 || " /\\-_.:".indexOf(t.charAt(found - 1)) >= 0) score += 5;
            if (found >= nameStart) score += 2;
            ti = found + 1;
        }
        if (t.contains(q)) score += 15;
        return score * 100 / (50 + t.length());
    }

    private void filter() {
        String q = query == null ? "" : query.getValue().strip().toLowerCase(Locale.ROOT);
        if (files && q.startsWith(">")) { files = false; all = commands(); query.setValue(q.substring(1)); return; }
        shown.clear();
        List<Object[]> scored = new ArrayList<>();
        for (Entry e : all) {
            int s = Math.max(score(e.label(), q), files ? 0 : score(e.run() + " " + e.hint(), q) / 2);
            if (s > 0) scored.add(new Object[]{e, s});
        }
        if (!q.isEmpty()) scored.sort(Comparator.comparingInt((Object[] o) -> -(int) o[1]));
        for (Object[] o : scored) { shown.add((Entry) o[0]); if (shown.size() >= 300) break; }
        selected = 0;
        scroll = 0;
    }

    private void run(Entry e) {
        if (files) {
            String path = root.isEmpty() ? e.label() : root + java.io.File.separator + e.label();
            if (onFile != null) { minecraft.setScreenAndShow(parent); onFile.accept(path); return; }
            minecraft.setScreenAndShow(new EditorScreen(mod, parent, path));
            return;
        }
        if (e.run().equals("@files")) { minecraft.setScreenAndShow(new PaletteScreen(mod, parent, true)); return; }
        if (e.suggest()) {  // needs an argument: open the K window with it typed in
            ConversationScreen.prefill("cc", e.run());
            minecraft.setScreenAndShow(new ConversationScreen(mod));
            return;
        }
        minecraft.setScreenAndShow(parent);
        mod.runLine("cc", e.run());
    }

    // ---- ui -----------------------------------------------------------------
    @Override protected void init() {
        int w = Math.min(420, width - 40), x = (width - w) / 2;
        String value = query == null ? "" : query.getValue();
        query = new EditBox(font, x, 30, w, 18, Component.literal("query"));
        query.setHint(Component.literal(files ? L.t("Имя файла… (> — команды)") : L.t("Команда…")));
        query.setMaxLength(200);
        query.setValue(value);
        query.setResponder(v -> filter());
        addRenderableWidget(query);
        setInitialFocus(query);
        if (all.isEmpty()) {
            if (files) loadFiles(); else all = commands();
        }
        filter();
    }

    @Override public boolean isPauseScreen() { return false; }
    void typeForTest(String text) { query.setValue(text); }
    String firstForTest() { return shown.isEmpty() ? "" : shown.get(0).label() + " " + shown.get(0).run(); }
    @Override public void onClose() { minecraft.setScreenAndShow(parent); }

    private int visible() { return Math.max(3, (height - 80) / ROW); }

    @Override public boolean keyPressed(KeyEvent event) {
        int key = event.key();
        if (key == InputConstants.KEY_ESCAPE) { onClose(); return true; }
        if (key == InputConstants.KEY_DOWN || key == InputConstants.KEY_UP || key == InputConstants.KEY_PAGEDOWN
                || key == InputConstants.KEY_PAGEUP) {
            int step = key == InputConstants.KEY_DOWN ? 1 : key == InputConstants.KEY_UP ? -1
                : key == InputConstants.KEY_PAGEDOWN ? visible() : -visible();
            selected = Math.max(0, Math.min(shown.size() - 1, selected + step));
            if (selected < scroll) scroll = selected;
            if (selected >= scroll + visible()) scroll = selected - visible() + 1;
            return true;
        }
        if (key == InputConstants.KEY_RETURN && !shown.isEmpty()) { run(shown.get(selected)); return true; }
        if (handleKey(mod, parent, event, true)) return true;
        return super.keyPressed(event);
    }

    @Override public boolean mouseClicked(MouseButtonEvent event, boolean doubleClick) {
        int w = Math.min(420, width - 40), x = (width - w) / 2;
        int row = (int) ((event.y() - 54) / ROW) + scroll;
        if (event.x() >= x && event.x() < x + w && event.y() >= 54 && row >= 0 && row < shown.size()) {
            selected = row;
            run(shown.get(row));
            return true;
        }
        return super.mouseClicked(event, doubleClick);
    }

    @Override public boolean mouseScrolled(double x, double y, double h, double v) {
        scroll = Math.max(0, Math.min(Math.max(0, shown.size() - visible()), scroll - (int) Math.signum(v) * 3));
        return true;
    }

    @Override public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float delta) {
        g.fill(0, 0, width, height, 0xC0101014);
        int w = Math.min(420, width - 40), x = (width - w) / 2;
        g.fill(x - 4, 12, x + w + 4, Math.min(height - 8, 58 + visible() * ROW), 0xF01E1E22);
        g.text(font, files ? L.t("Перейти к файлу") + "  Ctrl+P" : L.t("Команды") + "  Ctrl+Shift+P", x, 17, 0xFFD97757);
        super.extractRenderState(g, mouseX, mouseY, delta);
        for (int i = 0; i < visible() && scroll + i < shown.size(); i++) {
            Entry e = shown.get(scroll + i);
            int y = 54 + i * ROW;
            if (scroll + i == selected) g.fill(x - 2, y - 2, x + w + 2, y + ROW - 2, 0xFF2D3A4F);
            if (files) {
                int slash = e.label().lastIndexOf('/');
                String name = e.label().substring(slash + 1), dir = slash < 0 ? "" : e.label().substring(0, slash);
                g.text(font, "📄 " + name, x, y, 0xFFE8E8E8);
                g.text(font, font.plainSubstrByWidth(dir, w - font.width("📄 " + name) - 12), x + font.width("📄 " + name) + 8, y, 0xFF6A6A6A);
            } else {
                g.text(font, e.label(), x, y, 0xFFE8E8E8);
                String right = e.hint().isEmpty() ? e.run() : e.hint();
                right = font.plainSubstrByWidth(right, w / 2);
                g.text(font, right, x + w - font.width(right), y, 0xFF6A6A6A);
            }
        }
        if (shown.isEmpty()) g.text(font, L.t("ничего не найдено"), x, 56, 0xFF8A8A8A);
        if (!status.isEmpty()) g.text(font, font.plainSubstrByWidth(status, w), x, height - 14, 0xFF5E5E5E);
    }
}
