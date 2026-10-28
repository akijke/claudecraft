package dev.claudechat;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.network.chat.ClickEvent;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.HoverEvent;
import net.minecraft.network.chat.MutableComponent;
import java.net.URI;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static dev.claudechat.ChatStyle.*;

/** Developer-tool event kinds: streaming, projects, git, files, agents, tasks, connectors. */
final class WorkStyle {
    /** ``` state per chat while an answer streams in line by line. */
    private static final Map<String, boolean[]> CODE = new HashMap<>();

    private WorkStyle() { }

    private static String s(JsonObject e, String key) {
        return e.has(key) && !e.get(key).isJsonNull() ? e.get(key).getAsString() : "";
    }
    private static boolean b(JsonObject e, String key) {
        return e.has(key) && !e.get(key).isJsonNull() && e.get(key).getAsBoolean();
    }
    private static int n(JsonObject e, String key) {
        return e.has(key) && !e.get(key).isJsonNull() ? e.get(key).getAsInt() : 0;
    }
    private static MutableComponent sp() { return Component.literal(" "); }

    static boolean render(JsonObject e, String mode, List<Component> out) {
        String cmd = "/" + mode + " ";
        switch (s(e, "kind")) {
            case "history" -> { }
            case "text_part" -> {
                boolean[] code = CODE.computeIfAbsent(s(e, "chat"), k -> new boolean[1]);
                if (b(e, "first")) code[0] = false;
                MutableComponent content = markdownLine(s(e, "text"), code);
                if (content != null)
                    out.add(line(mode, b(e, "first") ? lit("✦ ", CLAUDE).append(content) : Component.literal("  ").append(content)));
            }
            case "confirm" -> {
                out.add(line(mode, lit("⚠ " + s(e, "text"), b(e, "danger") ? RED : GOLD)));
                out.add(line(mode, Component.literal("  ")
                    .append(button(L.t("✔ Да"), cmd + s(e, "command"), false, b(e, "danger") ? RED : GREEN, L.t("Подтвердить")))
                    .append(lit(L.t("  (иначе просто ничего не делай)"), FAINT))));
            }
            case "tree" -> {
                out.add(line(mode, lit("≡ ", DIR).append(lit(s(e, "path"), TEXT))));
                for (JsonElement item : e.getAsJsonArray("rows")) {
                    JsonObject r = item.getAsJsonObject();
                    boolean dir = b(r, "dir");
                    String name = clean(s(r, "name")) + (dir ? "/" : "");
                    String rel = s(r, "path");
                    out.add(line(mode, lit("  " + "│ ".repeat(Math.max(0, n(r, "level") - 1)) + "├ ", FAINT)
                        .append(Component.literal(name).withStyle(st -> st.withColor(dir ? DIR : TEXT)
                            .withClickEvent(new ClickEvent.RunCommand(cmd + (dir ? "cd " : "cat ") + s(e, "path") + "/" + rel))))));
                }
                if (n(e, "more") > 0) out.add(line(mode, lit(L.t("  … и ещё ") + n(e, "more"), DIM)));
            }
            case "found" -> {
                JsonArray rows = e.getAsJsonArray("rows");
                out.add(line(mode, lit("🔍 «" + s(e, "text") + "»  ", GOLD).append(lit(rows.size() + L.t(" найдено в ") + s(e, "root"), DIM))));
                for (JsonElement item : rows) {
                    JsonObject r = item.getAsJsonObject();
                    boolean dir = b(r, "dir");
                    String rel = s(r, "path");
                    out.add(line(mode, Component.literal("  ").append(Component.literal(rel + (dir ? "/" : "")).withStyle(st -> st
                        .withColor(dir ? DIR : TEXT).withClickEvent(new ClickEvent.RunCommand(cmd + (dir ? "cd " : "cat ") + rel))))));
                }
            }
            case "projects" -> projects(e, out);
            case "project_opened" -> {
                out.add(line("cc", lit(L.t("✦ Проект "), CODE_MODE).append(lit(s(e, "name"), TEXT).withStyle(st -> st.withBold(true)))
                    .append(lit("  " + s(e, "path"), DIM))));
                if (n(e, "synced") > 0)
                    out.add(line("cc", lit("  ⇄ " + L.t("С ПК подтянуто сессий: ") + n(e, "synced"), GREEN)));
                out.add(line("cc", Component.literal("  ").append(button(L.t("☰ Чаты"), "/cc chats", false, AQUA, null)).append(sp())
                    .append(button("⇄ Git", "/cc git", false, GREEN, null)).append(sp())
                    .append(button(L.t("📁 Файлы"), "/cc cd @", false, DIR, L.t("Файловый менеджер в папке проекта"))).append(sp())
                    .append(button(L.t("◆ Агенты"), "/cc agents", false, AQUA, null))));
            }
            case "git_none" -> {
                out.add(line(mode, lit("⇄ ", GOLD).append(lit(L.t("Папка проекта не git-репозиторий: ") + s(e, "text"), DIM))));
                out.add(line(mode, Component.literal("  ").append(button("git init", "/cc git init", false, GREEN, L.t("Создать репозиторий")))
                    .append(sp()).append(button("git clone …", "/cc git clone ", true, AQUA, L.t("Склонировать в новый проект")))));
            }
            case "git_status" -> gitStatus(e, out);
            case "diff" -> diff(e, mode, out);
            case "git_log" -> {
                out.add(line(mode, lit(L.t("⇄ История"), GOLD).withStyle(st -> st.withBold(true))));
                for (JsonElement item : e.getAsJsonArray("rows")) {
                    JsonObject r = item.getAsJsonObject();
                    out.add(line(mode, lit("  " + s(r, "hash") + " ", GOLD).append(lit(s(r, "subject"), TEXT))
                        .append(lit("  " + s(r, "author") + ", " + s(r, "when"), FAINT))));
                }
            }
            case "git_branches" -> {
                out.add(line(mode, lit(L.t("⇄ Ветки"), GOLD).withStyle(st -> st.withBold(true))));
                for (JsonElement item : e.getAsJsonArray("rows")) {
                    JsonObject r = item.getAsJsonObject();
                    boolean cur = b(r, "current");
                    out.add(line(mode, lit(cur ? "  ● " : "  ○ ", cur ? GREEN : FAINT)
                        .append(cur ? lit(s(r, "name"), GREEN) : button(s(r, "name"), "/cc git switch " + s(r, "name"), false, AQUA, L.t("Переключиться")))
                        .append(lit(s(r, "upstream").isEmpty() ? "" : "  → " + s(r, "upstream"), FAINT))));
                }
                out.add(line(mode, Component.literal("  ").append(button(L.t("+ ветка"), "/cc git branch ", true, GOLD, L.t("Создать и выбрать ветку")))));
            }
            case "agents" -> agents(e, out);
            case "tasks" -> tasks(e, out);
            case "spawned" -> out.add(line(mode, lit("◆ ", CODE_MODE).append(lit(s(e, "text"), TEXT)).append(lit("  ", TEXT))
                .append(button(L.t("открыть"), "/cc select " + s(e, "target"), false, AQUA, L.t("Переключиться на этот чат")))
                .append(sp()).append(button(L.t("задачи"), "/cc tasks", false, AQUA, null))));
            case "connectors" -> connectors(e, out);
            case "need_key" -> {
                String id = s(e, "connector");
                out.add(line(mode, lit("🔑 " + id + ": ", GOLD).append(lit(L.t("нужен ключ доступа"), TEXT))));
                for (JsonElement f : e.getAsJsonArray("fields")) {
                    JsonObject field = f.getAsJsonObject();
                    out.add(line(mode, lit("  " + s(field, "var") + (b(field, "required") ? "" : L.t(" (необязательно)")), b(field, "set") ? GREEN : TEXT)
                        .append(lit(b(field, "set") ? L.t("  ✔ задан") : "  — " + s(field, "hint"), DIM))));
                }
                out.add(line(mode, Component.literal("  ").append(button(L.t("🔑 Ввести ключ"), "/claude key " + id, false, GOLD,
                    L.t("Откроется окно с полем ввода; ключ не попадёт в чат")))));
            }
            case "shell_start" -> out.add(line(mode, lit("$ ", GREEN).append(lit(s(e, "text"), TEXT).withStyle(st -> st.withBold(true)))
                .append(lit("  " + s(e, "shell") + " · " + s(e, "cwd"), FAINT)).append(sp())
                .append(button("⏹", "/kill " + s(e, "job"), false, RED, L.t("Остановить команду")))));
            case "shell_out" -> {
                for (String raw : clean(s(e, "text")).split("\\R", -1))
                    out.add(line(mode, lit("│ ", FAINT).append(PathLinks.linkify(raw, 0xD0D0D0, AQUA))));
            }
            case "shell_end" -> {
                boolean killed = b(e, "killed");
                Integer code = e.has("code") && !e.get("code").isJsonNull() ? e.get("code").getAsInt() : null;
                boolean ok = code != null && code == 0;
                MutableComponent c = lit(killed ? "⏹ " + L.t("остановлено") : ok ? "✔ exit 0" : "✖ exit " + code,
                    killed ? GOLD : ok ? GREEN : RED);
                if (e.has("seconds")) c.append(lit("  " + e.get("seconds").getAsDouble() + L.t(" с"), FAINT));
                if (n(e, "cut") > 0) c.append(lit("  " + L.t("вывод обрезан: ещё ") + n(e, "cut"), DIM));
                c.append(sp()).append(button("↻", "/sh " + s(e, "text"), true, AQUA, L.t("Повторить (можно изменить)")));
                out.add(line(mode, c));
            }
            case "terminal" -> {
                out.add(line("cc", lit("▶ ", GREEN).append(lit(L.t("Открыт терминал на ПК: "), TEXT))
                    .append(Component.literal(s(e, "command")).withStyle(st -> st.withColor(ChatStyle.CODE)
                        .withClickEvent(new ClickEvent.CopyToClipboard(s(e, "command")))
                        .withHoverEvent(new HoverEvent.ShowText(lit(L.t("Клик — скопировать команду"), TEXT)))))));
                out.add(line("cc", lit("  " + L.t("Та же сессия: в игре и на ПК видно одно и то же. Не пиши в неё в двух местах одновременно."),
                    b(e, "busy") ? GOLD : FAINT)));
            }
            case "image" -> out.add(line(mode, lit("◎ ", AQUA).append(lit(s(e, "text"), TEXT)).append(sp())
                .append(button("⧉", cmd + "open " + s(e, "path"), false, AQUA, L.t("Открыть программой по умолчанию")))
                .append(lit(s(e, "question").isEmpty() ? "" : "  «" + s(e, "question") + "»", DIM))));
            case "commands" -> {
                out.add(line(mode, lit("/ " + L.t("Команды"), GOLD).withStyle(st -> st.withBold(true))
                    .append(lit(L.t("   свои: .claude/commands/*.md в проекте или ~/.claude/commands"), FAINT))));
                String source = "";
                for (JsonElement item : e.getAsJsonArray("rows")) {
                    JsonObject r = item.getAsJsonObject();
                    if (!s(r, "source").equals(source)) {
                        source = s(r, "source");
                        out.add(line(mode, lit("  " + (source.equals("project") ? L.t("проект") : source.equals("user")
                            ? L.t("пользователь") : L.t("встроенные")), DIM).withStyle(st -> st.withUnderlined(true))));
                    }
                    String name = "/" + s(r, "name");
                    out.add(line(mode, Component.literal("  ").append(button(name, cmd + name + " ", true, AQUA,
                            s(r, "path").isEmpty() ? null : s(r, "path")))
                        .append(lit(s(r, "hint").isEmpty() ? "" : " " + s(r, "hint"), FAINT))
                        .append(lit("  " + s(r, "description"), DIM))));
                }
            }
            case "slash" -> out.add(line(mode, lit("/ ", AQUA).append(lit(s(e, "text"), TEXT))
                .withStyle(st -> st.withHoverEvent(new HoverEvent.ShowText(lit(s(e, "prompt"), TEXT))))
                .append(lit(L.t("  → в очередь Claude"), FAINT))));
            case "doctor" -> {
                String overall = s(e, "overall");
                out.add(line(mode, lit(L.t("◎ Диагностика"), GOLD).withStyle(st -> st.withBold(true))
                    .append(lit(overall.equals("ok") ? L.t("  всё в порядке") : overall.equals("warn") ? L.t("  есть предупреждения")
                        : L.t("  есть проблемы"), overall.equals("ok") ? GREEN : overall.equals("warn") ? GOLD : RED)).append(sp())
                    .append(button("⟳ " + L.t("ещё раз"), cmd + "doctor", false, AQUA, null))));
                for (JsonElement item : e.getAsJsonArray("rows")) {
                    JsonObject r = item.getAsJsonObject();
                    String st = s(r, "status");
                    int color = st.equals("ok") ? GREEN : st.equals("warn") ? GOLD : st.equals("fail") ? RED : DIM;
                    String icon = st.equals("ok") ? "✔ " : st.equals("warn") ? "⚠ " : st.equals("fail") ? "✖ " : "· ";
                    MutableComponent row = lit("  " + icon, color).append(lit(s(r, "name") + "  ", TEXT)).append(lit(s(r, "value"), DIM));
                    if (!s(r, "hint").isEmpty() && !st.equals("ok"))
                        row.withStyle(x -> x.withHoverEvent(new HoverEvent.ShowText(lit(s(r, "hint"), TEXT))));
                    out.add(line(mode, row));
                    if (!s(r, "hint").isEmpty() && (st.equals("warn") || st.equals("fail")))
                        out.add(line(mode, lit("      → " + s(r, "hint"), FAINT)));
                }
            }
            case "edit_diff" -> {
                out.add(line(mode, lit("± ", GOLD).append(lit(s(e, "text"), TEXT)).append(sp())
                    .append(lit("+" + n(e, "added"), GREEN)).append(lit(" −" + n(e, "removed"), RED)).append(sp())
                    .append(button("✎", "/edit " + s(e, "path"), false, AQUA, L.t("Открыть во встроенном редакторе"))).append(sp())
                    .append(button("⟳ " + L.t("откатить"), cmd + "undo", false, GOLD,
                        L.t("Вернуть все файлы, изменённые Claude в этом запросе")))));
                editDiff(e, mode, out, 12);
            }
            case "undo" -> {
                out.add(line(mode, lit("⟳ " + L.t("Откат правок Claude"), GOLD).withStyle(st -> st.withBold(true))
                    .append(lit("  «" + s(e, "prompt") + "»", FAINT))));
                for (String key : new String[]{"restored", "removed", "skipped"}) {
                    if (!e.has(key)) continue;
                    String label = key.equals("restored") ? L.t("  восстановлен ") : key.equals("removed")
                        ? L.t("  удалён (создан Claude) ") : L.t("  не удалось ");
                    int color = key.equals("skipped") ? RED : key.equals("removed") ? DIM : GREEN;
                    for (JsonElement p : e.getAsJsonArray(key)) out.add(line(mode, lit(label, color).append(lit(p.getAsString(), TEXT))));
                }
                if (n(e, "left") > 0) out.add(line(mode, lit("  " + L.t("Ещё запросов для отката: ") + n(e, "left") + "  ", FAINT)
                    .append(button("⟳ " + L.t("ещё"), cmd + "undo", false, GOLD, null)).append(sp())
                    .append(button(L.t("история"), cmd + "undo list", false, AQUA, null))));
            }
            case "undo_list" -> {
                out.add(line(mode, lit("⟳ " + L.t("Правки Claude"), GOLD).withStyle(st -> st.withBold(true))
                    .append(lit(L.t("   новые сверху; откат идёт по одному запросу"), FAINT))));
                JsonArray turns = e.getAsJsonArray("turns");
                if (turns.isEmpty()) out.add(line(mode, lit(L.t("  пока ничего"), DIM)));
                boolean first = true;
                for (JsonElement t : turns) {
                    JsonObject turn = t.getAsJsonObject();
                    MutableComponent row = lit("  «" + s(turn, "prompt") + "»  ", TEXT)
                        .append(lit(turn.getAsJsonArray("files").size() + L.t(" файл(ов)"), DIM));
                    if (first) row.append(sp()).append(button("⟳", cmd + "undo", false, GOLD, L.t("Откатить этот запрос")));
                    out.add(line(mode, row));
                    first = false;
                }
            }
            case "model" -> {
                String model = s(e, "model"), effort = s(e, "effort");
                MutableComponent row = lit("◆ " + L.t("Модель") + ": ", GOLD);
                for (String m : new String[]{"default", "opus", "sonnet", "haiku"}) {
                    boolean on = m.equals(model.isEmpty() ? "default" : model);
                    row.append(button((on ? "● " : "") + m, cmd + "model " + m, false, on ? GREEN : AQUA,
                        m.equals("default") ? L.t("Модель по умолчанию из Claude Code") : null)).append(sp());
                }
                if (!model.isEmpty() && !java.util.List.of("opus", "sonnet", "haiku").contains(model))
                    row.append(lit("● " + model, GREEN));
                out.add(line(mode, row));
                MutableComponent eff = lit("  " + L.t("Усилие") + ": ", DIM);
                for (String level : new String[]{"default", "low", "medium", "high", "xhigh", "max"}) {
                    boolean on = level.equals(effort.isEmpty() ? "default" : effort);
                    eff.append(button((on ? "● " : "") + level, cmd + "effort " + level, false, on ? GREEN : AQUA,
                        level.equals("max") ? L.t("Дольше думает, тратит больше лимита") : null)).append(sp());
                }
                out.add(line(mode, eff));
                out.add(line(mode, lit("  " + L.t("Для этого чата, со следующего запроса. Можно и полный id: model claude-…"), FAINT)));
            }
            case "chats" -> { return false; }
            default -> { return false; }
        }
        return true;
    }

    private static void projects(JsonObject e, List<Component> out) {
        out.add(line("cc", lit(L.t("✦ Проекты"), CODE_MODE).withStyle(st -> st.withBold(true))
            .append(lit(L.t("   рабочая папка Claude = папка проекта"), FAINT))));
        for (JsonElement item : e.getAsJsonArray("rows")) {
            JsonObject r = item.getAsJsonObject();
            boolean cur = b(r, "current");
            MutableComponent row = lit(cur ? "  ● " : "  ○ ", cur ? GREEN : FAINT)
                .append(cur ? lit(s(r, "name"), GREEN).withStyle(st -> st.withBold(true))
                    : button(s(r, "name"), "/cc project open " + s(r, "name"), false, AQUA, s(r, "path")));
            if (b(r, "git")) row.append(lit("  ⇄", GREEN));
            if (n(r, "running") > 0) row.append(lit("  ▶ " + n(r, "running"), GOLD));
            row.append(lit("  " + n(r, "chats") + L.t(" чат."), FAINT));
            if (!b(r, "exists")) row.append(lit(L.t("  нет папки!"), RED));
            out.add(line("cc", row));
            out.add(line("cc", lit("      " + s(r, "path"), FAINT)));
        }
        out.add(line("cc", Component.literal("  ")
            .append(button(L.t("📁 Выбрать папку…"), "/cc project pick", false, GREEN,
                L.t("Окно выбора папки на ПК; сессии Claude Code этой папки подтянутся сами"))).append(sp())
            .append(button(L.t("+ здесь"), "/cc project here", false, GOLD, L.t("Проект из папки обзора: ") + s(e, "browsing"))).append(sp())
            .append(button(L.t("+ новый"), "/cc project new ", true, GOLD, L.t("Создать папку в workspace"))).append(sp())
            .append(button(L.t("+ по пути"), "/cc project add ", true, GOLD, L.t("project add <имя> <путь>"))).append(sp())
            .append(button("⇄ clone", "/cc git clone ", true, GREEN, L.t("git clone <url> [имя]")))));
    }

    private static void gitStatus(JsonObject e, List<Component> out) {
        MutableComponent head = lit("⇄ ", GREEN).append(lit(s(e, "branch"), TEXT).withStyle(st -> st.withBold(true)));
        if (!s(e, "upstream").isEmpty()) head.append(lit("  → " + s(e, "upstream"), FAINT));
        if (n(e, "ahead") > 0) head.append(lit("  ↑" + n(e, "ahead"), GOLD));
        if (n(e, "behind") > 0) head.append(lit("  ↓" + n(e, "behind"), AQUA));
        head.append(lit("  " + s(e, "project"), CODE_MODE));
        out.add(line("cc", head));
        if (!s(e, "url").isEmpty()) {
            try {
                URI uri = URI.create(s(e, "url"));
                out.add(line("cc", lit("  origin ", FAINT).append(Component.literal(uri.toString()).withStyle(st -> st
                    .withColor(AQUA).withUnderlined(true).withClickEvent(new ClickEvent.OpenUrl(uri))))));
            } catch (IllegalArgumentException ignored) { }
        } else if (!s(e, "remote").isEmpty()) {
            out.add(line("cc", lit("  origin " + s(e, "remote"), FAINT)));
        }
        if (!s(e, "last").isEmpty()) out.add(line("cc", lit(L.t("  последний: ") + s(e, "last"), FAINT)));
        JsonArray files = e.getAsJsonArray("files");
        if (files.isEmpty()) out.add(line("cc", lit(L.t("  ✔ всё закоммичено"), GREEN)));
        for (JsonElement item : files) {
            JsonObject f = item.getAsJsonObject();
            String code = s(f, "code"), path = s(f, "path");
            int color = code.contains("?") ? DIM : code.contains("D") ? RED : code.charAt(0) != ' ' ? GREEN : GOLD;
            out.add(line("cc", lit("  " + code.replace(' ', '·') + " ", color).append(Component.literal(path).withStyle(st -> st
                .withColor(TEXT).withClickEvent(new ClickEvent.RunCommand("/cc git diff " + path))
                .withHoverEvent(new HoverEvent.ShowText(lit(L.t("Показать изменения"), TEXT)))))));
        }
        if (n(e, "more") > 0) out.add(line("cc", lit(L.t("  … и ещё ") + n(e, "more"), DIM)));
        out.add(line("cc", Component.literal("  ")
            .append(button("diff", "/cc git diff", false, AQUA, L.t("Все изменения"))).append(sp())
            .append(button("✔ commit", "/cc git commit ", true, GREEN, L.t("Впиши сообщение; пусто — Claude напишет сам"))).append(sp())
            .append(button("✦ commit", "/cc git commit", false, CODE_MODE, L.t("Claude посмотрит diff и закоммитит"))).append(sp())
            .append(button("↑ push", "/cc git push", false, GOLD, null)).append(sp())
            .append(button("↓ pull", "/cc git pull", false, AQUA, null)).append(sp())
            .append(button(L.t("ветки"), "/cc git branch", false, AQUA, null)).append(sp())
            .append(button("log", "/cc git log", false, AQUA, null))));
        if (s(e, "remote").isEmpty())
            out.add(line("cc", Component.literal("  ").append(button(L.t("Создать на GitHub"), "/cc git github", false, GOLD,
                L.t("gh repo create (private) + push; нужен gh auth login"))).append(sp())
                .append(button("origin …", "/cc git remote ", true, AQUA, L.t("Привязать существующий репозиторий: git remote <url>")))));
    }

    /** Diff of a file edit by Claude (bridge backends/undo.py): unified lines without header. */
    static void editDiff(JsonObject d, String mode, List<Component> out, int max) {
        if (d == null || !d.has("lines")) return;
        JsonArray lines = d.getAsJsonArray("lines");
        if (b(d, "new")) out.add(line(mode, lit(L.t("  новый файл"), DIM)));
        int shown = 0;
        for (JsonElement l : lines) {
            if (shown++ >= max) break;
            String raw = l.getAsString();
            int color = raw.startsWith("+") ? GREEN : raw.startsWith("-") ? RED : raw.startsWith("@@") ? AQUA : DIM;
            out.add(line(mode, lit("  " + clean(raw), color)));
        }
        int more = lines.size() - Math.min(max, lines.size()) + n(d, "more");
        if (more > 0) out.add(line(mode, lit("  … " + L.t("ещё строк: ") + more, FAINT)));
    }

    private static void diff(JsonObject e, String mode, List<Component> out) {
        out.add(line(mode, lit("± ", GOLD).append(lit(s(e, "path").isEmpty() ? L.t("все изменения") : s(e, "path"), TEXT))
            .append(lit(b(e, "staged") ? L.t("  (подготовленные)") : "", FAINT))));
        if (b(e, "empty")) { out.add(line(mode, lit(L.t("  нет изменений"), DIM))); return; }
        for (String raw : clean(s(e, "text")).split("\\R")) {
            if (raw.startsWith("diff --git") || raw.startsWith("index ") || raw.startsWith("--- ") || raw.startsWith("+++ ")) {
                if (raw.startsWith("+++ ")) out.add(line(mode, lit("  " + raw.substring(4).replaceFirst("^b/", ""), GOLD).withStyle(st -> st.withBold(true))));
                continue;
            }
            int color = raw.startsWith("+") ? GREEN : raw.startsWith("-") ? RED : raw.startsWith("@@") ? AQUA : DIM;
            out.add(line(mode, lit("  " + raw, color)));
        }
        if (b(e, "truncated")) out.add(line(mode, lit(L.t("  … diff длиннее, показано начало"), DIM)));
    }

    private static void agents(JsonObject e, List<Component> out) {
        out.add(line("cc", lit(L.t("◆ Агенты"), CODE_MODE).withStyle(st -> st.withBold(true))
            .append(lit(L.t("   Claude сам поручает им задачи; можно и напрямую"), FAINT))));
        for (JsonElement item : e.getAsJsonArray("rows")) {
            JsonObject r = item.getAsJsonObject();
            boolean on = b(r, "enabled");
            String name = s(r, "name");
            out.add(line("cc", lit(on ? "  ● " : "  ○ ", on ? GREEN : FAINT)
                .append(button(name, "/cc agent " + name + " ", true, on ? AQUA : FAINT, s(r, "description")))
                .append(lit("  " + s(r, "source"), FAINT)).append(sp())
                .append(button(on ? L.t("выкл") : L.t("вкл"), "/cc agent " + (on ? "off " : "on ") + name, false, on ? DIM : GREEN, null))
                .append(sp()).append(button("∥", "/cc spawn @" + name + " ", true, CODE_MODE, L.t("Запустить параллельно в отдельном чате")))));
        }
        out.add(line("cc", Component.literal("  ").append(button(L.t("+ свой агент"), "/cc agent new ", true, GOLD,
            L.t("agent new <имя> <чем занимается>"))).append(lit(L.t("  или файлы .claude/agents/*.md в проекте"), FAINT))));
    }

    private static void tasks(JsonObject e, List<Component> out) {
        JsonArray rows = e.getAsJsonArray("rows");
        out.add(line("cc", lit(L.t("▶ Задачи"), GOLD).withStyle(st -> st.withBold(true)).append(lit("  " + rows.size() + L.t(" активных"), FAINT))));
        if (rows.isEmpty()) out.add(line("cc", lit(L.t("  сейчас ничего не выполняется"), DIM)));
        for (JsonElement item : rows) {
            JsonObject r = item.getAsJsonObject();
            String state = s(r, "state");
            String code = s(r, "code");  // language-independent state from the bridge
            int color = code.equals("waiting") ? GOLD : code.equals("running") ? GREEN : DIM;
            MutableComponent row = lit("  ● ", color).append(lit(s(r, "title"), TEXT))
                .append(lit("  " + (s(r, "project").isEmpty() ? L.t("чат") : s(r, "project")), CODE_MODE))
                .append(lit("  " + state + (n(r, "seconds") > 0 ? " " + n(r, "seconds") + L.t(" с") : ""), color));
            if (n(r, "queue") > 0) row.append(lit("  +" + n(r, "queue"), FAINT));
            out.add(line("cc", row));
            if (!s(r, "step").isEmpty()) out.add(line("cc", lit("      " + s(r, "step"), FAINT)));
            String m = s(r, "mode");
            out.add(line("cc", Component.literal("      ")
                .append(button(L.t("открыть"), "/" + m + " select " + s(r, "id"), false, AQUA, null)).append(sp())
                .append(button("f …", "/" + m + " select " + s(r, "id"), false, AQUA, L.t("Открой чат и напиши /") + m + L.t(" f <текст>"))).append(sp())
                .append(button(L.t("⏹ стоп"), "/cc stop " + s(r, "id"), false, RED, L.t("Остановить и очистить очередь")))));
        }
        out.add(line("cc", Component.literal("  ").append(button(L.t("∥ новая параллельная задача"), "/cc spawn ", true, CODE_MODE,
            L.t("Отдельный чат в этом проекте; текущий не прерывается")))));
    }

    private static void connectors(JsonObject e, List<Component> out) {
        out.add(line("cc", lit(L.t("⚙ Коннекторы"), GOLD).withStyle(st -> st.withBold(true))
            .append(lit(L.t("   включаются со следующего запроса /cc"), FAINT))));
        String group = "";
        for (JsonElement item : e.getAsJsonArray("rows")) {
            JsonObject r = item.getAsJsonObject();
            if (!s(r, "group").equals(group)) {
                group = s(r, "group");
                out.add(line("cc", lit("  " + group, DIM).withStyle(st -> st.withUnderlined(true))));
            }
            boolean on = b(r, "enabled");
            String id = s(r, "id"), status = s(r, "status");
            MutableComponent row = lit(on ? "  ● " : "  ○ ", on ? (status.equals("failed") ? RED : GREEN) : FAINT)
                .append(lit(s(r, "name"), on ? TEXT : DIM).withStyle(st -> st.withHoverEvent(new HoverEvent.ShowText(lit(s(r, "about"), TEXT)))))
                .append(sp());
            if (on) {
                if (!status.isEmpty()) row.append(lit(status.equals("connected") ? "✔ " : status + " ", status.equals("connected") ? GREEN : RED));
                row.append(button(L.t("выкл"), "/cc disconnect " + id, false, DIM, null));
                if (b(r, "oauth")) row.append(sp()).append(button(L.t("выйти"), "/cc connect " + id + " signout", false, DIM,
                    L.t("Забыть вход (токен удаляется из secrets.json)")));
            } else if (b(r, "oauth")) {
                boolean signedIn = r.has("missing") && r.getAsJsonArray("missing").isEmpty();
                row.append(button(signedIn ? L.t("подключить") : L.t("◎ войти через браузер"), "/cc connect " + id, false, GREEN,
                    s(r, "about") + "\n" + L.t("Откроется сайт сервиса; ключи вводить не нужно")));
            } else if (b(r, "needs_node")) {
                row.append(lit(L.t("нужен Node.js"), RED));
            } else {
                boolean needsKey = r.has("missing") && !r.getAsJsonArray("missing").isEmpty();
                row.append(button(needsKey ? L.t("🔑 подключить") : L.t("подключить"), "/cc connect " + id, false, GREEN, s(r, "about")));
            }
            out.add(line("cc", row));
        }
    }
}
