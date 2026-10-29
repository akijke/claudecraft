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
import java.util.Locale;
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
            case "bg_task" -> out.add(line(mode, lit("▶ ", GREEN).append(lit(s(e, "text"), DIM)).append(sp())
                .append(button(L.t("вывод"), cmd + "bg out " + s(e, "task"), false, AQUA, null)).append(sp())
                .append(button(L.t("⏹ стоп"), cmd + "bg stop " + s(e, "task"), false, RED, null))));
            case "spawned" -> out.add(line(mode, lit("◆ ", CODE_MODE).append(lit(s(e, "text"), TEXT)).append(lit("  ", TEXT))
                .append(button(L.t("открыть"), "/cc select " + s(e, "target"), false, AQUA, L.t("Переключиться на этот чат")))
                .append(sp()).append(button(L.t("задачи"), "/cc tasks", false, AQUA, null))
                .append(lit(s(e, "branch").isEmpty() ? "" : "  ⇄ " + s(e, "branch"), FAINT)
                    .withStyle(st -> st.withHoverEvent(new HoverEvent.ShowText(lit(L.t("Свой worktree: ") + s(e, "worktree"), TEXT)))))));
            case "worktree_ready" -> {
                String id = s(e, "target");
                out.add(line(mode, lit("⇄ " + s(e, "text"), GREEN).append(lit("  " + s(e, "branch"), FAINT))));
                out.add(line(mode, Component.literal("  ")
                    .append(button("✔ merge", "/cc worktree merge " + id, false, GREEN, L.t("Закоммитить и влить в текущую ветку проекта")))
                    .append(sp()).append(button("diff", "/cc worktree diff " + id, false, AQUA, L.t("Что поменяла задача")))
                    .append(sp()).append(button(L.t("открыть чат"), "/cc select " + id, false, AQUA, null))
                    .append(sp()).append(button("✖ drop", "/cc worktree drop " + id, false, RED, L.t("Удалить worktree и ветку без слияния")))));
            }
            case "worktrees" -> {
                out.add(line(mode, lit("⇄ Worktrees", GOLD).withStyle(st -> st.withBold(true))));
                JsonArray rows = e.getAsJsonArray("rows");
                if (rows.isEmpty()) out.add(line(mode, lit(L.t("  нет — /cc spawn <задача> создаёт worktree в git-проекте"), FAINT)));
                for (JsonElement item : rows) {
                    JsonObject r = item.getAsJsonObject();
                    String id = s(r, "id");
                    out.add(line(mode, lit(b(r, "busy") ? "  ▶ " : "  ● ", b(r, "busy") ? GOLD : GREEN).append(lit(s(r, "title"), TEXT))
                        .append(lit("  " + s(r, "branch") + "  ", FAINT))
                        .append(button("merge", "/cc worktree merge " + id, false, GREEN, null)).append(sp())
                        .append(button("diff", "/cc worktree diff " + id, false, AQUA, null)).append(sp())
                        .append(button("drop", "/cc worktree drop " + id, false, RED, null))));
                }
            }
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
            case "styles" -> {
                String current = s(e, "current");
                out.add(line(mode, lit("✦ " + L.t("Стиль ответов"), GOLD).withStyle(st -> st.withBold(true))
                    .append(lit(L.t("   проект ") + s(e, "project"), FAINT))));
                for (JsonElement item : e.getAsJsonArray("rows")) {
                    JsonObject r = item.getAsJsonObject();
                    boolean on = s(r, "name").equals(current);
                    MutableComponent row = Component.literal("  ").append(button((on ? "● " : "") + s(r, "name"), cmd + "style " + s(r, "name"),
                        false, on ? GREEN : AQUA, null)).append(lit("  " + s(r, "description"), DIM));
                    if (!s(r, "path").isEmpty()) row.append(sp()).append(button("✎", "/edit " + s(r, "path"), false, DIM, s(r, "path")));
                    out.add(line(mode, row));
                }
                out.add(line(mode, lit(L.t("  добавка к промпту: "), TEXT)
                    .append(lit(s(e, "append").isEmpty() ? L.t("нет  ") : "«" + s(e, "append") + "»  ", s(e, "append").isEmpty() ? FAINT : TEXT))
                    .append(button(L.t("изменить"), cmd + "style append " + s(e, "append"), true, AQUA,
                        L.t("Напр.: «отвечай по-русски, код комментируй по-английски»")))
                    .append(s(e, "append").isEmpty() ? Component.literal("") : Component.literal(" ").append(button(L.t("убрать"), cmd + "style append off", false, DIM, null)))));
                out.add(line(mode, lit(L.t("  свои стили: .claude/output-styles/*.md · действует со следующего запроса"), FAINT)));
            }
            case "hooks" -> {
                out.add(line(mode, lit("⚙ " + L.t("Хуки"), GOLD).withStyle(st -> st.withBold(true))
                    .append(lit(b(e, "loaded") ? L.t("   выполняет сам Claude Code") : L.t("   не загружаются: /cc memory on"), b(e, "loaded") ? FAINT : RED))
                    .append(sp()).append(button("+", cmd + "hooks add PostToolUse Edit|Write -- ", true, GREEN,
                        L.t("Напр.: PostToolUse Edit|Write -- npm run lint")))
                    .append(sp()).append(button(L.t("журнал"), cmd + "hooks log", false, AQUA, null))));
                JsonArray rows = e.getAsJsonArray("rows");
                if (rows.isEmpty()) out.add(line(mode, lit(L.t("  хуков нет"), FAINT)));
                for (JsonElement item : rows) {
                    JsonObject r = item.getAsJsonObject();
                    MutableComponent row = lit("  " + s(r, "event"), AQUA).append(lit(s(r, "matcher").isEmpty() ? "" : " [" + s(r, "matcher") + "]", GOLD))
                        .append(lit("  " + s(r, "command"), TEXT)).append(lit("  " + s(r, "source") + " ", FAINT));
                    if (s(r, "source").equals("project")) row.append(button("✖", cmd + "hooks rm " + s(r, "id"), false, DIM, L.t("Удалить")));
                    out.add(line(mode, row));
                }
                MutableComponent files = lit("  ", FAINT);
                for (JsonElement f : e.getAsJsonArray("files")) {
                    JsonObject o = f.getAsJsonObject();
                    files.append(button("✎ " + s(o, "source"), "/edit " + s(o, "path"), false, DIM, s(o, "path"))).append(sp());
                }
                out.add(line(mode, files));
            }
            case "hooks_log" -> {
                out.add(line(mode, lit("⚙ " + L.t("Журнал хуков"), GOLD).withStyle(st -> st.withBold(true))));
                JsonArray rows = e.getAsJsonArray("rows");
                if (rows.isEmpty()) out.add(line(mode, lit(L.t("  пока ничего не запускалось"), FAINT)));
                for (JsonElement item : rows) {
                    JsonObject r = item.getAsJsonObject();
                    boolean ok = s(r, "outcome").equals("success") || s(r, "exit").equals("0");
                    out.add(line(mode, lit(ok ? "  ✔ " : "  ✖ ", ok ? GREEN : RED).append(lit(s(r, "event") + " " + s(r, "name"), TEXT))
                        .append(lit("  " + firstLine(s(r, "output")), FAINT))));
                }
            }
            case "hook_failed" -> out.add(line(mode, lit("⚠ " + s(e, "text"), GOLD)
                .withStyle(st -> st.withHoverEvent(new HoverEvent.ShowText(lit(s(e, "output"), TEXT))))));
            case "skills" -> {
                out.add(line(mode, lit("✦ " + L.t("Скиллы"), GOLD).withStyle(st -> st.withBold(true))
                    .append(lit(L.t("   Claude сам берёт включённые, когда нужны"), FAINT))));
                JsonArray rows = e.getAsJsonArray("rows");
                if (rows.isEmpty()) out.add(line(mode, lit(L.t("  нет — положи папку со SKILL.md в ") + s(e, "project") + L.t(" или ") + s(e, "user"), FAINT)));
                for (JsonElement item : rows) {
                    JsonObject r = item.getAsJsonObject();
                    boolean on = b(r, "enabled");
                    out.add(line(mode, lit(on ? "  ● " : "  ○ ", on ? GREEN : FAINT).append(lit(s(r, "name"), on ? TEXT : DIM))
                        .append(lit("  " + (s(r, "source").equals("project") ? L.t("проект") : L.t("пользователь")) + "  ", FAINT))
                        .append(button(on ? L.t("выкл") : L.t("вкл"), cmd + "skills " + (on ? "off " : "on ") + s(r, "name"), false, on ? DIM : GREEN, null))
                        .append(sp()).append(button("SKILL.md", "/edit " + s(r, "path"), false, AQUA, null))));
                    if (!s(r, "description").isEmpty()) out.add(line(mode, lit("      " + s(r, "description"), FAINT)));
                }
            }
            case "memory" -> {
                boolean loaded = b(e, "loaded");
                out.add(line(mode, lit("☰ " + L.t("Память Claude"), GOLD).withStyle(st -> st.withBold(true))
                    .append(lit(loaded ? L.t("  Claude читает эти файлы  ") : L.t("  выключено — Claude их не видит  "), loaded ? GREEN : RED))
                    .append(button(loaded ? L.t("выключить") : L.t("включить"), cmd + "memory " + (loaded ? "off" : "on"), false, AQUA,
                        L.t("Вместе с CLAUDE.md грузятся правила и хуки из settings.json")))));
                for (JsonElement item : e.getAsJsonArray("files")) {
                    JsonObject f = item.getAsJsonObject();
                    String scope = s(f, "scope");
                    String label = scope.equals("project") ? L.t("проект") : scope.equals("local") ? L.t("локально (не в git)") : L.t("пользователь");
                    out.add(line(mode, lit("  " + label + "  ", TEXT)
                        .append(lit(b(f, "exists") ? n(f, "lines") + L.t(" строк  ") : L.t("нет файла  "), DIM))
                        .append(button("✎ " + (b(f, "exists") ? L.t("править") : L.t("создать")), cmd + "memory edit " + s(f, "path"), false, AQUA, s(f, "path")))));
                    for (JsonElement p : f.getAsJsonArray("preview")) out.add(line(mode, lit("      " + p.getAsString(), FAINT)));
                }
                out.add(line(mode, Component.literal("  ").append(button("# " + L.t("быстрая заметка"), cmd + "# ", true, GREEN,
                    L.t("Допишет строку в раздел ## Notes файла CLAUDE.md проекта")))));
            }
            case "rewind_list" -> {
                out.add(line(mode, lit("⟳ " + L.t("История запросов"), GOLD).withStyle(st -> st.withBold(true))
                    .append(lit(L.t("   ⟳ — откатить разговор и файлы к моменту перед запросом · «ветка» — новый чат после ответа"), FAINT))));
                for (JsonElement item : e.getAsJsonArray("rows")) {
                    JsonObject r = item.getAsJsonObject();
                    int number = n(r, "n");
                    out.add(line(mode, lit(String.format("  %2d ", number), FAINT)
                        .append(button("⟳", cmd + "rewind " + number, false, GOLD, L.t("Вернуться к моменту перед этим запросом")))
                        .append(sp()).append(button(L.t("ветка"), cmd + "fork " + number, false, AQUA, L.t("Новый чат: история до этого ответа включительно")))
                        .append(lit("  " + firstLine(s(r, "text")), TEXT))));
                }
            }
            case "search_results" -> {
                JsonArray rows = e.getAsJsonArray("rows");
                out.add(line(mode, lit("🔍 «" + s(e, "text") + "»", GOLD).withStyle(st -> st.withBold(true))
                    .append(lit("  " + rows.size() + L.t(" совпадений"), FAINT))));
                if (rows.isEmpty()) out.add(line(mode, lit(L.t("  ничего — попробуй другие слова"), DIM)));
                for (JsonElement item : rows) {
                    JsonObject r = item.getAsJsonObject();
                    String open = !s(r, "chat").isEmpty() ? cmd + "select " + s(r, "chat") : cmd + "open_session " + s(r, "session");
                    out.add(line(mode, Component.literal("  ").append(button(firstLine(s(r, "title")), open, false, AQUA,
                            s(r, "chat").isEmpty() ? L.t("Сессия без чата в игре (напр. с ПК) — откроется как новый чат") : L.t("Открыть чат")))
                        .append(lit(s(r, "role").equals("user") ? L.t("  ты") : "  Claude", FAINT))));
                    out.add(line(mode, lit("    " + s(r, "snippet"), DIM)));
                }
            }
            case "exported" -> out.add(line(mode, lit("✔ " + s(e, "text") + "  ", GREEN)
                .append(button(L.t("открыть"), cmd + "open " + s(e, "path"), false, AQUA, s(e, "path"))).append(sp())
                .append(button("✎", "/edit " + s(e, "path"), false, AQUA, L.t("Во встроенном редакторе")))
                .append(lit("  " + n(e, "count") + L.t(" записей"), FAINT))));
            case "forked" -> out.add(line(mode, lit("✦ " + s(e, "text"), AQUA).append(sp())
                .append(button(L.t("исходный чат"), cmd + "select " + s(e, "source"), false, DIM, null))));
            case "rewound" -> {
                out.add(line(mode, lit("⟳ " + s(e, "text"), GOLD).withStyle(st -> st.withBold(true))));
                for (JsonElement p : e.getAsJsonArray("restored")) out.add(line(mode, lit(L.t("  восстановлен ") + p.getAsString(), GREEN)));
                for (JsonElement p : e.getAsJsonArray("removed")) out.add(line(mode, lit(L.t("  удалён (создан Claude) ") + p.getAsString(), DIM)));
                if (!s(e, "prompt").isEmpty())
                    out.add(line(mode, Component.literal("  ").append(button("✎ " + L.t("изменить и отправить заново"),
                        cmd + firstLine(s(e, "prompt")), true, AQUA, s(e, "prompt")))));
            }
            case "budget_hit" -> {
                out.add(line(mode, lit("⏸ " + s(e, "text"), GOLD).withStyle(st -> st.withBold(true))));
                out.add(line(mode, Component.literal("  ")
                    .append(button("▶ " + L.t("продолжить"), cmd + "budget resume", false, GREEN,
                        L.t("Один запрос пройдёт сверх лимита")))
                    .append(sp()).append(button("+ " + L.t("лимит"), cmd + "budget " + s(e, "scope") + " ", true, AQUA,
                        L.t("Новый лимит: $1, 500k, 2M или off")))
                    .append(sp()).append(button(L.t("бюджет"), cmd + "budget", false, AQUA, null))
                    .append(lit(n(e, "queued") > 0 ? L.t("  в очереди: ") + n(e, "queued") : "", FAINT))));
            }
            case "budget" -> {
                out.add(line(mode, lit("$ " + L.t("Бюджет"), GOLD).withStyle(st -> st.withBold(true))
                    .append(lit(b(e, "paused") ? L.t("  на паузе") : "", RED))));
                JsonObject limits = e.getAsJsonObject("limits"), spent = e.getAsJsonObject("spent");
                for (String scope : new String[]{"task", "chat", "day"}) {
                    String label = scope.equals("task") ? L.t("запрос") : scope.equals("chat") ? L.t("чат") : L.t("день");
                    JsonObject lim = limits.has(scope) ? limits.getAsJsonObject(scope) : new JsonObject();
                    JsonObject sp = spent.getAsJsonObject(scope);
                    String limitText = lim.isEmpty() ? L.t("без лимита")
                        : (lim.has("usd") ? String.format(Locale.ROOT, "$%.2f", lim.get("usd").getAsDouble()) : "")
                        + (lim.has("usd") && lim.has("tokens") ? " · " : "")
                        + (lim.has("tokens") ? human(lim.get("tokens").getAsLong()) + L.t(" ток.") : "");
                    out.add(line(mode, lit(String.format("  %-8s", label), TEXT)
                        .append(lit(String.format(Locale.ROOT, "$%.3f · %s", sp.get("usd").getAsDouble(), human(sp.get("tokens").getAsLong())), DIM))
                        .append(lit("  / " + limitText + "  ", lim.isEmpty() ? FAINT : GOLD))
                        .append(button(L.t("изменить"), cmd + "budget " + scope + " ", true, AQUA, "$0.50 · 200k · 2M · off"))));
                }
                out.add(line(mode, lit(L.t("  при достижении лимита запрос прерывается, очередь ждёт /cc budget resume"), FAINT)));
            }
            case "issue_list" -> {
                JsonArray rows = e.getAsJsonArray("rows");
                out.add(line(mode, lit("☰ Issues", GOLD).withStyle(st -> st.withBold(true)).append(lit("  " + rows.size() + L.t(" открыто"), FAINT))));
                if (rows.isEmpty()) out.add(line(mode, lit(L.t("  открытых issues нет"), FAINT)));
                for (JsonElement item : rows) {
                    JsonObject r = item.getAsJsonObject();
                    MutableComponent row = Component.literal("  ").append(button("#" + n(r, "number"), cmd + "issue " + n(r, "number"), false, AQUA, null))
                        .append(lit(" " + s(r, "title"), TEXT));
                    for (JsonElement l : r.getAsJsonArray("labels")) row.append(lit(" [" + l.getAsString() + "]", GOLD));
                    row.append(sp()).append(button("✦ " + L.t("сделай"), cmd + "issue do " + n(r, "number"), false, CODE_MODE,
                        L.t("Claude: ветка issue-N, реализация, тесты, черновик PR с «Closes #N»")));
                    out.add(line(mode, row));
                }
            }
            case "issue_view" -> {
                out.add(line(mode, lit("☰ #" + n(e, "number") + " " + s(e, "title"), GOLD).withStyle(st -> st.withBold(true)).append(sp())
                    .append(button("✦ " + L.t("сделай"), cmd + "issue do " + n(e, "number"), false, CODE_MODE, null))));
                markdown(mode, s(e, "body"), out);
                for (JsonElement c : e.getAsJsonArray("comments")) out.add(line(mode, lit("  " + firstLine(c.getAsString()), DIM)));
            }
            case "pr_list" -> {
                JsonArray rows = e.getAsJsonArray("rows");
                out.add(line(mode, lit("⇄ Pull requests", GOLD).withStyle(st -> st.withBold(true))
                    .append(lit("  " + rows.size() + L.t(" открыто  "), FAINT))
                    .append(button("+ " + L.t("новый PR"), cmd + "pr create", false, GREEN, L.t("Claude напишет описание и откроет черновик PR")))));
                if (rows.isEmpty()) out.add(line(mode, lit(L.t("  открытых PR нет"), FAINT)));
                for (JsonElement item : rows) {
                    JsonObject r = item.getAsJsonObject();
                    out.add(line(mode, Component.literal("  ").append(button("#" + n(r, "number"), cmd + "pr " + n(r, "number"), false, AQUA, L.t("Подробности, CI и комментарии")))
                        .append(lit(" " + s(r, "title"), TEXT)).append(lit(b(r, "draft") ? L.t("  черновик") : "", FAINT))
                        .append(checksBadge(r.getAsJsonObject("checks")))
                        .append(lit("  " + s(r, "branch") + " → " + s(r, "base"), FAINT))));
                }
            }
            case "pr_view" -> {
                out.add(line(mode, lit("⇄ #" + n(e, "number") + " " + s(e, "title"), GOLD).withStyle(st -> st.withBold(true))
                    .append(checksBadge(e.getAsJsonObject("checks")))
                    .append(lit(s(e, "review").isEmpty() ? "" : "  " + s(e, "review").toLowerCase(Locale.ROOT).replace('_', ' '), DIM))));
                out.add(line(mode, Component.literal("  ").append(button(L.t("открыть на GitHub"), s(e, "url"), false, AQUA, s(e, "url"))
                        .withStyle(st -> st.withClickEvent(new ClickEvent.OpenUrl(URI.create(s(e, "url").isEmpty() ? "https://github.com" : s(e, "url"))))))
                    .append(sp()).append(button("✦ " + L.t("ответить на ревью"), cmd + "pr reply " + n(e, "number"), false, CODE_MODE,
                        L.t("Claude исправит по комментариям, запушит и ответит в PR (каждый шаг с подтверждением)")))));
                JsonArray failed = e.getAsJsonObject("checks").getAsJsonArray("failed");
                for (JsonElement f : failed) out.add(line(mode, lit("  ✖ CI: " + f.getAsString(), RED)));
                for (JsonElement ce : e.getAsJsonArray("comments")) {
                    JsonObject c = ce.getAsJsonObject();
                    out.add(line(mode, lit("  " + s(c, "author"), AQUA).append(lit(s(c, "where").isEmpty() ? "" : " " + s(c, "where"), FAINT))
                        .append(lit(": " + firstLine(s(c, "body")), TEXT))));
                }
            }
            case "git_conflicts" -> {
                JsonArray files = e.getAsJsonArray("files");
                out.add(line(mode, lit("⚠ " + L.t("Конфликты слияния"), files.isEmpty() ? GREEN : RED).withStyle(st -> st.withBold(true))
                    .append(lit(files.isEmpty() ? L.t("  нет") : "  " + files.size() + L.t(" файл(ов)  "), FAINT))
                    .append(files.isEmpty() ? Component.literal("") : button("✦ " + L.t("Claude, разреши"), "/cc git resolve", false, CODE_MODE,
                        L.t("Claude поймёт обе стороны, уберёт маркеры, проверит сборку и сделает git add")))));
                for (JsonElement fe : files) {
                    JsonObject f = fe.getAsJsonObject();
                    String file = s(f, "path");
                    out.add(line(mode, Component.literal("  ").append(button(file, "/edit " + file, false, GOLD, L.t("Открыть в редакторе")))));
                    for (JsonElement be : f.getAsJsonArray("blocks")) {
                        JsonObject b = be.getAsJsonObject();
                        int idx = n(b, "index");
                        String base = "/cc git pick " + file + " " + idx + " ";
                        out.add(line(mode, lit(String.format(L.t("    #%d стр. %d  "), idx + 1, n(b, "start")), DIM)
                            .append(button("◀ ours", base + "ours", false, GREEN, s(b, "ours_label"))).append(sp())
                            .append(button("theirs ▶", base + "theirs", false, AQUA, s(b, "theirs_label"))).append(sp())
                            .append(button(L.t("оба"), base + "both", false, GOLD, null))));
                        for (JsonElement l : b.getAsJsonArray("ours")) out.add(line(mode, lit("      < " + clean(l.getAsString()), GREEN)));
                        for (JsonElement l : b.getAsJsonArray("theirs")) out.add(line(mode, lit("      > " + clean(l.getAsString()), AQUA)));
                    }
                }
            }
            case "git_graph" -> {
                out.add(line(mode, lit("⇄ " + L.t("Граф коммитов"), GOLD).withStyle(st -> st.withBold(true))
                    .append(lit(L.t("   все ветки · клик по хэшу — show"), FAINT))));
                int[] laneColors = {GREEN, AQUA, GOLD, CODE_MODE, RED};
                for (JsonElement item : e.getAsJsonArray("rows")) {
                    JsonObject r = item.getAsJsonObject();
                    MutableComponent row = Component.literal("  ");
                    String graph = s(r, "graph");
                    for (int i = 0; i < graph.length(); i++) {
                        char ch = graph.charAt(i);
                        row.append(lit(String.valueOf(ch), ch == '*' ? TEXT : laneColors[(i / 2) % laneColors.length]));
                    }
                    if (!s(r, "hash").isEmpty()) {
                        row.append(sp()).append(button(s(r, "hash"), "/cc sh git show --stat " + s(r, "hash"), false, GOLD, s(r, "author") + " · " + s(r, "when")));
                        if (!s(r, "refs").isEmpty()) row.append(lit(" (" + s(r, "refs") + ")", GREEN));
                        row.append(lit(" " + s(r, "subject"), TEXT)).append(lit("  " + s(r, "when"), FAINT));
                    }
                    out.add(line(mode, row));
                }
            }
            case "git_hunks" -> {
                out.add(line(mode, lit("± " + L.t("Куски изменений"), GOLD).withStyle(st -> st.withBold(true))
                    .append(lit("  " + s(e, "text") + "  ", DIM)).append(button("⟳", "/cc git hunks " + s(e, "path"), false, AQUA, null))
                    .append(sp()).append(button("✔ commit", "/cc git commit ", true, GREEN, L.t("Закоммитить то, что в коммите (staged)")))));
                for (String group : new String[]{"unstaged", "staged"}) {
                    JsonArray list = e.getAsJsonArray(group);
                    boolean isStaged = group.equals("staged");
                    out.add(line(mode, lit(isStaged ? L.t("  В коммите (staged)") : L.t("  Не в коммите"), isStaged ? GREEN : GOLD)
                        .withStyle(st -> st.withUnderlined(true))));
                    if (list.isEmpty()) out.add(line(mode, lit(L.t("    — нет"), FAINT)));
                    for (JsonElement item : list) {
                        JsonObject h = item.getAsJsonObject();
                        out.add(line(mode, Component.literal("  ")
                            .append(button(isStaged ? "− " + L.t("убрать") : "+ " + L.t("в коммит"),
                                "/cc git " + (isStaged ? "unstage " : "stage ") + s(h, "id"), false, isStaged ? RED : GREEN, null))
                            .append(lit("  " + s(h, "path"), TEXT)).append(lit("  " + s(h, "header").replaceAll("@@ (.*?) @@.*", "$1"), AQUA))
                            .append(lit("  +" + n(h, "added"), GREEN)).append(lit(" −" + n(h, "removed"), RED))));
                        int shown = 0;
                        for (JsonElement l : h.getAsJsonArray("lines")) {
                            String raw = l.getAsString();
                            if (!raw.startsWith("+") && !raw.startsWith("-")) continue;  // context lines only in the window K
                            if (shown++ >= 6) { out.add(line(mode, lit("      …", FAINT))); break; }
                            out.add(line(mode, lit("      " + clean(raw), raw.startsWith("+") ? GREEN : RED)));
                        }
                    }
                }
            }
            case "replace_preview" -> {
                out.add(line(mode, lit("⇄ " + L.t("Замена"), GOLD).withStyle(st -> st.withBold(true))
                    .append(lit("  «" + s(e, "query") + "» → «" + s(e, "replacement") + "»", TEXT))
                    .append(lit("  " + n(e, "count") + L.t(" совп. в ") + n(e, "files") + L.t(" файл(ах)")
                        + (b(e, "regex") ? " · regex" : "") + (s(e, "glob").isEmpty() ? "" : " · " + s(e, "glob")), DIM))));
                JsonArray changes = e.getAsJsonArray("changes");
                String file = "";
                int shown = 0;
                for (JsonElement item : changes) {
                    if (shown++ >= 30) break;
                    JsonObject c = item.getAsJsonObject();
                    if (!s(c, "path").equals(file)) {
                        file = s(c, "path");
                        out.add(line(mode, Component.literal("  ").append(button(file, "/edit " + s(e, "root") + "/" + file
                            + ":" + n(c, "line"), false, GOLD, null))));
                    }
                    out.add(line(mode, lit(String.format("   %4d ", n(c, "line")), FAINT).append(lit("- " + s(c, "before"), RED))));
                    out.add(line(mode, lit("        ", FAINT).append(lit("+ " + s(c, "after"), GREEN))));
                }
                if (changes.size() > 30 || b(e, "truncated")) out.add(line(mode, lit(L.t("  … показаны первые изменения"), FAINT)));
                if (n(e, "count") > 0)
                    out.add(line(mode, Component.literal("  ").append(button("✔ " + L.t("Заменить всё"), cmd + "replace apply " + s(e, "token"),
                        false, GREEN, L.t("Можно откатить: /cc undo"))).append(sp())
                        .append(button(L.t("изменить"), cmd + "replace " + (b(e, "regex") ? "-r " : "") + s(e, "query") + " -> ", true, AQUA, null))));
                else out.add(line(mode, lit(L.t("  совпадений нет"), DIM)));
            }
            case "replace_done" -> out.add(line(mode, lit("✔ " + s(e, "text") + "  ", GREEN)
                .append(button("⟳ " + L.t("откатить"), cmd + "undo", false, GOLD, null))));
            case "perms" -> {
                out.add(line(mode, lit("⚙ " + L.t("Правила разрешений"), GOLD).withStyle(st -> st.withBold(true))
                    .append(lit("  " + s(e, "path"), FAINT))));
                JsonObject rules = e.getAsJsonObject("rules");
                for (String kind : new String[]{"deny", "ask", "allow"}) {
                    int color = kind.equals("deny") ? RED : kind.equals("ask") ? GOLD : GREEN;
                    MutableComponent head = lit("  " + kind + "  ", color).withStyle(st -> st.withBold(true))
                        .append(button("+", cmd + "perms " + kind + " ", true, color,
                            L.t("Добавить правило, напр. Bash(git *), Edit(src/**), WebFetch(domain:github.com)")));
                    out.add(line(mode, head));
                    JsonArray list = rules.has(kind) ? rules.getAsJsonArray(kind) : new JsonArray();
                    if (list.isEmpty()) out.add(line(mode, lit(L.t("    — нет"), FAINT)));
                    for (JsonElement item : list) {
                        JsonObject r = item.getAsJsonObject();
                        MutableComponent row = lit("    " + s(r, "rule"), TEXT);
                        if (s(r, "source").equals("user")) row.append(lit(L.t("  (~/.claude, только чтение)"), FAINT));
                        else row.append(sp()).append(button("✖", cmd + "perms rm " + s(r, "rule"), false, DIM, L.t("Удалить правило")));
                        out.add(line(mode, row));
                    }
                }
                out.add(line(mode, lit(L.t("  deny важнее ask, ask важнее allow; без совпадения — режим /cc auto"), FAINT)));
            }
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
            .append(button(L.t("куски"), "/cc git hunks", false, AQUA, L.t("Добавлять в коммит по кускам (stage по ханкам)"))).append(sp())
            .append(button(L.t("граф"), "/cc git graph", false, AQUA, L.t("Граф коммитов всех веток"))).append(sp())
            .append(button(L.t("конфликты"), "/cc git conflicts", false, AQUA, L.t("Разрешить конфликты слияния"))).append(sp())
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

    /** CI state of a PR: ✔ passing, ✖ failing, … pending. */
    static MutableComponent checksBadge(JsonObject checks) {
        String state = checks == null ? "none" : s(checks, "state");
        return switch (state) {
            case "passing" -> lit("  ✔ CI", GREEN);
            case "failing" -> lit("  ✖ CI", RED);
            case "pending" -> lit("  … CI", GOLD);
            default -> Component.literal("");
        };
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
        JsonArray background = e.has("background") ? e.getAsJsonArray("background") : new JsonArray();
        if (!background.isEmpty()) out.add(line("cc", lit(L.t("  Фоновые процессы Claude"), DIM).withStyle(st -> st.withUnderlined(true))));
        for (JsonElement item : background) {
            JsonObject t = item.getAsJsonObject();
            boolean running = s(t, "status").equals("running");
            MutableComponent row = lit(running ? "  ▶ " : "  ■ ", running ? GREEN : FAINT).append(lit(s(t, "description"), TEXT))
                .append(lit("  " + s(t, "status") + "  ", running ? GREEN : FAINT))
                .append(button(L.t("вывод"), "/cc bg out " + s(t, "id"), false, AQUA, null));
            if (running && b(t, "live")) row.append(sp()).append(button(L.t("⏹ стоп"), "/cc bg stop " + s(t, "id"), false, RED, null));
            out.add(line("cc", row));
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
