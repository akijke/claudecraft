package dev.claudechat;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.network.chat.ClickEvent;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.HoverEvent;
import net.minecraft.network.chat.MutableComponent;
import java.net.URI;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Turns bridge events into styled chat lines (colours, icons, buttons, light markdown). */
final class ChatStyle {
    static final int CLAUDE = 0xD97757, CODE_MODE = 0xB48EF0, TEXT = 0xE8E8E8, DIM = 0x8A8A8A, FAINT = 0x5E5E5E,
        GREEN = 0x6BD08B, RED = 0xF0626A, GOLD = 0xF2C14E, AQUA = 0x5CC8E6, CODE = 0xE5C07B, BLOCK = 0x9CDCFE,
        DIR = 0x6CA8FF;
    private static final Pattern INLINE = Pattern.compile("\\*\\*(.+?)\\*\\*|`([^`]+)`|\\[([^\\]]+)]\\((https?://[^)\\s]+)\\)");

    private ChatStyle() { }

    static String clean(String text) {
        return text.replaceAll("\\u001B\\[[;\\d]*[ -/]*[@-~]", "").replace("§", "")
            .replaceAll("[\\p{Cntrl}&&[^\\n\\t]]", "").replace("\t", "    ");
    }

    static MutableComponent lit(String text, int color) {
        return Component.literal(clean(text)).withColor(color);
    }

    /** Coloured bar that marks which mode (/c or /cc) a line belongs to. */
    static MutableComponent bar(String mode) {
        return lit("▍", mode.equals("cc") ? CODE_MODE : CLAUDE);
    }

    static MutableComponent line(String mode, Component content) {
        return bar(mode).append(Component.literal(" ")).append(content);
    }

    static MutableComponent button(String label, String command, boolean suggest, int color, String hover) {
        return Component.literal("[" + clean(label) + "]").withStyle(style -> {
            var s = style.withColor(color).withClickEvent(suggest
                ? new ClickEvent.SuggestCommand(command) : new ClickEvent.RunCommand(command));
            return hover == null ? s : s.withHoverEvent(new HoverEvent.ShowText(lit(hover, TEXT)));
        });
    }

    static String human(long n) {
        if (n < 1000) return Long.toString(n);
        if (n < 1_000_000) return String.format(Locale.ROOT, "%.1fk", n / 1000.0);
        return String.format(Locale.ROOT, "%.2fM", n / 1_000_000.0);
    }

    static String size(long n) {
        if (n < 1024) return n + " B";
        if (n < 1024 * 1024) return String.format(Locale.ROOT, "%.1f KB", n / 1024.0);
        return String.format(Locale.ROOT, "%.1f MB", n / 1024.0 / 1024.0);
    }

    private static String str(JsonObject e, String key) {
        return e.has(key) && !e.get(key).isJsonNull() ? e.get(key).getAsString() : "";
    }

    /** All chat lines for one event. */
    static List<Component> render(JsonObject e) {
        String mode = str(e, "mode"), kind = str(e, "kind"), text = str(e, "text"), title = str(e, "title");
        String cmd = "/" + mode + " ";
        List<Component> out = new ArrayList<>();
        switch (kind) {
            case "thinking" -> {
                MutableComponent c = lit("✦ Claude", CLAUDE).append(lit(" думает…", DIM))
                    .append(lit("  " + title, FAINT));
                if (e.has("queued") && e.get("queued").getAsInt() > 0)
                    c.append(lit("  · ещё в очереди: " + e.get("queued").getAsInt(), FAINT));
                out.add(line(mode, c));
            }
            case "text" -> markdown(mode, text, out);
            case "tool" -> {
                String icon = str(e, "icon");
                boolean shell = icon.equals("$");
                out.add(line(mode, lit("  " + icon + " ", shell ? GREEN : GOLD)
                    .append(lit(text, shell ? TEXT : DIM))));
            }
            case "tool_error" -> out.add(line(mode, lit("  ✖ ", RED).append(lit(firstLine(text), 0xC9888C))));
            case "todo" -> {
                for (JsonElement r : e.getAsJsonArray("rows")) {
                    JsonObject row = r.getAsJsonObject();
                    String status = str(row, "status");
                    MutableComponent item = switch (status) {
                        case "completed" -> lit("  ☑ ", GREEN).append(lit(str(row, "text"), FAINT)
                            .withStyle(s -> s.withStrikethrough(true)));
                        case "in_progress" -> lit("  ▶ ", GOLD).append(lit(str(row, "text"), TEXT));
                        default -> lit("  ☐ ", DIM).append(lit(str(row, "text"), DIM));
                    };
                    out.add(line(mode, item));
                }
            }
            case "done" -> {
                MutableComponent c = lit("✔ Готово", GREEN);
                if (e.has("seconds")) c.append(lit("  " + e.get("seconds").getAsDouble() + " с", DIM));
                long in = e.has("input") ? e.get("input").getAsLong() : 0, o = e.has("output") ? e.get("output").getAsLong() : 0;
                if (in + o > 0) c.append(lit("  ↑" + human(in) + " ↓" + human(o), DIM));
                if (e.has("cost") && e.get("cost").getAsDouble() > 0)
                    c.append(lit(String.format(Locale.ROOT, "  ≈$%.3f", e.get("cost").getAsDouble()), FAINT));
                out.add(line(mode, c));
            }
            case "error" -> {
                String[] lines = clean(text).split("\\R");
                for (int i = 0; i < lines.length; i++)
                    out.add(line(mode, lit(i == 0 ? "✖ " : "  ", RED).append(lit(lines[i], i == 0 ? RED : 0xC9888C))));
            }
            case "status" -> out.add(line(mode, lit("· " + text, DIM).withStyle(s -> s.withItalic(true))));
            case "permission" -> permission(e, mode, out);
            case "question" -> question(e, mode, text, out);
            case "chats" -> chats(e, mode, out);
            case "listing" -> listing(e, mode, out);
            case "cwd" -> {
                out.add(line(mode, lit("📁 ", DIR).append(lit(text, TEXT))
                    .append(Component.literal("  ")).append(button("ls", cmd + "ls", false, AQUA, "Показать содержимое"))));
                if (!str(e, "note").isEmpty()) out.add(line(mode, lit("  " + str(e, "note"), DIM)));
            }
            case "file" -> {
                out.add(line(mode, lit("📄 ", GOLD).append(lit(str(e, "path"), TEXT))));
                String[] lines = clean(text).split("\\R", -1);
                int width = String.valueOf(lines.length).length();
                for (int i = 0; i < lines.length; i++)
                    out.add(line(mode, lit(String.format("%" + width + "d", i + 1) + " │ ", FAINT).append(lit(lines[i], BLOCK))));
                if (e.has("truncated") && e.get("truncated").getAsBoolean())
                    out.add(line(mode, lit("  … файл длиннее, показано начало", DIM)));
            }
            case "tokens" -> tokens(e, mode, out);
            case "auto" -> {
                String current = str(e, "mode_value");
                MutableComponent row = lit("⚡ Авторазрешение: ", GOLD);
                for (String m : new String[]{"off", "edits", "all"}) {
                    boolean on = m.equals(current);
                    row.append(button((on ? "● " : "") + m, cmd + "auto " + m, false,
                        on ? GREEN : (m.equals("all") ? RED : AQUA), autoHint(m))).append(Component.literal(" "));
                }
                out.add(line(mode, row));
                out.add(line(mode, lit("  " + text, current.equals("all") ? RED : DIM)));
                if (e.has("allowed") && e.getAsJsonArray("allowed").size() > 0)
                    out.add(line(mode, lit("  Всегда разрешены: " + join(e.getAsJsonArray("allowed")), DIM)));
            }
            case "url" -> {
                out.add(line(mode, lit("◎ " + text, TEXT)));
                try {
                    URI uri = URI.create(str(e, "url"));
                    if (("https".equals(uri.getScheme()) || "http".equals(uri.getScheme())) && uri.getHost() != null)
                        out.add(line(mode, Component.literal("  [Открыть в браузере] " + uri.getHost()).withStyle(s -> s
                            .withColor(AQUA).withUnderlined(true).withClickEvent(new ClickEvent.OpenUrl(uri))
                            .withHoverEvent(new HoverEvent.ShowText(lit(uri.toString(), TEXT))))));
                } catch (IllegalArgumentException ignored) { }
            }
            default -> {
                for (String l : clean(text).split("\\R")) if (!l.isBlank()) out.add(line(mode, lit(l, TEXT)));
            }
        }
        return out;
    }

    private static String autoHint(String m) {
        return switch (m) {
            case "off" -> "Спрашивать каждое действие";
            case "edits" -> "Без вопросов: чтение, поиск, правка файлов";
            default -> "Без вопросов: ВСЁ, включая команды";
        };
    }

    private static String join(JsonArray a) {
        List<String> s = new ArrayList<>();
        for (JsonElement x : a) s.add(x.getAsString());
        return String.join(", ", s);
    }

    private static String firstLine(String text) {
        String t = clean(text).strip();
        int nl = t.indexOf('\n');
        return nl < 0 ? t : t.substring(0, nl) + " …";
    }

    private static void permission(JsonObject e, String mode, List<Component> out) {
        String request = str(e, "request"), text = str(e, "text");
        String summary = str(e, "summary").isEmpty() ? firstLine(text) : str(e, "summary");
        String prefix = "/" + mode + " reply " + request + " ";
        out.add(line(mode, lit("⚠ Разрешить действие?", GOLD).withStyle(s -> s.withBold(true))));
        out.add(line(mode, lit("  " + summary, TEXT).withStyle(s -> s.withHoverEvent(new HoverEvent.ShowText(
            lit(text.length() > 900 ? text.substring(0, 900) + "…" : text, BLOCK))))));
        JsonArray choices = e.getAsJsonArray("choices");
        MutableComponent row = Component.literal("  ");
        int[] colors = {GREEN, RED, AQUA};
        String[] icons = {"✔ ", "✖ ", "∞ "};
        for (int i = 0; i < choices.size(); i++) {
            String label = (i < icons.length ? icons[i] : "") + choices.get(i).getAsString();
            row.append(button(label, prefix + (i + 1), false, i < colors.length ? colors[i] : AQUA, null))
                .append(Component.literal(" "));
        }
        out.add(line(mode, row));
    }

    private static void question(JsonObject e, String mode, String text, List<Component> out) {
        String prefix = "/" + mode + " reply " + str(e, "request") + " ";
        boolean multiple = e.has("multiple") && e.get("multiple").getAsBoolean();
        String[] lines = clean(text).split("\\R");
        out.add(line(mode, lit("? ", AQUA).append(lit(lines[0], TEXT).withStyle(s -> s.withBold(true)))));
        // Option lines ("1. label — description") become button tooltips instead of duplicates.
        java.util.Map<Integer, String> descriptions = new java.util.HashMap<>();
        Pattern option = Pattern.compile("^(\\d+)\\. .*? — (.*)$");
        for (int i = 1; i < lines.length; i++) {
            Matcher m = option.matcher(lines[i]);
            if (m.matches()) { descriptions.put(Integer.parseInt(m.group(1)), m.group(2).strip()); continue; }
            if (lines[i].matches("^\\d+\\. .*")) continue;
            if (!lines[i].isBlank()) out.add(line(mode, lit("  " + lines[i], DIM)));
        }
        JsonArray choices = e.getAsJsonArray("choices");
        for (int i = 0; i < choices.size(); i++) {
            String hint = descriptions.getOrDefault(i + 1, "");
            if (multiple) hint = (hint.isEmpty() ? "" : hint + "\n") + "Несколько вариантов: " + prefix + "1,3";
            MutableComponent row = Component.literal("  ").append(button((i + 1) + ". " + choices.get(i).getAsString(),
                prefix + (i + 1), multiple, AQUA, hint.isEmpty() ? null : hint));
            String desc = descriptions.get(i + 1);
            if (desc != null && !desc.isEmpty()) row.append(lit("  " + desc, FAINT));
            out.add(line(mode, row));
        }
        out.add(line(mode, Component.literal("  ").append(button("✎ Свой ответ", prefix, true, GOLD, "Впиши ответ после команды"))));
    }

    private static void chats(JsonObject e, String mode, List<Component> out) {
        out.add(line(mode, lit("☰ Чаты " + (mode.equals("cc") ? "Claude Code" : "Claude"), TEXT).withStyle(s -> s.withBold(true))));
        for (JsonElement item : e.getAsJsonArray("rows")) {
            JsonObject row = item.getAsJsonObject();
            boolean selected = row.get("selected").getAsBoolean();
            out.add(line(mode, lit(selected ? "  ● " : "  ○ ", selected ? GREEN : FAINT)
                .append(button(row.get("label").getAsString(), "/" + mode + " select " + row.get("id").getAsString(),
                    false, selected ? GREEN : AQUA, "Переключиться на этот чат"))));
        }
        MutableComponent buttons = Component.literal("  ").append(button("+ Новый", "/" + mode + " new local", false, GOLD, null));
        if (mode.equals("c")) buttons.append(" ").append(button("+ API", "/c new api", false, GOLD, "Чат через Messages API"));
        else buttons.append(" ").append(button("Импорт CLI", "/cc local", false, GOLD, "Сессии Claude Code из workspace"));
        out.add(line(mode, buttons));
    }

    private static void listing(JsonObject e, String mode, List<Component> out) {
        String cmd = "/" + mode + " ";
        MutableComponent head = lit("📁 ", DIR).append(lit(str(e, "path"), TEXT));
        if (!str(e, "parent").isEmpty()) head.append(Component.literal("  ")).append(button("..", cmd + "cd ..", false, AQUA, "Вверх"));
        out.add(line(mode, head));
        JsonArray rows = e.getAsJsonArray("rows");
        if (rows.isEmpty()) out.add(line(mode, lit("  (пусто)", DIM)));
        // Pack several entries per chat line so big folders stay readable.
        MutableComponent current = Component.literal("  ");
        int used = 0;
        for (JsonElement item : rows) {
            JsonObject row = item.getAsJsonObject();
            String name = clean(str(row, "name"));
            boolean dir = row.get("dir").getAsBoolean();
            String label = dir ? name + "/" : name;
            if (used > 0 && used + label.length() > 46) { out.add(line(mode, current)); current = Component.literal("  "); used = 0; }
            MutableComponent entry = Component.literal(label).withStyle(s -> s
                .withColor(dir ? DIR : TEXT)
                .withClickEvent(new ClickEvent.RunCommand(cmd + (dir ? "cd " : "cat ") + name))
                .withHoverEvent(new HoverEvent.ShowText(lit(dir ? "Открыть папку" : "Показать · " + size(row.get("size").getAsLong()), TEXT))));
            current.append(entry).append(Component.literal("   "));
            used += label.length() + 3;
        }
        if (used > 0) out.add(line(mode, current));
        if (e.has("more") && e.get("more").getAsInt() > 0)
            out.add(line(mode, lit("  … и ещё " + e.get("more").getAsInt(), DIM)));
    }

    private static void tokens(JsonObject e, String mode, List<Component> out) {
        out.add(line(mode, lit("Σ Токены", GOLD).withStyle(s -> s.withBold(true))));
        for (JsonElement item : e.getAsJsonArray("rows")) {
            JsonObject r = item.getAsJsonObject();
            out.add(line(mode, lit("  " + str(r, "label"), TEXT)));
            MutableComponent c = lit("    ↑ вход ", DIM).append(lit(human(r.get("input").getAsLong()), TEXT))
                .append(lit("  ↓ выход ", DIM)).append(lit(human(r.get("output").getAsLong()), TEXT))
                .append(lit("  кэш ", DIM)).append(lit(human(r.get("cache").getAsLong()), TEXT));
            if (r.get("cost").getAsDouble() > 0)
                c.append(lit(String.format(Locale.ROOT, "  ≈$%.3f", r.get("cost").getAsDouble()), GOLD));
            out.add(line(mode, c));
        }
        if (!str(e, "note").isEmpty()) out.add(line(mode, lit("  " + str(e, "note"), FAINT)));
    }

    /** Light markdown: headings, bullets, code fences, **bold**, `code`, [links](url). */
    static void markdown(String mode, String text, List<Component> out) {
        boolean code = false, first = true;
        for (String raw : clean(text).split("\\R")) {
            if (raw.strip().startsWith("```")) { code = !code; continue; }
            MutableComponent content;
            if (code) {
                content = lit("│ ", FAINT).append(lit(raw, BLOCK));
            } else if (raw.isBlank()) {
                continue;
            } else if (raw.startsWith("#")) {
                String h = raw.replaceFirst("^#+\\s*", "");
                content = inline(h, TEXT).withStyle(s -> s.withBold(true).withColor(GOLD));
            } else if (raw.matches("^\\s*[-*+] .*")) {
                int indent = raw.indexOf(raw.strip().charAt(0));
                content = Component.literal(" ".repeat(indent)).append(lit("• ", CLAUDE))
                    .append(inline(raw.strip().substring(2), TEXT));
            } else if (raw.matches("^\\s*\\d+[.)] .*")) {
                String s = raw.strip();
                int dot = s.indexOf(' ');
                content = lit(s.substring(0, dot + 1), CLAUDE).append(inline(s.substring(dot + 1), TEXT));
            } else if (raw.startsWith(">")) {
                content = lit("┃ ", FAINT).append(inline(raw.substring(1).strip(), DIM));
            } else {
                content = inline(raw, TEXT);
            }
            out.add(line(mode, first ? lit("✦ ", CLAUDE).append(content) : Component.literal("  ").append(content)));
            first = false;
        }
    }

    static MutableComponent inline(String text, int color) {
        MutableComponent result = Component.literal("");
        Matcher m = INLINE.matcher(text);
        int last = 0;
        while (m.find()) {
            if (m.start() > last) result.append(lit(text.substring(last, m.start()), color));
            if (m.group(1) != null) result.append(lit(m.group(1), color).withStyle(s -> s.withBold(true)));
            else if (m.group(2) != null) result.append(lit(m.group(2), CODE));
            else {
                String url = m.group(4);
                try {
                    URI uri = URI.create(url);
                    result.append(lit(m.group(3), AQUA).withStyle(s -> s.withUnderlined(true)
                        .withClickEvent(new ClickEvent.OpenUrl(uri)).withHoverEvent(new HoverEvent.ShowText(lit(url, TEXT)))));
                } catch (IllegalArgumentException ex) { result.append(lit(m.group(3), color)); }
            }
            last = m.end();
        }
        if (last < text.length()) result.append(lit(text.substring(last), color));
        return result;
    }
}
