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
                    .append(button("✔ Да", cmd + s(e, "command"), false, b(e, "danger") ? RED : GREEN, "Подтвердить"))
                    .append(lit("  (иначе просто ничего не делай)", FAINT))));
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
                if (n(e, "more") > 0) out.add(line(mode, lit("  … и ещё " + n(e, "more"), DIM)));
            }
            case "found" -> {
                JsonArray rows = e.getAsJsonArray("rows");
                out.add(line(mode, lit("🔍 «" + s(e, "text") + "»  ", GOLD).append(lit(rows.size() + " найдено в " + s(e, "root"), DIM))));
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
                out.add(line("cc", lit("✦ Проект ", CODE_MODE).append(lit(s(e, "name"), TEXT).withStyle(st -> st.withBold(true)))
                    .append(lit("  " + s(e, "path"), DIM))));
                out.add(line("cc", Component.literal("  ").append(button("☰ Чаты", "/cc chats", false, AQUA, null)).append(sp())
                    .append(button("⇄ Git", "/cc git", false, GREEN, null)).append(sp())
                    .append(button("📁 Файлы", "/cc cd @", false, DIR, "Файловый менеджер в папке проекта")).append(sp())
                    .append(button("◆ Агенты", "/cc agents", false, AQUA, null))));
            }
            case "git_none" -> {
                out.add(line(mode, lit("⇄ ", GOLD).append(lit("Папка проекта не git-репозиторий: " + s(e, "text"), DIM))));
                out.add(line(mode, Component.literal("  ").append(button("git init", "/cc git init", false, GREEN, "Создать репозиторий"))
                    .append(sp()).append(button("git clone …", "/cc git clone ", true, AQUA, "Склонировать в новый проект"))));
            }
            case "git_status" -> gitStatus(e, out);
            case "diff" -> diff(e, mode, out);
            case "git_log" -> {
                out.add(line(mode, lit("⇄ История", GOLD).withStyle(st -> st.withBold(true))));
                for (JsonElement item : e.getAsJsonArray("rows")) {
                    JsonObject r = item.getAsJsonObject();
                    out.add(line(mode, lit("  " + s(r, "hash") + " ", GOLD).append(lit(s(r, "subject"), TEXT))
                        .append(lit("  " + s(r, "author") + ", " + s(r, "when"), FAINT))));
                }
            }
            case "git_branches" -> {
                out.add(line(mode, lit("⇄ Ветки", GOLD).withStyle(st -> st.withBold(true))));
                for (JsonElement item : e.getAsJsonArray("rows")) {
                    JsonObject r = item.getAsJsonObject();
                    boolean cur = b(r, "current");
                    out.add(line(mode, lit(cur ? "  ● " : "  ○ ", cur ? GREEN : FAINT)
                        .append(cur ? lit(s(r, "name"), GREEN) : button(s(r, "name"), "/cc git switch " + s(r, "name"), false, AQUA, "Переключиться"))
                        .append(lit(s(r, "upstream").isEmpty() ? "" : "  → " + s(r, "upstream"), FAINT))));
                }
                out.add(line(mode, Component.literal("  ").append(button("+ ветка", "/cc git branch ", true, GOLD, "Создать и выбрать ветку"))));
            }
            case "agents" -> agents(e, out);
            case "tasks" -> tasks(e, out);
            case "spawned" -> out.add(line(mode, lit("◆ ", CODE_MODE).append(lit(s(e, "text"), TEXT)).append(lit("  ", TEXT))
                .append(button("открыть", "/cc select " + s(e, "target"), false, AQUA, "Переключиться на этот чат"))
                .append(sp()).append(button("задачи", "/cc tasks", false, AQUA, null))));
            case "connectors" -> connectors(e, out);
            case "need_key" -> {
                String id = s(e, "connector");
                out.add(line(mode, lit("🔑 " + id + ": ", GOLD).append(lit("нужен ключ доступа", TEXT))));
                for (JsonElement f : e.getAsJsonArray("fields")) {
                    JsonObject field = f.getAsJsonObject();
                    out.add(line(mode, lit("  " + s(field, "var") + (b(field, "required") ? "" : " (необязательно)"), b(field, "set") ? GREEN : TEXT)
                        .append(lit(b(field, "set") ? "  ✔ задан" : "  — " + s(field, "hint"), DIM))));
                }
                out.add(line(mode, Component.literal("  ").append(button("🔑 Ввести ключ", "/claude key " + id, false, GOLD,
                    "Откроется окно с полем ввода; ключ не попадёт в чат"))));
            }
            case "chats" -> { return false; }
            default -> { return false; }
        }
        return true;
    }

    private static void projects(JsonObject e, List<Component> out) {
        out.add(line("cc", lit("✦ Проекты", CODE_MODE).withStyle(st -> st.withBold(true))
            .append(lit("   рабочая папка Claude = папка проекта", FAINT))));
        for (JsonElement item : e.getAsJsonArray("rows")) {
            JsonObject r = item.getAsJsonObject();
            boolean cur = b(r, "current");
            MutableComponent row = lit(cur ? "  ● " : "  ○ ", cur ? GREEN : FAINT)
                .append(cur ? lit(s(r, "name"), GREEN).withStyle(st -> st.withBold(true))
                    : button(s(r, "name"), "/cc project open " + s(r, "name"), false, AQUA, s(r, "path")));
            if (b(r, "git")) row.append(lit("  ⇄", GREEN));
            if (n(r, "running") > 0) row.append(lit("  ▶ " + n(r, "running"), GOLD));
            row.append(lit("  " + n(r, "chats") + " чат.", FAINT));
            if (!b(r, "exists")) row.append(lit("  нет папки!", RED));
            out.add(line("cc", row));
            out.add(line("cc", lit("      " + s(r, "path"), FAINT)));
        }
        out.add(line("cc", Component.literal("  ")
            .append(button("+ здесь", "/cc project here", false, GOLD, "Проект из папки обзора: " + s(e, "browsing"))).append(sp())
            .append(button("+ новый", "/cc project new ", true, GOLD, "Создать папку в workspace")).append(sp())
            .append(button("+ по пути", "/cc project add ", true, GOLD, "project add <имя> <путь>")).append(sp())
            .append(button("⇄ clone", "/cc git clone ", true, GREEN, "git clone <url> [имя]"))));
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
        if (!s(e, "last").isEmpty()) out.add(line("cc", lit("  последний: " + s(e, "last"), FAINT)));
        JsonArray files = e.getAsJsonArray("files");
        if (files.isEmpty()) out.add(line("cc", lit("  ✔ всё закоммичено", GREEN)));
        for (JsonElement item : files) {
            JsonObject f = item.getAsJsonObject();
            String code = s(f, "code"), path = s(f, "path");
            int color = code.contains("?") ? DIM : code.contains("D") ? RED : code.charAt(0) != ' ' ? GREEN : GOLD;
            out.add(line("cc", lit("  " + code.replace(' ', '·') + " ", color).append(Component.literal(path).withStyle(st -> st
                .withColor(TEXT).withClickEvent(new ClickEvent.RunCommand("/cc git diff " + path))
                .withHoverEvent(new HoverEvent.ShowText(lit("Показать изменения", TEXT)))))));
        }
        if (n(e, "more") > 0) out.add(line("cc", lit("  … и ещё " + n(e, "more"), DIM)));
        out.add(line("cc", Component.literal("  ")
            .append(button("diff", "/cc git diff", false, AQUA, "Все изменения")).append(sp())
            .append(button("✔ commit", "/cc git commit ", true, GREEN, "Впиши сообщение; пусто — Claude напишет сам")).append(sp())
            .append(button("✦ commit", "/cc git commit", false, CODE_MODE, "Claude посмотрит diff и закоммитит")).append(sp())
            .append(button("↑ push", "/cc git push", false, GOLD, null)).append(sp())
            .append(button("↓ pull", "/cc git pull", false, AQUA, null)).append(sp())
            .append(button("ветки", "/cc git branch", false, AQUA, null)).append(sp())
            .append(button("log", "/cc git log", false, AQUA, null))));
        if (s(e, "remote").isEmpty())
            out.add(line("cc", Component.literal("  ").append(button("Создать на GitHub", "/cc git github", false, GOLD,
                "gh repo create (private) + push; нужен gh auth login")).append(sp())
                .append(button("origin …", "/cc git remote ", true, AQUA, "Привязать существующий репозиторий: git remote <url>"))));
    }

    private static void diff(JsonObject e, String mode, List<Component> out) {
        out.add(line(mode, lit("± ", GOLD).append(lit(s(e, "path").isEmpty() ? "все изменения" : s(e, "path"), TEXT))
            .append(lit(b(e, "staged") ? "  (подготовленные)" : "", FAINT))));
        if (b(e, "empty")) { out.add(line(mode, lit("  нет изменений", DIM))); return; }
        for (String raw : clean(s(e, "text")).split("\\R")) {
            if (raw.startsWith("diff --git") || raw.startsWith("index ") || raw.startsWith("--- ") || raw.startsWith("+++ ")) {
                if (raw.startsWith("+++ ")) out.add(line(mode, lit("  " + raw.substring(4).replaceFirst("^b/", ""), GOLD).withStyle(st -> st.withBold(true))));
                continue;
            }
            int color = raw.startsWith("+") ? GREEN : raw.startsWith("-") ? RED : raw.startsWith("@@") ? AQUA : DIM;
            out.add(line(mode, lit("  " + raw, color)));
        }
        if (b(e, "truncated")) out.add(line(mode, lit("  … diff длиннее, показано начало", DIM)));
    }

    private static void agents(JsonObject e, List<Component> out) {
        out.add(line("cc", lit("◆ Агенты", CODE_MODE).withStyle(st -> st.withBold(true))
            .append(lit("   Claude сам поручает им задачи; можно и напрямую", FAINT))));
        for (JsonElement item : e.getAsJsonArray("rows")) {
            JsonObject r = item.getAsJsonObject();
            boolean on = b(r, "enabled");
            String name = s(r, "name");
            out.add(line("cc", lit(on ? "  ● " : "  ○ ", on ? GREEN : FAINT)
                .append(button(name, "/cc agent " + name + " ", true, on ? AQUA : FAINT, s(r, "description")))
                .append(lit("  " + s(r, "source"), FAINT)).append(sp())
                .append(button(on ? "выкл" : "вкл", "/cc agent " + (on ? "off " : "on ") + name, false, on ? DIM : GREEN, null))
                .append(sp()).append(button("∥", "/cc spawn @" + name + " ", true, CODE_MODE, "Запустить параллельно в отдельном чате"))));
        }
        out.add(line("cc", Component.literal("  ").append(button("+ свой агент", "/cc agent new ", true, GOLD,
            "agent new <имя> <чем занимается>")).append(lit("  или файлы .claude/agents/*.md в проекте", FAINT))));
    }

    private static void tasks(JsonObject e, List<Component> out) {
        JsonArray rows = e.getAsJsonArray("rows");
        out.add(line("cc", lit("▶ Задачи", GOLD).withStyle(st -> st.withBold(true)).append(lit("  " + rows.size() + " активных", FAINT))));
        if (rows.isEmpty()) out.add(line("cc", lit("  сейчас ничего не выполняется", DIM)));
        for (JsonElement item : rows) {
            JsonObject r = item.getAsJsonObject();
            String state = s(r, "state");
            int color = state.startsWith("ждёт") ? GOLD : state.equals("работает") ? GREEN : DIM;
            MutableComponent row = lit("  ● ", color).append(lit(s(r, "title"), TEXT))
                .append(lit("  " + (s(r, "project").isEmpty() ? "чат" : s(r, "project")), CODE_MODE))
                .append(lit("  " + state + (n(r, "seconds") > 0 ? " " + n(r, "seconds") + " с" : ""), color));
            if (n(r, "queue") > 0) row.append(lit("  +" + n(r, "queue"), FAINT));
            out.add(line("cc", row));
            if (!s(r, "step").isEmpty()) out.add(line("cc", lit("      " + s(r, "step"), FAINT)));
            String m = s(r, "mode");
            out.add(line("cc", Component.literal("      ")
                .append(button("открыть", "/" + m + " select " + s(r, "id"), false, AQUA, null)).append(sp())
                .append(button("f …", "/" + m + " select " + s(r, "id"), false, AQUA, "Открой чат и напиши /" + m + " f <текст>")).append(sp())
                .append(button("⏹ стоп", "/cc stop " + s(r, "id"), false, RED, "Остановить и очистить очередь"))));
        }
        out.add(line("cc", Component.literal("  ").append(button("∥ новая параллельная задача", "/cc spawn ", true, CODE_MODE,
            "Отдельный чат в этом проекте; текущий не прерывается"))));
    }

    private static void connectors(JsonObject e, List<Component> out) {
        out.add(line("cc", lit("⚙ Коннекторы", GOLD).withStyle(st -> st.withBold(true))
            .append(lit("   включаются со следующего запроса /cc", FAINT))));
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
                row.append(button("выкл", "/cc disconnect " + id, false, DIM, null));
            } else if (b(r, "needs_node")) {
                row.append(lit("нужен Node.js", RED));
            } else {
                boolean needsKey = r.has("missing") && !r.getAsJsonArray("missing").isEmpty();
                row.append(button(needsKey ? "🔑 подключить" : "подключить", "/cc connect " + id, false, GREEN, s(r, "about")));
            }
            out.add(line("cc", row));
        }
    }
}
