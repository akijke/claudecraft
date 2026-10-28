package dev.claudechat;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.mojang.blaze3d.platform.InputConstants;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.Tooltip;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.input.CharacterEvent;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.client.input.MouseButtonEvent;
import net.minecraft.network.chat.Component;
import java.io.File;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.concurrent.CompletableFuture;

/**
 * In-game code editor (/edit <file>, "✎ Edit" in the file window). Syntax highlighting,
 * selection, clipboard, undo/redo, auto-indent, Ctrl+S. Saving goes through the bridge
 * (POST /save), keeps the file's line endings and refuses to overwrite a file that changed
 * on disk since it was opened (Claude may have edited it) unless confirmed.
 */
final class EditorScreen extends Screen {
    private static final int LINE = 10, GUTTER_PAD = 6;
    private final ClaudeChat mod;
    private final Screen parent;
    private String path;
    private final String lang;
    private final List<String> lines = new ArrayList<>();
    private int row, col, anchorRow = -1, anchorCol;
    private int scrollRow, scrollX;
    private boolean loaded, dirty, confirmClose, confirmOverwrite, dragging;
    private long mtime;
    private String eol = "\n";
    private String message = "";
    private int messageColor = 0xFF8A8A8A;
    private final Deque<State> undo = new ArrayDeque<>(), redo = new ArrayDeque<>();
    private int areaX, areaY, areaW, areaH, gutterW;

    private record State(List<String> lines, int row, int col) { }

    EditorScreen(ClaudeChat mod, Screen parent, String path) {
        super(Component.literal(new File(path).getName()));
        this.mod = mod;
        this.parent = parent;
        this.path = path;
        this.lang = Highlighter.language(new File(path).getName());
        lines.add("");
    }

    private int gotoLine;

    /** Opens on this 1-based line (0 = top). */
    EditorScreen at(int line) {
        gotoLine = line;
        if (loaded) jump();
        return this;
    }

    private void jump() {
        if (gotoLine <= 0) return;
        row = Math.min(lines.size() - 1, gotoLine - 1);
        col = lines.get(row).length() - lines.get(row).stripLeading().length();
        scrollRow = Math.max(0, row - Math.max(1, areaH / LINE) / 2);  // the line in the middle
        highlight = row;
        gotoLine = 0;
    }

    private int highlight = -1;

    // ---- folding, brackets, go to line, extra carets (#48) ----
    private final java.util.TreeMap<Integer, Integer> folds = new java.util.TreeMap<>();  // first line -> last hidden line
    private final List<int[]> carets = new ArrayList<>();  // extra carets: row, col (Ctrl+Alt+↑/↓, Alt+click)
    private net.minecraft.client.gui.components.EditBox gotoBox;
    private boolean goingTo;

    private boolean hiddenLine(int r) {
        var f = folds.lowerEntry(r);
        return f != null && r <= f.getValue();
    }

    private int stepVisible(int r, int dir) {
        int n = r + dir;
        while (n >= 0 && n < lines.size() && hiddenLine(n)) n += dir;
        return n < 0 || n >= lines.size() ? r : n;
    }

    private static int indentOf(String l) { return l.isBlank() ? Integer.MAX_VALUE : l.length() - l.stripLeading().length(); }

    /** Lines below `r` that belong to its block: deeper indentation (blank lines inside count too). */
    private int blockEnd(int r) {
        int base = indentOf(lines.get(r)), end = r;
        for (int i = r + 1; i < lines.size(); i++) {
            int ind = indentOf(lines.get(i));
            if (ind == Integer.MAX_VALUE) continue;
            if (ind <= base) break;
            end = i;
        }
        return end;
    }

    private void toggleFold(int r) {
        var inside = folds.floorEntry(r);
        if (inside != null && r <= inside.getValue()) { folds.remove(inside.getKey()); return; }
        int end = blockEnd(r);
        if (end > r) {
            folds.put(r, end);
            if (row > r && row <= end) { row = r; col = Math.min(col, lines.get(r).length()); anchorRow = -1; }
        }
    }

    /** {row, col} of the bracket matching the one at/just before the caret, plus that bracket; or null. */
    private int[] matchBracket() {
        String l = lines.get(row);
        String open = "([{", close = ")]}";
        for (int c : new int[]{col - 1, col}) {
            if (c < 0 || c >= l.length()) continue;
            char ch = l.charAt(c);
            int oi = open.indexOf(ch), ci = close.indexOf(ch);
            if (oi < 0 && ci < 0) continue;
            char want = oi >= 0 ? close.charAt(oi) : open.charAt(ci);
            int dir = oi >= 0 ? 1 : -1, depth = 0, r = row, x = c, limit = 5000;
            while (limit-- > 0) {
                String line = lines.get(r);
                if (x >= 0 && x < line.length()) {
                    char k = line.charAt(x);
                    if (k == ch) depth++;
                    else if (k == want && --depth == 0) return new int[]{r, x, row, c};
                }
                x += dir;
                if (x < 0 || x >= line.length()) {
                    r += dir;
                    if (r < 0 || r >= lines.size()) return null;
                    x = dir > 0 ? 0 : lines.get(r).length() - 1;
                }
            }
            return null;
        }
        return null;
    }

    /** Typing at the extra carets too (same text, rows below first so positions stay right). */
    private void typeAtCarets(String text) {
        carets.sort((a, b) -> b[0] - a[0]);
        for (int[] c : carets) {
            if (c[0] == row || c[0] >= lines.size()) continue;
            String l = lines.get(c[0]);
            int x = Math.min(c[1], l.length());
            lines.set(c[0], l.substring(0, x) + text + l.substring(x));
            c[1] = x + text.length();
        }
    }

    private void backspaceAtCarets() {
        for (int[] c : carets) {
            if (c[0] == row || c[0] >= lines.size()) continue;
            String l = lines.get(c[0]);
            int x = Math.min(c[1], l.length());
            if (x > 0) { lines.set(c[0], l.substring(0, x - 1) + l.substring(x)); c[1] = x - 1; }
        }
    }

    private void openGoto() {
        goingTo = true;
        rebuildWidgets();
        setFocused(gotoBox);
    }

    // ---- find & replace (Ctrl+F / Ctrl+H) ----
    private net.minecraft.client.gui.components.EditBox findBox, replaceBox;
    private boolean finding, replacing, findRegex, findCase;
    private String findValue = "", replaceValue = "";
    private final List<int[]> matches = new ArrayList<>();  // row, start, end
    private int matchIndex = -1;

    private void openFind(boolean withReplace) {
        finding = true;
        replacing = withReplace || replacing;
        if (hasSelection() && selection()[0] == selection()[2]) findValue = selectedText();
        rebuildWidgets();
        setFocused(findBox);
    }

    private void closeFind() {
        finding = replacing = false;
        matches.clear();
        rebuildWidgets();
    }

    private java.util.regex.Pattern findPattern() {
        if (findValue.isEmpty()) return null;
        try {
            int flags = findCase ? 0 : java.util.regex.Pattern.CASE_INSENSITIVE | java.util.regex.Pattern.UNICODE_CASE;
            return java.util.regex.Pattern.compile(findRegex ? findValue : java.util.regex.Pattern.quote(findValue), flags);
        } catch (java.util.regex.PatternSyntaxException e) {
            say(L.t("Неверное регулярное выражение"), 0xFFF0626A);
            return null;
        }
    }

    private void refreshMatches() {
        matches.clear();
        java.util.regex.Pattern p = findPattern();
        if (p == null) { matchIndex = -1; return; }
        for (int r = 0; r < lines.size(); r++) {
            java.util.regex.Matcher m = p.matcher(lines.get(r));
            while (m.find()) {
                if (m.end() == m.start()) { if (m.end() >= lines.get(r).length()) break; continue; }
                matches.add(new int[]{r, m.start(), m.end()});
            }
        }
        matchIndex = -1;
        say(matches.isEmpty() ? L.t("Не найдено") : L.t("Найдено: ") + matches.size(), matches.isEmpty() ? 0xFFF2C14E : 0xFF8A8A8A);
    }

    private void nextMatch(int dir) {
        if (matches.isEmpty()) return;
        if (matchIndex < 0) {  // first jump: the match after the cursor
            matchIndex = 0;
            for (int i = 0; i < matches.size(); i++) {
                int[] m = matches.get(i);
                if (m[0] > row || (m[0] == row && m[1] >= col)) { matchIndex = i; break; }
            }
            if (dir < 0) matchIndex = (matchIndex - 1 + matches.size()) % matches.size();
        } else matchIndex = (matchIndex + dir + matches.size()) % matches.size();
        int[] m = matches.get(matchIndex);
        anchorRow = m[0]; anchorCol = m[1]; row = m[0]; col = m[2];
        reveal();
        say((matchIndex + 1) + " / " + matches.size(), 0xFF8A8A8A);
    }

    private String replacementFor(String found) {
        if (!findRegex) return replaceValue;
        java.util.regex.Pattern p = findPattern();
        return p == null ? replaceValue : p.matcher(found).replaceFirst(replaceValue);
    }

    private void replaceOne() {
        if (matchIndex < 0 || matchIndex >= matches.size()) { nextMatch(1); return; }
        int[] m = matches.get(matchIndex);
        snapshot();
        String l = lines.get(m[0]);
        String rep = replacementFor(l.substring(m[1], m[2]));
        lines.set(m[0], l.substring(0, m[1]) + rep + l.substring(m[2]));
        row = m[0]; col = m[1] + rep.length(); anchorRow = -1;
        int keep = matchIndex;
        refreshMatches();
        matchIndex = Math.min(keep, matches.size()) - 1;
        nextMatch(1);
    }

    private void replaceAll() {
        java.util.regex.Pattern p = findPattern();
        if (p == null || matches.isEmpty()) return;
        snapshot();
        int n = matches.size();
        for (int r = 0; r < lines.size(); r++) {
            java.util.regex.Matcher m = p.matcher(lines.get(r));
            lines.set(r, findRegex ? m.replaceAll(replaceValue) : m.replaceAll(java.util.regex.Matcher.quoteReplacement(replaceValue)));
        }
        refreshMatches();
        say(L.t("Заменено: ") + n, 0xFF6BD08B);
    }

    int foldsForTest() { return folds.size(); }
    int diagnosticsForTest() { return diagnostics.size(); }
    int completionsForTest() { return completions.size(); }
    void caretForTest(int r, int c) { row = r; col = c; anchorRow = -1; }
    int caretsForTest() { return carets.size(); }

    String textForTest() { return String.join("\n", lines); }

    /** Dev/live tests: find and replace without the keyboard. */
    void findForTest(String find, String replace, boolean all) {
        openFind(true);
        findBox.setValue(find);
        replaceBox.setValue(replace);
        if (all) replaceAll(); else { nextMatch(1); replaceOne(); }
    }
    int cursorRow() { return row; }
    boolean isLoaded() { return loaded; }

    /** Dev screenshots: fixed text, no bridge. */
    EditorScreen withText(String text) {
        lines.clear();
        lines.addAll(List.of(text.split("\n", -1)));
        loaded = true;
        jump();
        return this;
    }

    // ---- io ---------------------------------------------------------------
    private void load() {
        CompletableFuture.supplyAsync(() -> {
            try { return mod.request("/edit?mode=cc&path=" + URLEncoder.encode(path, StandardCharsets.UTF_8), null); }
            catch (Exception e) { JsonObject o = new JsonObject(); o.addProperty("error", e.getMessage()); return o; }
        }, mod.io).thenAccept(r -> minecraft.execute(() -> {
            if (r.has("error")) { say(r.get("error").getAsString(), 0xFFF0626A); return; }
            lines.clear();
            lines.addAll(List.of(r.get("text").getAsString().split("\n", -1)));
            if (lines.size() > 1 && lines.get(lines.size() - 1).isEmpty() && r.get("text").getAsString().endsWith("\n"))
                lines.remove(lines.size() - 1);  // trailing newline is restored on save
            mtime = r.get("mtime").getAsLong();
            eol = r.get("eol").getAsString();
            loaded = true;
            dirty = false;
            say(L.t("Открыт") + " · " + lines.size() + L.t(" строк"), 0xFF8A8A8A);
            lspDue = 1;  // first diagnostics as soon as possible
            path = r.get("path").getAsString();  // the bridge resolved it (relative → project)
            jump();
        }));
    }

    private void save(boolean force) {
        if (!loaded) return;
        JsonObject body = new JsonObject();
        body.addProperty("mode", "cc");
        body.addProperty("path", path);
        body.addProperty("text", String.join("\n", lines) + "\n");
        body.addProperty("mtime", mtime);
        body.addProperty("eol", eol);
        body.addProperty("force", force);
        CompletableFuture.supplyAsync(() -> {
            try { return mod.request("/save", body); }
            catch (Exception e) { JsonObject o = new JsonObject(); o.addProperty("error", e.getMessage()); return o; }
        }, mod.io).thenAccept(r -> minecraft.execute(() -> {
            if (r.has("error")) {
                String err = r.get("error").getAsString();
                confirmOverwrite = err.contains("изменился") || err.contains("changed on disk");
                say(err + (confirmOverwrite ? "  — Ctrl+S " + L.t("ещё раз, чтобы перезаписать") : ""), 0xFFF2C14E);
                return;
            }
            mtime = r.get("mtime").getAsLong();
            dirty = false;
            confirmOverwrite = false;
            say("✔ " + L.t("Сохранено"), 0xFF6BD08B);
        }));
    }

    private void say(String text, int color) { message = text; messageColor = color; }

    // ---- language server (#50): diagnostics, F12 definition, Ctrl+Space completion ----
    private JsonArray diagnostics = new JsonArray();
    private boolean lspOff;          // no server for this file type: stop asking
    private long lspDue;             // when to send the text again (after typing stops)
    private boolean lspBusy;
    private List<JsonObject> completions = new ArrayList<>();
    private int completionIndex;
    private String lspServer = "";

    private JsonObject lspBody() {
        JsonObject body = new JsonObject();
        body.addProperty("mode", "cc");
        body.addProperty("path", path);
        body.addProperty("text", String.join("\n", lines) + "\n");
        body.addProperty("line", row);
        body.addProperty("col", col);
        return body;
    }

    private void lspCall(String op, java.util.function.Consumer<JsonObject> done) {
        if (lspOff || !loaded) return;
        JsonObject body = lspBody();
        CompletableFuture.supplyAsync(() -> {
            try { return mod.request("/lsp/" + op, body); }
            catch (Exception e) { JsonObject o = new JsonObject(); o.addProperty("error", e.getMessage()); return o; }
        }, mod.io).thenAccept(r -> minecraft.execute(() -> {
            lspBusy = false;
            if (r.has("error")) {
                String err = r.get("error").getAsString();
                if (err.contains("языкового сервера") || err.contains("language server")) {
                    lspOff = true;
                    if (op.equals("sync")) return;  // quietly: the hint is shown on F12 / Ctrl+Space
                }
                say(err, 0xFFF2C14E);
                return;
            }
            done.accept(r);
        }));
    }

    private void lspSync() {
        lspBusy = true;
        lspCall("sync", r -> { diagnostics = r.getAsJsonArray("diagnostics"); lspServer = r.get("server").getAsString(); });
    }

    private void lspTick() {
        if (!lspOff && loaded && !lspBusy && lspDue > 0 && System.currentTimeMillis() >= lspDue) { lspDue = 0; lspSync(); }
    }

    private void nextDiagnostic() {
        if (diagnostics.isEmpty()) { say(L.t("Ошибок нет") + (lspServer.isEmpty() ? "" : " · " + lspServer), 0xFF6BD08B); return; }
        JsonObject best = null, first = null;
        for (JsonElement e : diagnostics) {
            JsonObject d = e.getAsJsonObject();
            if (first == null) first = d;
            int l = d.get("line").getAsInt(), c = d.get("col").getAsInt();
            if (best == null && (l > row || (l == row && c > col))) best = d;
        }
        JsonObject d = best != null ? best : first;
        row = Math.min(d.get("line").getAsInt(), lines.size() - 1);
        col = Math.min(d.get("col").getAsInt(), lines.get(row).length());
        anchorRow = -1;
        reveal();
        say((d.get("severity").getAsInt() == 1 ? "✖ " : "⚠ ") + d.get("message").getAsString(), d.get("severity").getAsInt() == 1 ? 0xFFF0626A : 0xFFF2C14E);
    }

    private void goToDefinition() {
        if (lspOff) { lspOff = false; }  // ask once more: the reply explains what to install
        lspCall("definition", r -> {
            JsonArray locs = r.getAsJsonArray("locations");
            if (locs.isEmpty()) { say(L.t("Определение не найдено"), 0xFF8A8A8A); return; }
            JsonObject loc = locs.get(0).getAsJsonObject();
            String target = loc.get("path").getAsString();
            int line = loc.get("line").getAsInt();
            if (new File(target).getAbsoluteFile().equals(new File(path).getAbsoluteFile())) { gotoLine = line; jump(); }
            else minecraft.setScreenAndShow(new EditorScreen(mod, this, target).at(line));
        });
    }

    private void complete() {
        if (lspOff) { lspOff = false; }
        lspCall("complete", r -> {
            completions = new ArrayList<>();
            for (JsonElement e : r.getAsJsonArray("items")) completions.add(e.getAsJsonObject());
            completionIndex = 0;
            if (completions.isEmpty()) say(L.t("Нет вариантов"), 0xFF8A8A8A);
        });
    }

    private void acceptCompletion() {
        JsonObject item = completions.get(completionIndex);
        completions = new ArrayList<>();
        String l = lines.get(row);
        int start = col;
        while (start > 0 && (Character.isLetterOrDigit(l.charAt(start - 1)) || l.charAt(start - 1) == '_')) start--;
        snapshot();
        String insert = item.get("insert").getAsString();
        lines.set(row, l.substring(0, start) + insert + l.substring(col));
        col = start + insert.length();
        lspDue = System.currentTimeMillis() + 300;
    }

    /** Ctrl+Shift+F: the installed formatter (ruff/black, prettier, google-java-format, gofmt…) via the bridge. */
    private void format() {
        if (!loaded) return;
        JsonObject body = new JsonObject();
        body.addProperty("mode", "cc");
        body.addProperty("path", path);
        body.addProperty("text", String.join("\n", lines) + "\n");
        say(L.t("Форматирую…"), 0xFF8A8A8A);
        CompletableFuture.supplyAsync(() -> {
            try { return mod.request("/format", body); }
            catch (Exception e) { JsonObject o = new JsonObject(); o.addProperty("error", e.getMessage()); return o; }
        }, mod.io).thenAccept(r -> minecraft.execute(() -> {
            if (r.has("error")) { say(r.get("error").getAsString(), 0xFFF2C14E); return; }
            String text = r.get("text").getAsString();
            List<String> formatted = new ArrayList<>(List.of(text.split("\n", -1)));
            if (formatted.size() > 1 && formatted.get(formatted.size() - 1).isEmpty()) formatted.remove(formatted.size() - 1);
            if (formatted.equals(lines)) { say(L.t("Уже отформатировано") + " (" + r.get("tool").getAsString() + ")", 0xFF8A8A8A); return; }
            snapshot();  // Ctrl+Z brings the old text back
            lines.clear();
            lines.addAll(formatted);
            row = Math.min(row, lines.size() - 1);
            col = Math.min(col, lines.get(row).length());
            anchorRow = -1;
            say("✔ " + L.t("Отформатировано: ") + r.get("tool").getAsString() + L.t(" · Ctrl+S — сохранить"), 0xFF6BD08B);
        }));
    }

    // ---- layout -------------------------------------------------------------
    @Override protected void init() {
        areaX = 6;
        areaY = finding ? 52 : 30;
        areaW = width - 12;
        areaH = height - areaY - 18;
        if (finding) {
            int bw = Math.min(200, (width - 200) / 2);
            findBox = new net.minecraft.client.gui.components.EditBox(font, 6, 29, bw, 18, Component.literal("find"));
            findBox.setHint(Component.literal(L.t("найти… (Enter — дальше)")));
            findBox.setMaxLength(1000);
            findBox.setValue(findValue);
            findBox.setResponder(v -> { findValue = v; refreshMatches(); });
            addRenderableWidget(findBox);
            int x = 10 + bw;
            addRenderableWidget(Button.builder(Component.literal(findRegex ? "[.*]" : ".*"), b -> {
                findRegex = !findRegex; refreshMatches(); rebuildWidgets(); }).bounds(x, 29, 26, 18)
                .tooltip(Tooltip.create(Component.literal(L.t("Регулярное выражение")))).build());
            addRenderableWidget(Button.builder(Component.literal(findCase ? "[Aa]" : "Aa"), b -> {
                findCase = !findCase; refreshMatches(); rebuildWidgets(); }).bounds(x + 28, 29, 26, 18)
                .tooltip(Tooltip.create(Component.literal(L.t("Учитывать регистр")))).build());
            x += 58;
            if (replacing) {
                replaceBox = new net.minecraft.client.gui.components.EditBox(font, x, 29, bw, 18, Component.literal("replace"));
                replaceBox.setHint(Component.literal(L.t("заменить на…") + (findRegex ? " ($1)" : "")));
                replaceBox.setMaxLength(1000);
                replaceBox.setValue(replaceValue);
                replaceBox.setResponder(v -> replaceValue = v);
                addRenderableWidget(replaceBox);
                x += bw + 4;
                addRenderableWidget(Button.builder(Component.literal(L.t("Заменить")), b -> replaceOne()).bounds(x, 29, 60, 18).build());
                addRenderableWidget(Button.builder(Component.literal(L.t("Все")), b -> replaceAll()).bounds(x + 62, 29, 34, 18).build());
                x += 98;
            }
            addRenderableWidget(Button.builder(Component.literal("✕"), b -> closeFind()).bounds(x, 29, 18, 18).build());
            refreshMatches();
        }
        int x = width - 6;
        x = button(x, "✕", L.t("Закрыть (Esc)"), this::onClose);
        x = button(x, "→ Claude", L.t("Спросить Claude про этот файл"), () -> {
            ConversationScreen.prefill("cc", L.t("Посмотри ") + path + ": ");
            minecraft.setScreenAndShow(new ConversationScreen(mod));
        });
        x = button(x, "💾 " + L.t("Сохранить"), "Ctrl+S", () -> save(confirmOverwrite));
        button(x, "{ }", L.t("Форматировать (Ctrl+Shift+F)"), this::format);
        if (goingTo) {
            gotoBox = new net.minecraft.client.gui.components.EditBox(font, 6, 29, 120, 18, Component.literal("line"));
            gotoBox.setHint(Component.literal(L.t("строка… (Enter)")));
            gotoBox.setMaxLength(7);
            addRenderableWidget(gotoBox);
            areaY = Math.max(areaY, 52);
            areaH = height - areaY - 18;
        }
        if (!loaded) load();
    }

    private int button(int right, String label, String tip, Runnable action) {
        int w = font.width(label) + 12;
        addRenderableWidget(Button.builder(Component.literal(label), b -> action.run())
            .bounds(right - w, 5, w, 18).tooltip(Tooltip.create(Component.literal(tip))).build());
        return right - w - 3;
    }

    @Override public boolean isPauseScreen() { return false; }

    @Override public void onClose() {
        if (dirty && !confirmClose) {
            confirmClose = true;
            say(L.t("Есть несохранённые правки: Esc ещё раз — закрыть без сохранения, Ctrl+S — сохранить"), 0xFFF2C14E);
            return;
        }
        minecraft.setScreenAndShow(parent);
    }

    // ---- editing --------------------------------------------------------------
    private void snapshot() {
        folds.clear();
        lspDue = System.currentTimeMillis() + 800;
        undo.push(new State(new ArrayList<>(lines), row, col));
        if (undo.size() > 300) undo.removeLast();
        redo.clear();
        dirty = true;
        confirmClose = false;
    }

    private boolean hasSelection() { return anchorRow >= 0 && (anchorRow != row || anchorCol != col); }

    private int[] selection() {  // r0, c0, r1, c1 ordered
        boolean forward = anchorRow < row || (anchorRow == row && anchorCol <= col);
        return forward ? new int[]{anchorRow, anchorCol, row, col} : new int[]{row, col, anchorRow, anchorCol};
    }

    private String selectedText() {
        int[] s = selection();
        if (s[0] == s[2]) return lines.get(s[0]).substring(s[1], s[3]);
        StringBuilder b = new StringBuilder(lines.get(s[0]).substring(s[1]));
        for (int r = s[0] + 1; r < s[2]; r++) b.append('\n').append(lines.get(r));
        return b.append('\n').append(lines.get(s[2]), 0, s[3]).toString();
    }

    private void deleteSelection() {
        int[] s = selection();
        String head = lines.get(s[0]).substring(0, s[1]), tail = lines.get(s[2]).substring(s[3]);
        for (int r = s[2]; r > s[0]; r--) lines.remove(r);
        lines.set(s[0], head + tail);
        row = s[0];
        col = s[1];
        anchorRow = -1;
    }

    private void insert(String text) {
        snapshot();
        if (hasSelection()) deleteSelection();
        anchorRow = -1;
        String line = lines.get(row), head = line.substring(0, col), tail = line.substring(col);
        String[] parts = text.replace("\r\n", "\n").replace("\t", "    ").split("\n", -1);
        if (parts.length == 1) {
            lines.set(row, head + parts[0] + tail);
            col += parts[0].length();
        } else {
            lines.set(row, head + parts[0]);
            for (int i = 1; i < parts.length; i++) lines.add(row + i, parts[i]);
            row += parts.length - 1;
            col = parts[parts.length - 1].length();
            lines.set(row, lines.get(row) + tail);
        }
        reveal();
    }

    private void newline() {
        String line = lines.get(row);
        String indent = line.substring(0, line.length() - line.stripLeading().length());
        if (col > 0 && "{[(:".indexOf(line.charAt(Math.min(col, line.length()) - 1)) >= 0) indent += "    ";
        insert("\n" + indent);
    }

    private void backspace(boolean forward) {
        if (hasSelection()) { snapshot(); deleteSelection(); reveal(); return; }
        String line = lines.get(row);
        if (!forward && col > 0 && col < line.length() && "()[]{}\"\"''".contains(line.substring(col - 1, col + 1))
                && "([{\"'".indexOf(line.charAt(col - 1)) == ")]}\"'".indexOf(line.charAt(col))) {
            snapshot();  // empty pair: delete both
            lines.set(row, line.substring(0, col - 1) + line.substring(col + 1));
            col--;
            return;
        }
        if (!forward && !carets.isEmpty()) backspaceAtCarets();
        if (!forward && col == 0 && row == 0) return;
        if (forward && col == line.length() && row == lines.size() - 1) return;
        snapshot();
        if (!forward && col == 0) {
            col = lines.get(row - 1).length();
            lines.set(row - 1, lines.get(row - 1) + line);
            lines.remove(row--);
        } else if (forward && col == line.length()) {
            lines.set(row, line + lines.remove(row + 1));
        } else if (forward) {
            lines.set(row, line.substring(0, col) + line.substring(col + 1));
        } else {
            // remove a whole 4-space indent step at once
            int n = col >= 4 && line.substring(col - 4, col).isBlank() && line.substring(0, col).isBlank() ? 4 : 1;
            lines.set(row, line.substring(0, col - n) + line.substring(col));
            col -= n;
        }
        reveal();
    }

    private void indent(boolean out) {
        snapshot();
        int r0 = hasSelection() ? selection()[0] : row, r1 = hasSelection() ? selection()[2] : row;
        for (int r = r0; r <= r1; r++) {
            String l = lines.get(r);
            if (out) {
                int n = Math.min(4, l.length() - l.stripLeading().length());
                lines.set(r, l.substring(n));
                if (r == row) col = Math.max(0, col - n);
            } else {
                lines.set(r, "    " + l);
                if (r == row) col += 4;
            }
        }
    }

    private void move(int dRow, int newCol, boolean select) {
        if (select && anchorRow < 0) { anchorRow = row; anchorCol = col; }
        if (!select) anchorRow = -1;
        int target = row;
        for (int i = 0; i < Math.abs(dRow); i++) target = stepVisible(target, dRow > 0 ? 1 : -1);
        if (dRow != 0 && target == row) target = Math.max(0, Math.min(lines.size() - 1, row + dRow > row ? lines.size() - 1 : 0));
        row = hiddenLine(target) ? folds.lowerKey(target) : target;
        col = Math.max(0, Math.min(lines.get(row).length(), newCol));
        reveal();
    }

    private int wordJump(int dir) {
        String l = lines.get(row);
        int c = col;
        if (dir < 0) {
            while (c > 0 && !Character.isLetterOrDigit(l.charAt(c - 1))) c--;
            while (c > 0 && Character.isLetterOrDigit(l.charAt(c - 1))) c--;
        } else {
            while (c < l.length() && !Character.isLetterOrDigit(l.charAt(c))) c++;
            while (c < l.length() && Character.isLetterOrDigit(l.charAt(c))) c++;
        }
        return c;
    }

    private void reveal() {
        int visible = Math.max(1, areaH / LINE);
        if (hiddenLine(row)) {  // the caret went into a fold: open it
            folds.remove(folds.lowerKey(row));
        }
        if (row < scrollRow) scrollRow = row;
        int shown = 0;
        for (int r = scrollRow; r < row; r = stepVisible(r, 1)) { shown++; if (stepVisible(r, 1) == r) break; }
        while (shown >= visible) { scrollRow = stepVisible(scrollRow, 1); shown--; }
        int x = font.width(lines.get(row).substring(0, Math.min(col, lines.get(row).length())));
        int textW = areaW - gutterW - 8;
        if (x < scrollX) scrollX = Math.max(0, x - 40);
        if (x > scrollX + textW - 10) scrollX = x - textW + 40;
    }

    // ---- input ----------------------------------------------------------------
    @Override public boolean charTyped(CharacterEvent event) {
        if (finding && (getFocused() == findBox || (replaceBox != null && getFocused() == replaceBox))) return super.charTyped(event);
        if (goingTo && getFocused() == gotoBox) return super.charTyped(event);
        if (!loaded || event.codepoint() < 32) return super.charTyped(event);
        String ch = event.codepointAsString();
        String line = lines.get(row);
        char next = col < line.length() ? line.charAt(col) : 0;
        if (carets.isEmpty() && !hasSelection()) {
            if (")]}\"'".contains(ch) && next == ch.charAt(0)) { col++; return true; }  // type over the closing char
            String pairs = "([{\"'", closers = ")]}\"'";
            int pi = pairs.indexOf(ch);
            char prev = col > 0 ? line.charAt(col - 1) : ' ';
            boolean quote = pi >= 3;
            if (pi >= 0 && (next == 0 || Character.isWhitespace(next) || ")]},;".indexOf(next) >= 0)
                    && !(quote && (Character.isLetterOrDigit(prev) || prev == ch.charAt(0)))) {
                insert(ch + closers.charAt(pi));
                col--;
                return true;
            }
        }
        insert(ch);
        if (!carets.isEmpty()) typeAtCarets(ch);
        return true;
    }

    @Override public boolean keyPressed(KeyEvent event) {
        if (PaletteScreen.handleKey(mod, this, event, true)) return true;  // Ctrl+Shift+P / Ctrl+P
        int key = event.key();
        boolean ctrl = (event.modifiers() & 2) != 0, shift = (event.modifiers() & 1) != 0;
        boolean inFind = finding && (getFocused() == findBox || (replaceBox != null && getFocused() == replaceBox));
        boolean alt = (event.modifiers() & 4) != 0;
        if (goingTo && getFocused() == gotoBox) {
            if (key == InputConstants.KEY_RETURN) {
                try { gotoLine = Integer.parseInt(gotoBox.getValue().strip()); jump(); } catch (NumberFormatException ignored) { }
                goingTo = false; rebuildWidgets(); setFocused(null); return true;
            }
            if (key == InputConstants.KEY_ESCAPE) { goingTo = false; rebuildWidgets(); return true; }
            return super.keyPressed(event);
        }
        if (key == InputConstants.KEY_ESCAPE && !completions.isEmpty()) { completions = new ArrayList<>(); return true; }
        if (key == InputConstants.KEY_ESCAPE && !carets.isEmpty()) { carets.clear(); return true; }
        if (key == InputConstants.KEY_ESCAPE) { if (finding) closeFind(); else onClose(); return true; }
        if (!completions.isEmpty()) {
            switch (key) {
                case InputConstants.KEY_DOWN -> { completionIndex = (completionIndex + 1) % completions.size(); return true; }
                case InputConstants.KEY_UP -> { completionIndex = (completionIndex - 1 + completions.size()) % completions.size(); return true; }
                case InputConstants.KEY_RETURN, InputConstants.KEY_TAB -> { acceptCompletion(); return true; }
                case InputConstants.KEY_ESCAPE -> { completions = new ArrayList<>(); return true; }
                default -> completions = new ArrayList<>();
            }
        }
        if (key == InputConstants.KEY_F8) { nextDiagnostic(); return true; }
        if (key == InputConstants.KEY_F12) { goToDefinition(); return true; }
        if (ctrl && key == InputConstants.KEY_SPACE) { complete(); return true; }
        if (ctrl && key == InputConstants.KEY_G) { openGoto(); return true; }
        if (ctrl && shift && key == InputConstants.KEY_F) { format(); return true; }
        if (ctrl && shift && key == InputConstants.KEY_LBRACKET) { toggleFold(row); return true; }
        if (ctrl && shift && key == InputConstants.KEY_RBRACKET) { folds.clear(); return true; }
        if (ctrl && alt && (key == InputConstants.KEY_UP || key == InputConstants.KEY_DOWN)) {
            int base = carets.isEmpty() ? row : carets.get(carets.size() - 1)[0];
            int r = key == InputConstants.KEY_DOWN ? base + 1 : base - 1;
            if (r >= 0 && r < lines.size() && r != row) carets.add(new int[]{r, col});
            return true;
        }
        if (!loaded) return super.keyPressed(event);
        if (ctrl && !shift && key == InputConstants.KEY_F) { openFind(false); return true; }
        if (ctrl && key == InputConstants.KEY_H) { openFind(true); return true; }
        if (key == InputConstants.KEY_F3 || (inFind && key == InputConstants.KEY_RETURN)) {
            if (inFind && getFocused() == replaceBox) replaceOne(); else nextMatch(shift ? -1 : 1);
            return true;
        }
        if (inFind) return super.keyPressed(event);
        if (!carets.isEmpty() && !ctrl && (key == InputConstants.KEY_HOME || key == InputConstants.KEY_END
                || key == InputConstants.KEY_LEFT || key == InputConstants.KEY_RIGHT)) {
            for (int[] c : carets) {  // extra carets follow horizontal moves
                String l = lines.get(Math.min(c[0], lines.size() - 1));
                int first = l.length() - l.stripLeading().length();
                c[1] = switch (key) {
                    case InputConstants.KEY_HOME -> c[1] == first ? 0 : first;
                    case InputConstants.KEY_END -> l.length();
                    case InputConstants.KEY_LEFT -> Math.max(0, c[1] - 1);
                    default -> Math.min(l.length(), c[1] + 1);
                };
            }
        }
        if (ctrl) {
            switch (key) {
                case InputConstants.KEY_S -> { save(confirmOverwrite); return true; }
                case InputConstants.KEY_A -> { anchorRow = 0; anchorCol = 0; row = lines.size() - 1; col = lines.get(row).length(); return true; }
                case InputConstants.KEY_C -> { if (hasSelection()) minecraft.keyboardHandler.setClipboard(selectedText()); return true; }
                case InputConstants.KEY_X -> {
                    if (hasSelection()) { minecraft.keyboardHandler.setClipboard(selectedText()); snapshot(); deleteSelection(); }
                    return true;
                }
                case InputConstants.KEY_V -> { insert(minecraft.keyboardHandler.getClipboard()); return true; }
                case InputConstants.KEY_Z -> { swap(undo, redo); return true; }
                case InputConstants.KEY_Y -> { swap(redo, undo); return true; }
                case InputConstants.KEY_D -> {  // duplicate line
                    snapshot(); lines.add(row + 1, lines.get(row)); row++; reveal(); return true;
                }
                case InputConstants.KEY_LEFT -> { move(0, wordJump(-1), shift); return true; }
                case InputConstants.KEY_RIGHT -> { move(0, wordJump(1), shift); return true; }
                case InputConstants.KEY_HOME -> { move(-row, 0, shift); return true; }
                case InputConstants.KEY_END -> { move(lines.size(), Integer.MAX_VALUE, shift); return true; }
                default -> { }
            }
        }
        switch (key) {
            case InputConstants.KEY_LEFT -> {
                if (col == 0 && row > 0) move(-1, Integer.MAX_VALUE, shift); else move(0, col - 1, shift);
                return true;
            }
            case InputConstants.KEY_RIGHT -> {
                if (col == lines.get(row).length() && row < lines.size() - 1) move(1, 0, shift); else move(0, col + 1, shift);
                return true;
            }
            case InputConstants.KEY_UP -> { move(-1, col, shift); return true; }
            case InputConstants.KEY_DOWN -> { move(1, col, shift); return true; }
            case InputConstants.KEY_PAGEUP -> { move(-areaH / LINE, col, shift); return true; }
            case InputConstants.KEY_PAGEDOWN -> { move(areaH / LINE, col, shift); return true; }
            case InputConstants.KEY_HOME -> {
                String l = lines.get(row);
                int first = l.length() - l.stripLeading().length();
                move(0, col == first ? 0 : first, shift);  // smart home
                return true;
            }
            case InputConstants.KEY_END -> { move(0, Integer.MAX_VALUE, shift); return true; }
            case InputConstants.KEY_RETURN -> { newline(); return true; }
            case InputConstants.KEY_BACKSPACE -> { backspace(false); return true; }
            case InputConstants.KEY_DELETE -> { backspace(true); return true; }
            case InputConstants.KEY_TAB -> { if (shift || hasSelection()) indent(shift); else insert("    "); return true; }
            default -> { return super.keyPressed(event); }
        }
    }

    private void swap(Deque<State> from, Deque<State> to) {
        if (from.isEmpty()) return;
        to.push(new State(new ArrayList<>(lines), row, col));
        State s = from.pop();
        lines.clear();
        lines.addAll(s.lines());
        row = Math.min(s.row(), lines.size() - 1);
        col = Math.min(s.col(), lines.get(row).length());
        anchorRow = -1;
        dirty = true;
        reveal();
    }

    private int[] positionAt(double x, double y) {
        int r = Math.max(0, Math.min(lines.size() - 1, scrollRow));
        for (int i = (int) ((y - areaY - 2) / LINE); i > 0; i--) r = stepVisible(r, 1);
        String l = lines.get(r);
        int target = (int) x - (areaX + gutterW + 4) + scrollX, c = 0;
        while (c < l.length() && font.width(l.substring(0, c + 1)) - font.width(String.valueOf(l.charAt(c))) / 2 <= target) c++;
        return new int[]{r, c};
    }

    @Override public boolean mouseClicked(MouseButtonEvent event, boolean doubleClick) {
        if (event.x() >= areaX && event.x() < areaX + areaW && event.y() >= areaY && event.y() < areaY + areaH && loaded) {
            int[] p = positionAt(event.x(), event.y());
            boolean shift = (event.modifiers() & 1) != 0;
            if ((event.modifiers() & 4) != 0) { carets.add(new int[]{p[0], p[1]}); return true; }  // Alt+click
            if (event.x() < areaX + gutterW) { toggleFold(p[0]); return true; }                    // gutter: fold
            carets.clear();
            if (shift && anchorRow < 0) { anchorRow = row; anchorCol = col; }
            if (!shift) { anchorRow = p[0]; anchorCol = p[1]; }
            row = p[0];
            col = p[1];
            if (doubleClick) {  // select word
                col = wordJump(1);
                int end = col;
                col = p[1];
                anchorRow = row; anchorCol = wordJump(-1); col = end;
            }
            dragging = true;
            setFocused(null);
            return true;
        }
        return super.mouseClicked(event, doubleClick);
    }

    @Override public boolean mouseDragged(MouseButtonEvent event, double dx, double dy) {
        if (dragging) {
            int[] p = positionAt(event.x(), event.y());
            row = p[0];
            col = p[1];
            reveal();
            return true;
        }
        return super.mouseDragged(event, dx, dy);
    }

    @Override public boolean mouseReleased(MouseButtonEvent event) {
        dragging = false;
        if (!hasSelection()) anchorRow = -1;
        return super.mouseReleased(event);
    }

    @Override public boolean mouseScrolled(double x, double y, double h, double v) {
        if ((x >= areaX && x < areaX + areaW) || true) {
            if (h != 0) scrollX = Math.max(0, scrollX - (int) (h * 20));
            scrollRow = Math.max(0, Math.min(Math.max(0, lines.size() - areaH / LINE), scrollRow - (int) Math.signum(v) * 3));
            return true;
        }
        return super.mouseScrolled(x, y, h, v);
    }

    // ---- render ---------------------------------------------------------------
    @Override public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float delta) {
        lspTick();
        g.fill(0, 0, width, height, Theme.SCREEN_BG);
        super.extractRenderState(g, mouseX, mouseY, delta);
        String title = "✎ " + new File(path).getName() + (dirty ? " ●" : "");
        g.text(font, Component.literal(title).withColor(dirty ? 0xF2C14E : 0xD97757), 6, 10, 0xFFFFFFFF);
        g.text(font, font.plainSubstrByWidth(path, width / 2 - font.width(title)), 14 + font.width(title), 10, 0xFF5E5E5E);

        gutterW = font.width(String.valueOf(lines.size())) + GUTTER_PAD * 2;
        g.fill(areaX, areaY, areaX + areaW, areaY + areaH, Theme.PANEL);
        g.fill(areaX, areaY, areaX + gutterW, areaY + areaH, Theme.GUTTER);
        int visible = areaH / LINE, textX = areaX + gutterW + 4;
        boolean[] block = {false};
        // block-comment state for the lines above the view
        for (int r = Math.max(0, scrollRow - 200); r < scrollRow; r++) Highlighter.line(lines.get(r), lang, block);
        int[] sel = hasSelection() ? selection() : null;
        int[] bracket = loaded ? matchBracket() : null;
        int r = Math.min(scrollRow, Math.max(0, lines.size() - 1)) - 1;
        for (int i = 0; i < visible; i++) {
            int nextR = r < 0 ? 0 : stepVisible(r, 1);
            if (r >= 0 && nextR == r) break;
            r = nextR;
            if (r >= lines.size()) break;
            int y = areaY + 2 + i * LINE;
            String text = lines.get(r);
            if (r == row) g.fill(areaX + gutterW, y - 1, areaX + areaW, y + LINE - 1, Theme.CURRENT_LINE);
            if (r == highlight) g.fill(areaX + gutterW, y - 1, areaX + areaW, y + LINE - 1, 0x40F2C14E);
            String number = String.valueOf(r + 1);
            g.text(font, number, areaX + gutterW - GUTTER_PAD - font.width(number), y, r == row ? Theme.LINE_NUMBER_ON : Theme.LINE_NUMBER);
            g.enableScissor(textX, areaY, areaX + areaW, areaY + areaH);
            for (int[] m : matches) {
                if (m[0] != r || m[1] > text.length()) continue;
                int mx0 = textX - scrollX + font.width(text.substring(0, m[1]));
                int mx1 = textX - scrollX + font.width(text.substring(0, Math.min(m[2], text.length())));
                g.fill(mx0, y - 1, mx1, y + LINE - 1, 0x60F2C14E);
            }
            if (sel != null && r >= sel[0] && r <= sel[2]) {
                int c0 = r == sel[0] ? sel[1] : 0, c1 = r == sel[2] ? sel[3] : text.length();
                int x0 = textX - scrollX + font.width(text.substring(0, Math.min(c0, text.length())));
                int x1 = textX - scrollX + font.width(text.substring(0, Math.min(c1, text.length()))) + (r < sel[2] ? 4 : 0);
                g.fill(x0, y - 1, x1, y + LINE - 1, Theme.SELECTION);
            }
            if (bracket != null)
                for (int b = 0; b < 4; b += 2)
                    if (bracket[b] == r && bracket[b + 1] < text.length()) {
                        int bx = textX - scrollX + font.width(text.substring(0, bracket[b + 1]));
                        g.fill(bx - 1, y - 1, bx + font.width(text.substring(bracket[b + 1], bracket[b + 1] + 1)) + 1, y + LINE - 1, 0x50FFFFFF);
                    }
            g.text(font, Highlighter.line(text, lang, block), textX - scrollX, y, 0xFFFFFFFF, !Theme.current().equals("light"));
            for (JsonElement de : diagnostics) {
                JsonObject d = de.getAsJsonObject();
                if (d.get("line").getAsInt() != r) continue;
                int c0 = Math.min(d.get("col").getAsInt(), text.length());
                int c1 = d.get("end_line").getAsInt() == r ? Math.min(Math.max(d.get("end_col").getAsInt(), c0 + 1), text.length()) : text.length();
                int ux0 = textX - scrollX + font.width(text.substring(0, c0));
                int ux1 = Math.max(ux0 + 4, textX - scrollX + font.width(text.substring(0, Math.max(c0, c1))));
                int color = d.get("severity").getAsInt() == 1 ? 0xFFF0626A : 0xFFF2C14E;
                for (int ux = ux0; ux < ux1; ux += 2) g.fill(ux, y + LINE - 2 - (ux / 2 % 2), ux + 2, y + LINE - 1 - (ux / 2 % 2), color);
                g.fill(areaX + 1, y, areaX + 3, y + LINE - 2, color);
                if (mouseY >= y - 1 && mouseY < y + LINE - 1 && mouseX >= ux0 && mouseX <= ux1)
                    g.setTooltipForNextFrame(font, Component.literal(d.get("message").getAsString()), mouseX, mouseY);
            }
            Integer foldEnd = folds.get(r);
            if (foldEnd != null) {
                int fx = textX - scrollX + font.width(text) + 6;
                String marker = " ⋯ " + (foldEnd - r) + " ";
                g.fill(fx - 2, y - 1, fx + font.width(marker) + 2, y + LINE - 1, 0x60808080);
                g.text(font, marker, fx, y, 0xFFC8C8C8, false);
                for (int skip = r + 1; skip <= foldEnd; skip++) Highlighter.line(lines.get(skip), lang, block);
            }
            for (int[] c : carets)
                if (c[0] == r && (System.currentTimeMillis() / 500) % 2 == 0) {
                    int cx = textX - scrollX + font.width(text.substring(0, Math.min(c[1], text.length())));
                    g.fill(cx, y - 1, cx + 1, y + LINE - 1, 0xFFF2C14E);
                }
            if (r == row && (System.currentTimeMillis() / 500) % 2 == 0) {
                int cx = textX - scrollX + font.width(text.substring(0, Math.min(col, text.length())));
                g.fill(cx, y - 1, cx + 1, y + LINE - 1, Theme.CARET);
            }
            g.disableScissor();
        }
        if (!loaded) g.text(font, L.t("Загрузка…"), textX, areaY + 4, 0xFF8A8A8A);
        if (!completions.isEmpty()) {
            int shownRows = 0;
            for (int rr = scrollRow; rr < row && rr < lines.size(); rr = stepVisible(rr, 1)) { shownRows++; if (stepVisible(rr, 1) == rr) break; }
            int px = textX - scrollX + font.width(lines.get(row).substring(0, Math.min(col, lines.get(row).length())));
            int py = areaY + 2 + (shownRows + 1) * LINE, n = Math.min(8, completions.size()), pw = 180;
            int first = Math.max(0, Math.min(completionIndex - n + 1, completions.size() - n));
            g.fill(px - 2, py - 1, px + pw, py + n * LINE + 1, 0xF0202028);
            for (int i = 0; i < n; i++) {
                JsonObject it = completions.get(first + i);
                int yy = py + i * LINE;
                if (first + i == completionIndex) g.fill(px - 2, yy - 1, px + pw, yy + LINE - 1, 0xFF2D3A4F);
                g.text(font, font.plainSubstrByWidth(it.get("label").getAsString(), pw - 60), px, yy, 0xFFE8E8E8, false);
                String detail = it.get("detail").getAsString();
                if (!detail.isEmpty()) g.text(font, font.plainSubstrByWidth(detail, 56), px + pw - 58, yy, 0xFF7A7A7A, false);
            }
        }
        String status = L.t("Стр ") + (row + 1) + ", " + L.t("кол ") + (col + 1) + " · " + (lang.isEmpty() ? "text" : lang)
            + " · " + (eol.equals("\r\n") ? "CRLF" : "LF") + " · " + lines.size() + L.t(" строк")
            + (carets.isEmpty() ? "" : " · " + (carets.size() + 1) + L.t(" курсоров"))
            + (diagnostics.isEmpty() ? "" : " · ⚠ " + diagnostics.size() + " (F8)");
        g.text(font, status, width - font.width(status) - 6, height - 13, Theme.WINDOW_DIM);
        if (!message.isEmpty()) g.text(font, font.plainSubstrByWidth(message, width - font.width(status) - 20), 6, height - 13, messageColor);
    }
}
