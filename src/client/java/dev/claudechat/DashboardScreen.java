package dev.claudechat;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.Component;
import java.util.Locale;
import java.util.concurrent.CompletableFuture;

/**
 * Spending dashboard (/cc usage-chart, palette): bars of cost or tokens per day for the
 * last 7/14/30 days, and the top projects and models. Data: the bridge's usage.jsonl
 * (one line per model turn). Costs are Claude Code's estimates at API prices.
 */
final class DashboardScreen extends Screen {
    private final ClaudeChat mod;
    private final Screen parent;
    private JsonObject data;
    private String error = "";
    private static int days = 14;
    private static boolean tokens;

    DashboardScreen(ClaudeChat mod, Screen parent) {
        super(Component.literal("Usage"));
        this.mod = mod;
        this.parent = parent;
    }

    /** Dev screenshots: fixed data. */
    DashboardScreen withData(JsonObject d) { data = d; return this; }

    private void load() {
        CompletableFuture.supplyAsync(() -> {
            try { return mod.request("/usage?days=" + days, null); }
            catch (Exception e) { JsonObject o = new JsonObject(); o.addProperty("error", e.getMessage()); return o; }
        }, mod.io).thenAccept(r -> minecraft.execute(() -> {
            if (r.has("error")) error = r.get("error").getAsString(); else { data = r; error = ""; }
        }));
    }

    private int buttonsLeft;

    @Override protected void init() {
        int x = width - 6;
        x = button(x, "✕", this::onClose);
        x = button(x, tokens ? "$ | ● Σ" : "● $ | Σ", () -> { tokens = !tokens; rebuildWidgets(); });
        for (int d : new int[]{30, 14, 7}) {
            int value = d;
            x = button(x, (days == d ? "● " : "") + d + L.t(" дн."), () -> { days = value; data = null; load(); rebuildWidgets(); });
        }
        buttonsLeft = x;
        if (data == null || !data.has("days") || data.getAsJsonArray("days").size() != days) load();
    }

    private int button(int right, String label, Runnable action) {
        int w = font.width(label) + 12;
        addRenderableWidget(Button.builder(Component.literal(label), b -> action.run()).bounds(right - w, 4, w, 18).build());
        return right - w - 3;
    }

    @Override public boolean isPauseScreen() { return false; }
    @Override public void onClose() { minecraft.setScreenAndShow(parent); }

    private double value(JsonObject o) { return tokens ? o.get("tokens").getAsDouble() : o.get("cost").getAsDouble(); }

    private String fmt(double v) {
        return tokens ? ChatStyle.human((long) v) : String.format(Locale.ROOT, "$%.2f", v);
    }

    @Override public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float delta) {
        g.fill(0, 0, width, height, Theme.SCREEN_BG);
        super.extractRenderState(g, mouseX, mouseY, delta);
        g.text(font, Component.literal("$ " + L.t("Расходы")).withColor(ChatStyle.GOLD), 6, 9, 0xFFFFFFFF);
        if (data == null) { g.text(font, error.isEmpty() ? L.t("Загрузка…") : error, 6, 30, 0xFF8A8A8A); return; }
        JsonObject total = data.getAsJsonObject("total");
        String summary = String.format(Locale.ROOT, L.t("за %d дн.: %s · %s токенов · %d ходов"), days,
            String.format(Locale.ROOT, "$%.2f", total.get("cost").getAsDouble()), ChatStyle.human(total.get("tokens").getAsLong()),
            total.get("turns").getAsInt());
        g.text(font, font.plainSubstrByWidth(summary, Math.max(0, buttonsLeft - 76)), 70, 9, 0xFF8A8A8A);

        // per day: vertical bars
        JsonArray perDay = data.getAsJsonArray("days");
        int x0 = 10, y0 = 34, w = width - 20, h = Math.max(60, height / 2 - 40);
        g.fill(x0, y0, x0 + w, y0 + h, Theme.PANEL);
        double max = 0;
        for (JsonElement e : perDay) max = Math.max(max, value(e.getAsJsonObject()));
        int n = Math.max(1, perDay.size()), bw = Math.max(2, w / n - 2);
        for (int i = 0; i < perDay.size(); i++) {
            JsonObject d = perDay.get(i).getAsJsonObject();
            double v = value(d);
            int bh = max <= 0 ? 0 : (int) Math.round((h - 22) * v / max);
            int bx = x0 + 2 + i * (w / n);
            g.fill(bx, y0 + h - 12 - bh, bx + bw, y0 + h - 12, 0xFFD97757);
            String day = d.get("key").getAsString().substring(8);
            if (n <= 16 || i % 2 == 0) g.text(font, day, bx + bw / 2 - font.width(day) / 2, y0 + h - 10, 0xFF6A6A6A);
            if (mouseX >= bx && mouseX < bx + bw + 2 && mouseY >= y0 && mouseY < y0 + h)
                g.setTooltipForNextFrame(font, Component.literal(d.get("key").getAsString() + ": " + fmt(v)), mouseX, mouseY);
        }
        if (max > 0) g.text(font, fmt(max), x0 + 3, y0 + 3, 0xFF8A8A8A);

        // projects and models: horizontal bars
        int top = y0 + h + 12, colW = (width - 30) / 2;
        bars(g, L.t("Проекты"), data.getAsJsonArray("projects"), 10, top, colW);
        bars(g, L.t("Модели"), data.getAsJsonArray("models"), 20 + colW, top, colW);
        g.text(font, L.t("Оценка Claude Code по ценам API; на подписке тратится лимит, а не деньги"), 10, height - 12, 0xFF5E5E5E);
    }

    private void bars(GuiGraphicsExtractor g, String title, JsonArray rows, int x, int y, int w) {
        g.text(font, Component.literal(title).withColor(ChatStyle.AQUA), x, y, 0xFFFFFFFF);
        double max = 0;
        for (JsonElement e : rows) max = Math.max(max, value(e.getAsJsonObject()));
        int labelW = Math.min(110, w / 3), row = 12;
        for (int i = 0; i < rows.size() && y + 14 + (i + 1) * row < height - 16; i++) {
            JsonObject r = rows.get(i).getAsJsonObject();
            int yy = y + 14 + i * row;
            g.text(font, font.plainSubstrByWidth(r.get("key").getAsString().replaceFirst("^claude-", ""), labelW - 4), x, yy, 0xFFC8C8C8);
            double v = value(r);
            int bw = max <= 0 ? 0 : (int) Math.round((w - labelW - 50) * v / max);
            g.fill(x + labelW, yy, x + labelW + Math.max(1, bw), yy + 8, 0xFF5CC8E6);
            g.text(font, fmt(v), x + labelW + bw + 4, yy, 0xFF8A8A8A);
        }
    }
}
