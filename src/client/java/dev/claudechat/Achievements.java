package dev.claudechat;

import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.components.toasts.SystemToast;
import net.minecraft.network.chat.Component;
import java.time.LocalTime;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Pattern;

/** Small achievements for programming work (#85): a toast + sound once, kept in client.json → "achievements". */
final class Achievements {
    private static final SystemToast.SystemToastId TOAST = new SystemToast.SystemToastId(8000L);
    private static final Pattern TESTS = Pattern.compile("(unittest|pytest|gradlew\\S* (test|build|check)|npm (run )?test|cargo test|go test|mvn test)");

    /** id, title, how to get it. */
    static final String[][] ALL = {
        {"first_task", "Первая задача", "Claude закончил первую задачу"},
        {"tasks_100", "Сотня", "100 завершённых задач"},
        {"night_shift", "Ночная смена", "задача завершилась между полуночью и 5 утра"},
        {"marathon", "Марафон", "одна задача шла больше 10 минут"},
        {"first_commit", "Первый коммит", "Claude сделал git commit"},
        {"tests_100", "Зелёная полоса", "Claude 100 раз запускал тесты или сборку"},
        {"undo", "Машина времени", "откат правок Claude (/cc undo)"},
        {"guest", "Вдвоём веселее", "первый гость в парном программировании"},
    };

    private Achievements() {}

    /** Pure logic (tested without a game): updates counters in `state`, returns newly earned ids. */
    static List<String> check(JsonObject state, JsonObject e, int hour) {
        List<String> earned = new ArrayList<>();
        String kind = e.has("kind") ? e.get("kind").getAsString() : "";
        String text = e.has("text") && !e.get("text").isJsonNull() ? e.get("text").getAsString() : "";
        switch (kind) {
            case "done" -> {
                int done = bump(state, "count_done");
                earn(state, "first_task", earned);
                if (done >= 100) earn(state, "tasks_100", earned);
                if (hour < 5) earn(state, "night_shift", earned);
                if (e.has("seconds") && e.get("seconds").getAsDouble() >= 600) earn(state, "marathon", earned);
            }
            case "tool" -> {
                String tool = e.has("tool") ? e.get("tool").getAsString() : "";
                if (!tool.equals("Bash") && !tool.isEmpty()) break;
                if (text.contains("git commit")) earn(state, "first_commit", earned);
                if (TESTS.matcher(text).find() && bump(state, "count_tests") >= 100) earn(state, "tests_100", earned);
            }
            case "undo" -> earn(state, "undo", earned);
            case "guest_token" -> earn(state, "guest", earned);
            default -> { }
        }
        return earned;
    }

    private static int bump(JsonObject state, String key) {
        int n = (state.has(key) ? state.get(key).getAsInt() : 0) + 1;
        state.addProperty(key, n);
        return n;
    }

    private static void earn(JsonObject state, String id, List<String> earned) {
        JsonObject got = state.has("got") ? state.getAsJsonObject("got") : new JsonObject();
        state.add("got", got);
        if (got.has(id)) return;
        got.addProperty(id, System.currentTimeMillis() / 1000);
        earned.add(id);
    }

    static JsonObject state(BridgeLauncher launcher) {
        if (!launcher.settings.has("achievements") || !launcher.settings.get("achievements").isJsonObject())
            launcher.settings.add("achievements", new JsonObject());
        return launcher.settings.getAsJsonObject("achievements");
    }

    /** Main thread, for every bridge event. History replays are not passed here. */
    static void onEvent(BridgeLauncher launcher, JsonObject e) {
        JsonObject state = state(launcher);
        int before = state.toString().hashCode();
        List<String> earned = check(state, e, LocalTime.now().getHour());
        if (state.toString().hashCode() != before) launcher.saveAll();
        for (String id : earned) {
            String[] a = find(id);
            if (!launcher.flag("achievements", true) || a == null) continue;
            Minecraft client = Minecraft.getInstance();
            SystemToast.add(client.gui.toastManager(), TOAST, Component.literal("✦ " + L.t("Достижение: ") + L.t(a[1])),
                Component.literal(L.t(a[2])));
            if (launcher.flag("sound", true)) Sounds.play("done");
        }
    }

    static String[] find(String id) {
        for (String[] a : ALL) if (a[0].equals(id)) return a;
        return null;
    }

    static boolean has(BridgeLauncher launcher, String id) {
        JsonObject s = state(launcher);
        return s.has("got") && s.getAsJsonObject("got").has(id);
    }
}
