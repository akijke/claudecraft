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
import net.minecraft.network.chat.Component;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Real interactive terminal (/pty): a shell in a pseudo console on the bridge
 * (ConPTY on Windows, pty elsewhere), drawn as a character grid. Keys go to the
 * shell as terminal input, so PowerShell, cmd, bash, git, vim, htop, python REPLs
 * work as in a normal terminal. The shell keeps running when the window is hidden.
 */
final class TerminalScreen extends Screen {
    private static final int CELL_W = 6, CELL_H = 9, TOP = 22, BOTTOM = 12;
    private static final float[] ZOOMS = {1f, 0.75f, 0.5f};
    private static String lastId;
    private static int zoomIndex;

    private final ClaudeChat mod;
    private final Screen parent;
    private final String shell, cwd;
    private String id;
    private JsonObject snap;
    private long version = -1;
    private int scroll, cols, rows;
    private String message = "";
    private long nextPoll;
    private final AtomicBoolean polling = new AtomicBoolean();
    private JsonArray tabs = new JsonArray();

    /** Opens a new terminal (shell: auto|ps|cmd|bash|pwsh|wsl, cwd: folder or "" = browsing folder). */
    TerminalScreen(ClaudeChat mod, Screen parent, String shell, String cwd) {
        super(Component.literal("Terminal"));
        this.mod = mod;
        this.parent = parent;
        this.shell = shell == null || shell.isBlank() ? "auto" : shell;
        this.cwd = cwd == null ? "" : cwd;
    }

    /** Reopens the last terminal if it is still there, otherwise starts one. */
    static TerminalScreen reopen(ClaudeChat mod, Screen parent) {
        TerminalScreen screen = new TerminalScreen(mod, parent, "auto", "");
        screen.id = lastId;
        return screen;
    }

    private float zoom() { return ZOOMS[zoomIndex]; }

    // ---- bridge ---------------------------------------------------------------
    private CompletableFuture<JsonObject> call(String path, JsonObject body) {
        return CompletableFuture.supplyAsync(() -> {
            try { return mod.request(path, body); }
            catch (Exception e) { JsonObject o = new JsonObject(); o.addProperty("error", e.getMessage()); return o; }
        }, mod.io);
    }

    private void open() {
        JsonObject body = new JsonObject();
        body.addProperty("mode", "cc");
        body.addProperty("shell", shell);
        body.addProperty("cwd", cwd);
        body.addProperty("cols", cols);
        body.addProperty("rows", rows);
        message = L.t("Запускаю ") + shell + "…";
        call("/pty/open", body).thenAccept(r -> minecraft.execute(() -> {
            if (r.has("error")) { message = r.get("error").getAsString(); return; }
            id = r.get("id").getAsString();
            lastId = id;
            snap = r;
            version = r.get("version").getAsLong();
            message = "";
            refreshTabs();
        }));
    }

    private void refreshTabs() {
        call("/pty/list", null).thenAccept(r -> minecraft.execute(() -> {
            if (r.has("terminals")) { tabs = r.getAsJsonArray("terminals"); rebuildWidgets(); }
        }));
    }

    private void send(String data) {
        if (id == null || data.isEmpty()) return;
        scroll = 0;
        JsonObject body = new JsonObject();
        body.addProperty("id", id);
        body.addProperty("data", data);
        call("/pty/input", body).thenAccept(r -> { if (r.has("error")) minecraft.execute(() -> message = r.get("error").getAsString()); });
        nextPoll = 0;
    }

    private void resizeRemote() {
        if (id == null) return;
        JsonObject body = new JsonObject();
        body.addProperty("id", id);
        body.addProperty("cols", cols);
        body.addProperty("rows", rows);
        call("/pty/resize", body);
    }

    private void poll() {
        long now = System.currentTimeMillis();
        if (id == null || now < nextPoll || !polling.compareAndSet(false, true)) return;
        nextPoll = now + 60;
        String query = "/pty/screen?id=" + URLEncoder.encode(id, StandardCharsets.UTF_8) + "&scroll=" + scroll
            + "&version=" + version + "&was=" + (snap != null && snap.has("scroll") ? snap.get("scroll").getAsInt() : -1);
        call(query, null).thenAccept(r -> minecraft.execute(() -> {
            polling.set(false);
            if (r.has("error")) {
                message = r.get("error").getAsString();
                if (message.contains("Нет такого терминала") || message.contains("No such terminal")) { id = null; lastId = null; }
                return;
            }
            if (r.has("same")) return;
            snap = r;
            version = r.get("version").getAsLong();
        }));
    }

    // ---- layout -------------------------------------------------------------
    @Override protected void init() {
        int newCols = Math.max(20, (int) ((width - 8) / (CELL_W * zoom())));
        int newRows = Math.max(5, (int) ((height - TOP - BOTTOM) / (CELL_H * zoom())));
        boolean changed = newCols != cols || newRows != rows;
        cols = newCols;
        rows = newRows;
        int x = width - 4;
        x = button(x, "✕", L.t("Закрыть терминал (завершить оболочку)"), () -> {
            if (id != null) { JsonObject b = new JsonObject(); b.addProperty("id", id); call("/pty/close", b); }
            id = null; lastId = null;
            minecraft.setScreenAndShow(parent);
        });
        x = button(x, L.t("Скрыть"), L.t("Оболочка продолжит работать; /pty — вернуться (Ctrl+Shift+W)"), this::onClose);
        x = button(x, "+", L.t("Новый терминал (shell: ps, cmd, bash, pwsh, wsl — /pty new <shell>)"), () -> {
            minecraft.setScreenAndShow(new TerminalScreen(mod, parent, shell, cwd));
        });
        x = button(x, "A" + (zoomIndex == 0 ? "+" : "-"), L.t("Размер шрифта (Ctrl+= / Ctrl+-)"), () -> {
            zoomIndex = (zoomIndex + 1) % ZOOMS.length;
            rebuildWidgets();
        });
        int tx = 4;
        for (JsonElement e : tabs) {
            JsonObject t = e.getAsJsonObject();
            String tid = t.get("id").getAsString();
            String label = (t.get("alive").getAsBoolean() ? "" : "✖ ") + t.get("shell").getAsString();
            int w = font.width(label) + 10;
            if (tx + w > x - 4) break;
            Button b = Button.builder(Component.literal(label), btn -> { id = tid; lastId = tid; version = -1; snap = null; resizeRemote(); rebuildWidgets(); })
                .bounds(tx, 2, w, 16).tooltip(Tooltip.create(Component.literal(t.get("cwd").getAsString()))).build();
            b.active = !tid.equals(id);
            addRenderableWidget(b);
            tx += w + 2;
        }
        if (id == null) open();
        else if (changed) resizeRemote();
        if (tabs.isEmpty() && id != null) refreshTabs();
        setFocused(null);
    }

    private int button(int right, String label, String tip, Runnable action) {
        int w = font.width(label) + 10;
        addRenderableWidget(Button.builder(Component.literal(label), b -> action.run())
            .bounds(right - w, 2, w, 16).tooltip(Tooltip.create(Component.literal(tip))).build());
        return right - w - 2;
    }

    @Override public boolean isPauseScreen() { return false; }
    @Override public void onClose() { minecraft.setScreenAndShow(parent); }

    // ---- input ----------------------------------------------------------------
    @Override public boolean charTyped(CharacterEvent event) {
        if (event.codepoint() < 32) return true;
        send(event.codepointAsString());
        return true;
    }

    @Override public boolean keyPressed(KeyEvent event) {
        if (PaletteScreen.handleKey(mod, this, event, false)) return true;  // Ctrl+Shift+P / Ctrl+P
        int key = event.key(), mods = event.modifiers();
        boolean ctrl = (mods & 2) != 0, shift = (mods & 1) != 0, alt = (mods & 4) != 0;
        if (key == InputConstants.KEY_ESCAPE) {
            // Full-screen programs (vim, less, htop) get Esc; at the prompt Esc hides the window.
            boolean fullScreen = snap != null && snap.has("alt") && snap.get("alt").getAsBoolean();
            if (fullScreen || shift) send("\u001b"); else onClose();
            return true;
        }
        if (ctrl && shift) {
            switch (key) {
                case InputConstants.KEY_C -> { minecraft.keyboardHandler.setClipboard(screenText()); message = L.t("Экран скопирован"); return true; }
                case InputConstants.KEY_V -> { paste(); return true; }
                case InputConstants.KEY_W -> { onClose(); return true; }
                default -> { }
            }
        }
        if (ctrl && (key == InputConstants.KEY_EQUALS)) { zoomIndex = Math.max(0, zoomIndex - 1); rebuildWidgets(); return true; }
        if (ctrl && (key == InputConstants.KEY_MINUS)) { zoomIndex = Math.min(ZOOMS.length - 1, zoomIndex + 1); rebuildWidgets(); return true; }
        if (shift && key == InputConstants.KEY_INSERT) { paste(); return true; }
        if (ctrl && key >= InputConstants.KEY_A && key <= InputConstants.KEY_Z) {
            send(String.valueOf((char) (key - InputConstants.KEY_A + 1)));  // Ctrl+C = \x03 …
            return true;
        }
        String seq = switch (key) {
            case InputConstants.KEY_RETURN, InputConstants.KEY_NUMPADENTER -> "\r";
            case InputConstants.KEY_BACKSPACE -> ctrl ? "\u0017" : "\u007f";
            case InputConstants.KEY_TAB -> shift ? "\u001b[Z" : "\t";
            case InputConstants.KEY_UP -> ctrl ? "\u001b[1;5A" : "\u001b[A";
            case InputConstants.KEY_DOWN -> ctrl ? "\u001b[1;5B" : "\u001b[B";
            case InputConstants.KEY_RIGHT -> ctrl ? "\u001b[1;5C" : "\u001b[C";
            case InputConstants.KEY_LEFT -> ctrl ? "\u001b[1;5D" : "\u001b[D";
            case InputConstants.KEY_HOME -> "\u001b[H";
            case InputConstants.KEY_END -> "\u001b[F";
            case InputConstants.KEY_INSERT -> "\u001b[2~";
            case InputConstants.KEY_DELETE -> "\u001b[3~";
            case InputConstants.KEY_PAGEUP -> { if (shift) { scrollBy(rows / 2); yield ""; } yield "\u001b[5~"; }
            case InputConstants.KEY_PAGEDOWN -> { if (shift) { scrollBy(-rows / 2); yield ""; } yield "\u001b[6~"; }
            case InputConstants.KEY_F1 -> "\u001bOP";
            case InputConstants.KEY_F2 -> "\u001bOQ";
            case InputConstants.KEY_F3 -> "\u001bOR";
            case InputConstants.KEY_F4 -> "\u001bOS";
            case InputConstants.KEY_F5 -> "\u001b[15~";
            case InputConstants.KEY_F6 -> "\u001b[17~";
            case InputConstants.KEY_F7 -> "\u001b[18~";
            case InputConstants.KEY_F8 -> "\u001b[19~";
            case InputConstants.KEY_F9 -> "\u001b[20~";
            case InputConstants.KEY_F10 -> "\u001b[21~";
            case InputConstants.KEY_F11 -> "\u001b[23~";
            case InputConstants.KEY_F12 -> "\u001b[24~";
            default -> null;
        };
        if (seq != null) { if (!seq.isEmpty()) send(alt ? "\u001b" + seq : seq); return true; }
        return super.keyPressed(event);
    }

    /** Dev screenshots: types into the shell. */
    void typeForTest(String text) { send(text); }

    private void paste() {
        String text = minecraft.keyboardHandler.getClipboard().replace("\r\n", "\r").replace("\n", "\r");
        send("\u001b[200~" + text + "\u001b[201~");  // bracketed paste: shells don't run it line by line
    }

    private void scrollBy(int lines) {
        int history = snap != null && snap.has("history") ? snap.get("history").getAsInt() : 0;
        scroll = Math.max(0, Math.min(history, scroll + lines));
        nextPoll = 0;
    }

    /** Text of one screen row (as shown) for link detection. */
    private String rowText(int r) {
        if (snap == null) return "";
        JsonArray rowsJson = snap.getAsJsonArray("rows");
        if (r < 0 || r >= rowsJson.size()) return "";
        StringBuilder b = new StringBuilder();
        for (JsonElement run : rowsJson.get(r).getAsJsonArray()) b.append(run.getAsJsonArray().get(0).getAsString());
        return b.toString();
    }

    /** Click on `path:line` (compiler errors, stack traces, grep -n) opens the editor there. */
    @Override public boolean mouseClicked(net.minecraft.client.input.MouseButtonEvent event, boolean doubleClick) {
        if (super.mouseClicked(event, doubleClick)) return true;
        float z = zoom();
        int r = (int) ((event.y() - TOP) / (CELL_H * z)), c = (int) ((event.x() - 4) / (CELL_W * z));
        String text = rowText(r);
        for (PathLinks.Link link : PathLinks.find(text)) {
            int start = text.codePointCount(0, link.start()), end = text.codePointCount(0, link.end());
            if (c >= start && c < end) {
                String path = link.path();
                java.io.File f = new java.io.File(path);
                if (!f.isAbsolute() && snap.has("cwd")) path = new java.io.File(snap.get("cwd").getAsString(), path).getPath();
                minecraft.setScreenAndShow(new EditorScreen(mod, this, path).at(link.line()));
                return true;
            }
        }
        return false;
    }

    @Override public boolean mouseScrolled(double x, double y, double h, double v) {
        scrollBy((int) Math.signum(v) * 3);
        return true;
    }

    String screenText() {
        if (snap == null) return "";
        StringBuilder b = new StringBuilder();
        for (JsonElement row : snap.getAsJsonArray("rows")) {
            for (JsonElement run : row.getAsJsonArray()) b.append(run.getAsJsonArray().get(0).getAsString());
            b.append('\n');
        }
        return b.toString().stripTrailing();
    }

    // ---- render ---------------------------------------------------------------
    @Override public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float delta) {
        poll();
        g.fill(0, 0, width, height, Theme.TERMINAL_BG);
        super.extractRenderState(g, mouseX, mouseY, delta);
        float z = zoom();
        int x0 = 4, y0 = TOP;
        if (snap != null && snap.has("rows")) {
            g.pose().pushMatrix();
            g.pose().translate(x0, y0);
            g.pose().scale(z, z);
            JsonArray rowsJson = snap.getAsJsonArray("rows");
            for (int r = 0; r < rowsJson.size(); r++) {
                int col = 0, y = r * CELL_H;
                for (JsonElement runEl : rowsJson.get(r).getAsJsonArray()) {
                    JsonArray run = runEl.getAsJsonArray();
                    String text = run.get(0).getAsString();
                    int fg = run.get(1).isJsonNull() ? Theme.TERMINAL_FG : run.get(1).getAsInt();
                    boolean bold = run.get(3).getAsBoolean();
                    int n = text.codePointCount(0, text.length());
                    if (!run.get(2).isJsonNull())
                        g.fill(col * CELL_W, y, (col + n) * CELL_W, y + CELL_H, 0xFF000000 | run.get(2).getAsInt());
                    int i = 0;
                    while (i < text.length()) {
                        int cp = text.codePointAt(i);
                        i += Character.charCount(cp);
                        if (cp != ' ') {
                            String ch = new String(Character.toChars(cp));
                            int w = font.width(ch);
                            Component c = Component.literal(ch).withStyle(st -> st.withColor(fg).withBold(bold));
                            g.text(font, c, col * CELL_W + Math.max(0, (CELL_W - w) / 2), y + 1, 0xFFFFFFFF, false);
                        }
                        col++;
                    }
                }
            }
            JsonArray cursor = snap.getAsJsonArray("cursor");
            if (snap.get("cursor_visible").getAsBoolean() && (System.currentTimeMillis() / 530) % 2 == 0) {
                int cx = cursor.get(0).getAsInt() * CELL_W, cy = cursor.get(1).getAsInt() * CELL_H;
                g.fill(cx, cy + CELL_H - 2, cx + CELL_W, cy + CELL_H, Theme.CARET);
            }
            g.pose().popMatrix();
        }
        String title = snap != null && snap.has("title") && !snap.get("title").getAsString().isEmpty()
            ? snap.get("title").getAsString() : shell;
        boolean alive = snap == null || !snap.has("alive") || snap.get("alive").getAsBoolean();
        String status = (scroll > 0 ? L.t("история −") + scroll + " · " : "") + cols + "×" + rows
            + " · Ctrl+Shift+C/V · Shift+PgUp · Esc " + L.t("скрыть");
        String head = font.plainSubstrByWidth("$ " + title, Math.max(40, width - font.width(status) - 16));
        g.text(font, head, 4, height - 10, alive ? 0xFF6BD08B : 0xFF8A8A8A);
        g.text(font, status, width - font.width(status) - 4, height - 10, 0xFF5E5E5E);
        if (!message.isEmpty()) g.centeredText(font, message, width / 2, height / 2, 0xFFF2C14E);
        if (!alive) g.text(font, L.t("оболочка завершилась — ✕ закрыть, + новая"), width / 2 - font.width(L.t("оболочка завершилась — ✕ закрыть, + новая")) / 2, height - 22, 0xFF8A8A8A);
    }
}
