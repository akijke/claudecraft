package dev.claudechat;

import com.google.gson.JsonArray;
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
import net.minecraft.network.chat.CommonComponents;
import net.minecraft.network.chat.Component;
import java.io.File;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.CompletableFuture;

/**
 * File manager window (/files, /cc files, "Files" in the K window): folder list on the
 * left, file preview on the right, path bar with clickable parts, quick jumps and
 * actions. Browsing never moves Claude; "Make project" does that explicitly.
 * Data comes from the bridge (GET /files, GET /file); changes go through /command.
 */
final class FileScreen extends Screen {
    private static final int TEXT = 0xFFE8E8E8, DIM = 0xFF8A8A8A, FAINT = 0xFF5E5E5E, DIR = 0xFF6CA8FF,
        GOLD = 0xFFF2C14E, GREEN = 0xFF6BD08B, RED = 0xFFF0626A, CODE = 0xFF9CDCFE, ACCENT = 0xFFB48EF0;
    private static final int ROW = 11;
    private static String lastPath = "";

    private final ClaudeChat mod;
    private final Screen parent;
    private final boolean preview;
    private JsonObject dir;
    private final List<JsonObject> rows = new ArrayList<>();
    private int selected = -1, listScroll;
    private JsonObject file;
    private final List<String> fileLines = new ArrayList<>();
    private int fileScroll;
    private boolean loadingMore;
    private EditBox filter, nameInput;
    private String pending = "";         // "", "mkdir", "touch", "rename"
    private boolean confirmDelete;
    private String message = "";
    private int messageColor = DIM;
    private int listX, listY, listW, listH, viewX, viewW;
    private final List<int[]> crumbs = new ArrayList<>();   // x0, x1, index into crumbPaths
    private final List<String> crumbPaths = new ArrayList<>();
    private int workX0, workX1;  // clickable "… works in" label
    private static boolean contentMode;  // search box: names (false) or file contents (true)
    private JsonArray matches;           // results of a contents search, null = none
    private int fileStart = 1, highlightLine;

    FileScreen(ClaudeChat mod, Screen parent) { this(mod, parent, null); }

    /** Dev screenshots: fixed listing, no bridge calls. */
    FileScreen(ClaudeChat mod, Screen parent, JsonObject fixed) {
        super(Component.literal(L.t("Файлы")));
        this.mod = mod;
        this.parent = parent;
        this.preview = fixed != null;
        this.dir = fixed;
    }

    static void resetSearchMode() { contentMode = false; }

    /** Dev screenshots: show contents-search results and an open file at a line. */
    FileScreen previewSearch(JsonArray found, JsonObject openFile, int line) {
        contentMode = true;
        matches = found;
        file = openFile;
        fileStart = openFile.get("start").getAsInt();
        for (JsonElement l : openFile.getAsJsonArray("lines")) fileLines.add(l.getAsString());
        highlightLine = line;
        selected = 0;
        return this;
    }

    // ---- data -----------------------------------------------------------------
    private static String enc(String s) { return URLEncoder.encode(s == null ? "" : s, StandardCharsets.UTF_8); }

    private String path() { return dir == null ? "" : dir.get("path").getAsString(); }

    private String child(String name) {
        String p = path();
        return p.endsWith(File.separator) || p.endsWith("/") ? p + name : p + File.separator + name;
    }

    private JsonObject current() { return selected >= 0 && selected < rows.size() ? rows.get(selected) : null; }

    void load(String target) {
        if (preview) return;
        CompletableFuture.supplyAsync(() -> {
            try { return mod.request("/files?mode=cc&path=" + enc(target), null); }
            catch (Exception e) { return error(e); }
        }, mod.io).thenAccept(result -> minecraft.execute(() -> {
            if (result.has("error")) { say(result.get("error").getAsString(), RED); return; }
            String before = path();
            dir = result;
            lastPath = path();
            if (!before.equals(path())) { selected = -1; listScroll = 0; file = null; fileLines.clear(); }
            applyFilter();
            if (selected >= rows.size()) selected = rows.size() - 1;
        }));
    }

    private void loadFile(String target, int start) {
        if (preview) return;
        loadingMore = true;
        CompletableFuture.supplyAsync(() -> {
            try { return mod.request("/file?mode=cc&path=" + enc(target) + "&start=" + start, null); }
            catch (Exception e) { return error(e); }
        }, mod.io).thenAccept(result -> minecraft.execute(() -> {
            loadingMore = false;
            if (result.has("error")) { say(result.get("error").getAsString(), RED); return; }
            boolean append = file != null && file.get("path").getAsString().equals(result.get("path").getAsString())
                && start == fileStart + fileLines.size();
            if (!append) { file = result; fileLines.clear(); fileScroll = 0; fileStart = start; }
            for (JsonElement line : result.getAsJsonArray("lines")) fileLines.add(line.getAsString());
        }));
    }

    private static JsonObject error(Exception e) {
        JsonObject o = new JsonObject();
        o.addProperty("error", e.getMessage() == null ? e.toString() : e.getMessage());
        return o;
    }

    /** Runs a bridge command (mkdir, mv, rm, open, project …) and refreshes the listing. */
    private void run(String action, String text, String done) {
        CompletableFuture.supplyAsync(() -> {
            try { mod.request("/command", mod.command("cc", action, text)); return null; }
            catch (Exception e) { return e.getMessage(); }
        }, mod.io).thenAccept(err -> minecraft.execute(() -> {
            if (err != null) say(err, RED);
            else { if (done != null) say(done, GREEN); load(path()); }
        }));
    }

    private void applyFilter() {
        rows.clear();
        if (contentMode) {
            if (matches != null)
                for (JsonElement e : matches) {
                    JsonObject m = e.getAsJsonObject(), r = new JsonObject();
                    r.addProperty("name", m.get("path").getAsString());
                    r.addProperty("dir", false);
                    r.addProperty("size", 0);
                    r.addProperty("line", m.get("line").getAsInt());
                    r.addProperty("text", m.get("text").getAsString());
                    rows.add(r);
                }
            return;
        }
        if (dir == null) return;
        String q = filter == null ? "" : filter.getValue().strip().toLowerCase(Locale.ROOT);
        for (JsonElement e : dir.getAsJsonArray("rows")) {
            JsonObject r = e.getAsJsonObject();
            if (q.isEmpty() || r.get("name").getAsString().toLowerCase(Locale.ROOT).contains(q)) rows.add(r);
        }
    }

    private long messageAt;

    /** Contents search under the current folder (Enter in the search box in "in files" mode). */
    private void grep(String query) {
        if (query.isBlank() || preview) return;
        say(L.t("Ищу…"), DIM);
        CompletableFuture.supplyAsync(() -> {
            try { return mod.request("/grep?mode=cc&path=" + enc(path()) + "&q=" + enc(query), null); }
            catch (Exception e) { return error(e); }
        }, mod.io).thenAccept(result -> minecraft.execute(() -> {
            if (result.has("error")) { say(result.get("error").getAsString(), RED); return; }
            matches = result.getAsJsonArray("matches");
            selected = -1;
            listScroll = 0;
            applyFilter();
            say(matches.size() + " " + L.t("совпадений в") + " " + result.get("files").getAsInt() + " " + L.t("файлах")
                + (result.get("truncated").getAsBoolean() ? " " + L.t("(показаны первые)") : ""), matches.isEmpty() ? GOLD : GREEN);
        }));
    }

    private void say(String text, int color) { message = text; messageColor = color; messageAt = System.currentTimeMillis(); }

    // ---- layout ---------------------------------------------------------------
    @Override protected void init() {
        String filterValue = filter == null ? "" : filter.getValue();
        String nameValue = nameInput == null ? "" : nameInput.getValue();
        listX = 6;
        listY = 72;
        listW = Math.max(150, (int) (width * 0.42));
        listH = height - listY - 66;  // leaves a status line between the panes and the buttons
        viewX = listX + listW + 8;
        viewW = width - viewX - 6;

        // toolbar
        int x = 6, y = 34;
        x = tool(x, y, "↑", L.t("Вверх"), () -> { if (dir != null && !dir.get("parent").isJsonNull()) load(dir.get("parent").getAsString()); });
        x = tool(x, y, "✦ " + L.t("Проект"), L.t("Папка текущего проекта (где работает Claude)"),
            () -> { if (dir != null) load(dir.get("claude").getAsString()); });
        x = tool(x, y, "~ workspace", null, () -> { if (dir != null) load(dir.get("workspace").getAsString()); });
        x = tool(x, y, L.t("Дом"), System.getProperty("user.home"), () -> { if (dir != null) load(dir.get("home").getAsString()); });
        if (dir != null && dir.has("roots")) {
            for (JsonElement root : dir.getAsJsonArray("roots")) {
                String r = root.getAsString();
                x = tool(x, y, r.replace("\\", ""), null, () -> load(r));
            }
        }
        x = tool(x, y, "⟳", L.t("Обновить"), () -> load(path()));
        x = tool(x, y, "📂 " + L.t("Папка на ПК…"), L.t("Системное окно выбора; папка станет проектом"), () -> FolderPicker.pick(mod));
        filter = new EditBox(font, listX, 54, Math.min(listW, 180), 14, Component.literal("filter"));
        filter.setHint(Component.literal(L.t("фильтр…")));
        filter.setValue(filterValue);
        filter.setHint(Component.literal(contentMode ? L.t("текст в файлах… (Enter)") : L.t("фильтр…")));
        filter.setResponder(v -> { if (!contentMode) { applyFilter(); selected = rows.isEmpty() ? -1 : 0; listScroll = 0; } });
        addRenderableWidget(filter);
        // ✖ clears the search box and shows the whole folder again.
        addRenderableWidget(Button.builder(Component.literal(contentMode ? "≡ " + L.t("в файлах") : "Aa " + L.t("имена")), b -> {
                contentMode = !contentMode;
                matches = null;
                selected = -1;
                rebuildWidgets();
            }).bounds(listX + Math.min(listW, 180) + 20, 53, 74, 16)
            .tooltip(Tooltip.create(Component.literal(L.t("Искать по именам или по содержимому файлов")))).build());
        addRenderableWidget(Button.builder(Component.literal("✖"), b -> {
                filter.setValue(""); matches = null; applyFilter(); setFocused(filter); })
            .bounds(listX + Math.min(listW, 180) + 2, 53, 16, 16)
            .tooltip(Tooltip.create(Component.literal(L.t("Очистить поиск")))).build());
        applyFilter();

        // bottom actions
        int bottom1 = height - 48, bottom2 = height - 25;
        if (!pending.isEmpty()) {
            nameInput = new EditBox(font, 6, bottom1, width - 180, 18, Component.literal("name"));
            nameInput.setMaxLength(255);
            nameInput.setHint(Component.literal(pending.equals("rename") ? L.t("новое имя") : pending.equals("mkdir")
                ? L.t("имя новой папки") : L.t("имя нового файла")));
            nameInput.setValue(nameValue);
            addRenderableWidget(nameInput);
            addRenderableWidget(Button.builder(Component.literal("✔ OK"), b -> confirmName()).bounds(width - 170, bottom1 - 1, 80, 20).build());
            addRenderableWidget(Button.builder(CommonComponents.GUI_CANCEL, b -> { pending = ""; rebuildWidgets(); })
                .bounds(width - 86, bottom1 - 1, 80, 20).build());
            setInitialFocus(nameInput);
        } else {
            nameInput = null;
            row(bottom1, new String[][]{
                {"+ " + L.t("Папка"), L.t("Новая папка здесь")},
                {"+ " + L.t("Файл"), L.t("Новый пустой файл здесь")},
                {L.t("Переименовать"), "F2"},
                {confirmDelete ? L.t("Точно удалить?") : L.t("Удалить"), "Delete"},
                {L.t("Копировать путь"), null},
            }, new Runnable[]{
                () -> startName("mkdir"), () -> startName("touch"),
                () -> { if (current() != null) startName("rename"); },
                this::delete,
                () -> { minecraft.keyboardHandler.setClipboard(current() != null ? child(current().get("name").getAsString()) : path());
                        say(L.t("Путь скопирован"), GREEN); },
            });
        }
        row(bottom2, new String[][]{
            {L.t("Проводник"), L.t("Открыть в проводнике / программой по умолчанию")},
            {L.t("Редактор"), "VS Code / Cursor / IDEA"},
            {"→ Claude", L.t("Спросить Claude про выбранный файл или папку")},
            {"✦ " + L.t("Проект"), L.t("Сделать проектом") + ": " + L.t("Claude будет работать в этой папке")},
            {"$ " + L.t("Терминал"), L.t("Окно K с командой оболочки в этой папке")},
            {CommonComponents.GUI_DONE.getString(), null},
        }, new Runnable[]{
            () -> run("open", current() != null ? child(current().get("name").getAsString()) : path(), null),
            () -> run("code", current() != null ? child(current().get("name").getAsString()) : path(), null),
            this::askClaude,
            () -> run("project", "pick " + (current() != null && current().get("dir").getAsBoolean()
                ? child(current().get("name").getAsString()) : path()), L.t("Проект открыт")),
            () -> openConversation("$ "),
            this::onClose,
        });
        if (dir == null) load(lastPath);
    }

    private int tool(int x, int y, String label, String tip, Runnable action) {
        int w = font.width(label) + 10;
        Button.Builder b = Button.builder(Component.literal(label), btn -> action.run()).bounds(x, y - 1, w, 20);
        if (tip != null) b.tooltip(Tooltip.create(Component.literal(tip)));
        addRenderableWidget(b.build());
        return x + w + 2;
    }

    private void row(int y, String[][] labels, Runnable[] actions) {
        int n = labels.length, w = (width - 12 - (n - 1) * 3) / n;
        for (int i = 0; i < n; i++) {
            Runnable action = actions[i];
            Button.Builder b = Button.builder(Component.literal(labels[i][0]), btn -> action.run())
                .bounds(6 + i * (w + 3), y - 1, w, 20);
            if (labels[i][1] != null) b.tooltip(Tooltip.create(Component.literal(labels[i][1])));
            addRenderableWidget(b.build());
        }
    }

    // ---- actions --------------------------------------------------------------
    private void startName(String what) {
        pending = what;
        confirmDelete = false;
        rebuildWidgets();
        if (what.equals("rename") && current() != null) nameInput.setValue(current().get("name").getAsString());
    }

    private void confirmName() {
        String name = nameInput.getValue().strip();
        if (name.isEmpty() || name.contains("/") || name.contains("\\")) { say(L.t("Нужно имя без / и \\"), RED); return; }
        switch (pending) {
            case "mkdir" -> run("mkdir", child(name), null);
            case "touch" -> run("touch", child(name), null);
            case "rename" -> {
                if (current() != null)
                    run("mv", "\"" + child(current().get("name").getAsString()) + "\" " + child(name), null);
            }
            default -> { }
        }
        pending = "";
        rebuildWidgets();
    }

    private void delete() {
        if (current() == null) return;
        if (!confirmDelete) { confirmDelete = true; rebuildWidgets(); say(L.t("Нажми «Точно удалить?» ещё раз — без корзины"), GOLD); return; }
        confirmDelete = false;
        run("rm", "--yes " + child(current().get("name").getAsString()), null);
        rebuildWidgets();
    }

    private void askClaude() {
        String target = current() != null ? child(current().get("name").getAsString()) : path();
        openConversation(L.t("Посмотри ") + target + ": ");
    }

    private void openConversation(String draft) {
        ConversationScreen.prefill("cc", draft);
        minecraft.setScreenAndShow(new ConversationScreen(mod));
    }

    private void openSelected() {
        JsonObject r = current();
        if (r == null) return;
        String target = child(r.get("name").getAsString());
        if (r.get("dir").getAsBoolean()) load(target);
        else loadFile(target, 1);
    }

    // ---- input ----------------------------------------------------------------
    @Override public boolean keyPressed(KeyEvent event) {
        boolean typing = getFocused() == filter || (nameInput != null && getFocused() == nameInput);
        int key = event.key();
        if (nameInput != null && getFocused() == nameInput && key == InputConstants.KEY_RETURN) { confirmName(); return true; }
        if (!rows.isEmpty() && (key == InputConstants.KEY_DOWN || key == InputConstants.KEY_UP
                || key == InputConstants.KEY_PAGEDOWN || key == InputConstants.KEY_PAGEUP)) {
            int step = key == InputConstants.KEY_DOWN ? 1 : key == InputConstants.KEY_UP ? -1
                : key == InputConstants.KEY_PAGEDOWN ? listH / ROW : -listH / ROW;
            select(Math.max(0, Math.min(rows.size() - 1, (selected < 0 ? 0 : selected) + step)));
            return true;
        }
        if (key == InputConstants.KEY_RETURN && !typing) { openSelected(); return true; }
        if (key == InputConstants.KEY_RETURN && getFocused() == filter && contentMode) { grep(filter.getValue()); return true; }
        if (key == InputConstants.KEY_RETURN && getFocused() == filter && !rows.isEmpty()) { select(0); openSelected(); return true; }
        if (key == InputConstants.KEY_BACKSPACE && !typing && dir != null && !dir.get("parent").isJsonNull()) {
            load(dir.get("parent").getAsString()); return true;
        }
        if (key == InputConstants.KEY_DELETE && !typing) { delete(); return true; }
        if (key == InputConstants.KEY_F2 && !typing && current() != null) { startName("rename"); return true; }
        return super.keyPressed(event);
    }

    private void select(int index) {
        selected = index;
        int visible = listH / ROW;
        if (selected < listScroll) listScroll = selected;
        if (selected >= listScroll + visible) listScroll = selected - visible + 1;
        JsonObject r = current();
        if (r != null && !r.get("dir").getAsBoolean()) {
            int line = r.has("line") ? r.get("line").getAsInt() : 0;
            highlightLine = line;
            loadFile(child(r.get("name").getAsString()), Math.max(1, line - 8));
        }
    }

    @Override public boolean mouseScrolled(double x, double y, double h, double v) {
        int step = (int) Math.signum(v) * -3;
        if (x >= listX && x < listX + listW && y >= listY && y < listY + listH) {
            listScroll = Math.max(0, Math.min(Math.max(0, rows.size() - listH / ROW), listScroll + step));
            return true;
        }
        if (x >= viewX && x < viewX + viewW && y >= listY && y < listY + listH) {
            int visible = (listH - 14) / 9;
            fileScroll = Math.max(0, Math.min(Math.max(0, fileLines.size() - visible), fileScroll + step));
            if (file != null && !loadingMore && fileScroll + visible >= fileLines.size() - 5
                    && fileStart - 1 + fileLines.size() < file.get("total").getAsInt())
                loadFile(file.get("path").getAsString(), fileStart + fileLines.size());
            return true;
        }
        return super.mouseScrolled(x, y, h, v);
    }

    @Override public boolean mouseClicked(MouseButtonEvent event, boolean doubleClick) {
        double x = event.x(), y = event.y();
        if (x >= listX && x < listX + listW && y >= listY && y < listY + listH) {
            int index = listScroll + (int) ((y - listY - 2) / ROW);
            if (index >= 0 && index < rows.size()) {
                select(index);
                if (doubleClick) openSelected();
            }
            setFocused(null);
            return true;
        }
        for (int[] c : crumbs) {
            if (y >= 20 && y < 30 && x >= c[0] && x < c[1]) { load(crumbPaths.get(c[2])); return true; }
        }
        if (dir != null && y >= 5 && y < 16 && x >= workX0 && x < workX1) { load(dir.get("claude").getAsString()); return true; }
        return super.mouseClicked(event, doubleClick);
    }

    @Override public void onClose() { minecraft.setScreenAndShow(parent); }
    @Override public boolean isPauseScreen() { return false; }

    // ---- render ---------------------------------------------------------------
    @Override public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float delta) {
        g.fill(0, 0, width, height, 0xB0101014);
        g.fill(listX - 2, listY - 2, listX + listW + 2, listY + listH + 2, 0x90000000);
        g.fill(viewX - 2, listY - 2, viewX + viewW + 2, listY + listH + 2, 0x90000000);
        super.extractRenderState(g, mouseX, mouseY, delta);
        g.text(font, Component.literal("📁 " + L.t("Файлы")).withColor(0xD97757), 6, 7, TEXT);
        if (dir != null) {
            String project = dir.has("project") && !dir.get("project").isJsonNull() ? dir.get("project").getAsString() : null;
            // "Claude Code works in: <project>" — click opens that folder (Codexcraft shows "Codex works in").
            String claude = L.t("Claude Code работает в: ") + dir.get("current_project").getAsString();
            workX0 = width - font.width(claude) - 6;
            workX1 = width - 6;
            boolean hover = mouseY >= 5 && mouseY < 16 && mouseX >= workX0 && mouseX < workX1;
            g.text(font, Component.literal(claude).withStyle(st -> st.withUnderlined(hover)), workX0, 7, hover ? GOLD : ACCENT);
            if (project != null) g.text(font, "✦ " + project, 60, 7, ACCENT);
            drawCrumbs(g, mouseX, mouseY);
        }
        drawList(g, mouseX, mouseY);
        drawPreview(g);
        if (!message.isEmpty() && System.currentTimeMillis() - messageAt < 8000) {
            String shown = font.plainSubstrByWidth(message, width - 16);
            g.fill(4, listY + listH + 2, 8 + font.width(shown), listY + listH + 13, 0xC0000000);
            g.text(font, shown, 6, listY + listH + 4, messageColor);
        }
    }

    private void drawCrumbs(GuiGraphicsExtractor g, int mouseX, int mouseY) {
        crumbs.clear();
        crumbPaths.clear();
        String p = path();
        String sep = p.contains("\\") ? "\\" : "/";
        String[] parts = p.split(sep.equals("\\") ? "\\\\" : "/");
        StringBuilder acc = new StringBuilder();
        int x = 6;
        for (int i = 0; i < parts.length; i++) {
            if (parts[i].isEmpty() && i > 0) continue;
            acc.append(parts[i]);
            String target = acc.toString() + (i == 0 ? sep : "");
            acc.append(sep);
            String label = parts[i].isEmpty() ? sep : parts[i];
            int w = font.width(label);
            if (x + w > width - 10) { g.text(font, "…", x, 20, DIM); break; }
            boolean hover = mouseY >= 20 && mouseY < 30 && mouseX >= x && mouseX < x + w;
            g.text(font, label, x, 20, i == parts.length - 1 ? TEXT : hover ? GOLD : DIR);
            crumbs.add(new int[]{x, x + w, crumbPaths.size()});
            crumbPaths.add(target);
            x += w;
            if (i < parts.length - 1) { g.text(font, " › ", x, 20, FAINT); x += font.width(" › "); }
        }
    }

    private void drawList(GuiGraphicsExtractor g, int mouseX, int mouseY) {
        if (dir == null) { g.text(font, L.t("Загрузка…"), listX + 4, listY + 4, DIM); return; }
        int visible = listH / ROW;
        if (rows.isEmpty()) g.text(font, L.t("  (пусто)"), listX + 4, listY + 4, DIM);
        for (int i = listScroll; i < Math.min(rows.size(), listScroll + visible); i++) {
            JsonObject r = rows.get(i);
            int y = listY + 2 + (i - listScroll) * ROW;
            if (i == selected) g.fill(listX, y - 1, listX + listW, y + ROW - 1, 0x50FFFFFF);
            else if (mouseX >= listX && mouseX < listX + listW && mouseY >= y - 1 && mouseY < y + ROW - 1)
                g.fill(listX, y - 1, listX + listW, y + ROW - 1, 0x20FFFFFF);
            if (r.has("line")) {  // contents search result: path:line  text
                String loc = r.get("name").getAsString() + ":" + r.get("line").getAsInt();
                String head = font.plainSubstrByWidth(loc, listW / 2);
                g.text(font, head, listX + 3, y, DIR);
                g.text(font, font.plainSubstrByWidth(ChatStyle.clean(r.get("text").getAsString()), listW - font.width(head) - 12),
                    listX + font.width(head) + 9, y, TEXT);
                continue;
            }
            boolean isDir = r.get("dir").getAsBoolean();
            boolean hidden = r.has("hidden") && r.get("hidden").getAsBoolean();
            String size = isDir ? "" : ChatStyle.size(r.get("size").getAsLong());
            int sizeW = font.width(size);
            String name = font.plainSubstrByWidth((isDir ? "📁 " : "📄 ") + r.get("name").getAsString() + (isDir ? "/" : ""),
                listW - sizeW - 12);
            g.text(font, name, listX + 3, y, hidden ? DIM : isDir ? DIR : TEXT);
            g.text(font, size, listX + listW - sizeW - 3, y, FAINT);
        }
        if (rows.size() > visible) {  // scrollbar
            int barH = Math.max(10, listH * visible / rows.size());
            int barY = listY + (listH - barH) * listScroll / Math.max(1, rows.size() - visible);
            g.fill(listX + listW - 1, barY, listX + listW + 1, barY + barH, 0x80FFFFFF);
        }
        String info = rows.size() + " " + L.t("объектов") + (dir.has("more") && dir.get("more").getAsInt() > 0
            ? " (+" + dir.get("more").getAsInt() + ")" : "") + (dir.get("git").getAsBoolean() ? "  ⇄ git" : "");
        g.text(font, info, listX + Math.min(listW, 180) + 98, 57, FAINT);
    }

    private void drawPreview(GuiGraphicsExtractor g) {
        JsonObject r = current();
        if (file == null || r == null || r.get("dir").getAsBoolean()) {
            String hint = r != null && r.get("dir").getAsBoolean()
                ? "📁 " + r.get("name").getAsString() + L.t(" — Enter или двойной клик, чтобы открыть")
                : L.t("Выбери файл — тут появится его содержимое");
            g.text(font, font.plainSubstrByWidth(hint, viewW - 8), viewX + 4, listY + 4, DIM);
            g.text(font, font.plainSubstrByWidth(L.t("↑↓ выбор · Enter открыть · Backspace вверх · F2 имя · Delete удалить"), viewW - 8),
                viewX + 4, listY + listH - 11, FAINT);
            return;
        }
        String name = new File(file.get("path").getAsString()).getName();
        String meta = ChatStyle.size(file.get("size").getAsLong()) + (file.get("binary").getAsBoolean() ? ""
            : " · " + file.get("total").getAsInt() + L.t(" строк"));
        if (r.has("mtime")) meta += " · " + new SimpleDateFormat("dd.MM.yyyy HH:mm").format(new Date(r.get("mtime").getAsLong() * 1000));
        g.text(font, "📄 " + font.plainSubstrByWidth(name, viewW - font.width(meta) - 30), viewX + 3, listY + 2, GOLD);
        g.text(font, meta, viewX + viewW - font.width(meta) - 3, listY + 2, FAINT);
        if (file.get("binary").getAsBoolean()) {
            g.text(font, L.t("Двоичный файл — открой «Проводник» или «Редактор»"), viewX + 4, listY + 18, DIM);
            return;
        }
        int visible = (listH - 14) / 9, y = listY + 14;
        int numW = font.width(String.valueOf(fileStart + fileLines.size())) + 6;
        for (int i = fileScroll; i < Math.min(fileLines.size(), fileScroll + visible); i++) {
            int number = fileStart + i;
            if (number == highlightLine) g.fill(viewX, y - 1, viewX + viewW, y + 8, 0x40F2C14E);
            g.text(font, String.valueOf(number), viewX + 3, y, number == highlightLine ? GOLD : FAINT);
            g.text(font, font.plainSubstrByWidth(ChatStyle.clean(fileLines.get(i)), viewW - numW - 8), viewX + 3 + numW, y, CODE);
            y += 9;
        }
    }
}
