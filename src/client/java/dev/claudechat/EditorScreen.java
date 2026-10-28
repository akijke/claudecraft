package dev.claudechat;

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
        button(x, "💾 " + L.t("Сохранить"), "Ctrl+S", () -> save(confirmOverwrite));
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
        row = Math.max(0, Math.min(lines.size() - 1, row + dRow));
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
        int visible = areaH / LINE;
        if (row < scrollRow) scrollRow = row;
        if (row >= scrollRow + visible) scrollRow = row - visible + 1;
        int x = font.width(lines.get(row).substring(0, Math.min(col, lines.get(row).length())));
        int textW = areaW - gutterW - 8;
        if (x < scrollX) scrollX = Math.max(0, x - 40);
        if (x > scrollX + textW - 10) scrollX = x - textW + 40;
    }

    // ---- input ----------------------------------------------------------------
    @Override public boolean charTyped(CharacterEvent event) {
        if (finding && (getFocused() == findBox || (replaceBox != null && getFocused() == replaceBox))) return super.charTyped(event);
        if (!loaded || event.codepoint() < 32) return super.charTyped(event);
        insert(event.codepointAsString());
        return true;
    }

    @Override public boolean keyPressed(KeyEvent event) {
        if (PaletteScreen.handleKey(mod, this, event, true)) return true;  // Ctrl+Shift+P / Ctrl+P
        int key = event.key();
        boolean ctrl = (event.modifiers() & 2) != 0, shift = (event.modifiers() & 1) != 0;
        boolean inFind = finding && (getFocused() == findBox || (replaceBox != null && getFocused() == replaceBox));
        if (key == InputConstants.KEY_ESCAPE) { if (finding) closeFind(); else onClose(); return true; }
        if (!loaded) return super.keyPressed(event);
        if (ctrl && key == InputConstants.KEY_F) { openFind(false); return true; }
        if (ctrl && key == InputConstants.KEY_H) { openFind(true); return true; }
        if (key == InputConstants.KEY_F3 || (inFind && key == InputConstants.KEY_RETURN)) {
            if (inFind && getFocused() == replaceBox) replaceOne(); else nextMatch(shift ? -1 : 1);
            return true;
        }
        if (inFind) return super.keyPressed(event);
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
        int r = Math.max(0, Math.min(lines.size() - 1, scrollRow + (int) ((y - areaY - 2) / LINE)));
        String l = lines.get(r);
        int target = (int) x - (areaX + gutterW + 4) + scrollX, c = 0;
        while (c < l.length() && font.width(l.substring(0, c + 1)) - font.width(String.valueOf(l.charAt(c))) / 2 <= target) c++;
        return new int[]{r, c};
    }

    @Override public boolean mouseClicked(MouseButtonEvent event, boolean doubleClick) {
        if (event.x() >= areaX && event.x() < areaX + areaW && event.y() >= areaY && event.y() < areaY + areaH && loaded) {
            int[] p = positionAt(event.x(), event.y());
            boolean shift = (event.modifiers() & 1) != 0;
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
        g.fill(0, 0, width, height, 0xE0151518);
        super.extractRenderState(g, mouseX, mouseY, delta);
        String title = "✎ " + new File(path).getName() + (dirty ? " ●" : "");
        g.text(font, Component.literal(title).withColor(dirty ? 0xF2C14E : 0xD97757), 6, 10, 0xFFFFFFFF);
        g.text(font, font.plainSubstrByWidth(path, width / 2 - font.width(title)), 14 + font.width(title), 10, 0xFF5E5E5E);

        gutterW = font.width(String.valueOf(lines.size())) + GUTTER_PAD * 2;
        g.fill(areaX, areaY, areaX + areaW, areaY + areaH, 0xFF1E1E22);
        g.fill(areaX, areaY, areaX + gutterW, areaY + areaH, 0xFF19191D);
        int visible = areaH / LINE, textX = areaX + gutterW + 4;
        boolean[] block = {false};
        // block-comment state for the lines above the view
        for (int r = Math.max(0, scrollRow - 200); r < scrollRow; r++) Highlighter.line(lines.get(r), lang, block);
        int[] sel = hasSelection() ? selection() : null;
        for (int i = 0; i < visible && scrollRow + i < lines.size(); i++) {
            int r = scrollRow + i, y = areaY + 2 + i * LINE;
            String text = lines.get(r);
            if (r == row) g.fill(areaX + gutterW, y - 1, areaX + areaW, y + LINE - 1, 0xFF26262C);
            if (r == highlight) g.fill(areaX + gutterW, y - 1, areaX + areaW, y + LINE - 1, 0x40F2C14E);
            String number = String.valueOf(r + 1);
            g.text(font, number, areaX + gutterW - GUTTER_PAD - font.width(number), y, r == row ? 0xFFC6C6C6 : 0xFF5A5A5A);
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
                g.fill(x0, y - 1, x1, y + LINE - 1, 0xFF264F78);
            }
            g.text(font, Highlighter.line(text, lang, block), textX - scrollX, y, 0xFFFFFFFF);
            if (r == row && (System.currentTimeMillis() / 500) % 2 == 0) {
                int cx = textX - scrollX + font.width(text.substring(0, Math.min(col, text.length())));
                g.fill(cx, y - 1, cx + 1, y + LINE - 1, 0xFFFFFFFF);
            }
            g.disableScissor();
        }
        if (!loaded) g.text(font, L.t("Загрузка…"), textX, areaY + 4, 0xFF8A8A8A);
        String status = L.t("Стр ") + (row + 1) + ", " + L.t("кол ") + (col + 1) + " · " + (lang.isEmpty() ? "text" : lang)
            + " · " + (eol.equals("\r\n") ? "CRLF" : "LF") + " · " + lines.size() + L.t(" строк");
        g.text(font, status, width - font.width(status) - 6, height - 13, 0xFF6A6A6A);
        if (!message.isEmpty()) g.text(font, font.plainSubstrByWidth(message, width - font.width(status) - 20), 6, height - 13, messageColor);
    }
}
