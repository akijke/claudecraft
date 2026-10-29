package dev.claudechat;

import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;
import java.util.List;
import static org.junit.jupiter.api.Assertions.*;

class AchievementsTest {
    private static JsonObject event(String kind, String text) {
        JsonObject e = new JsonObject();
        e.addProperty("kind", kind);
        e.addProperty("text", text);
        return e;
    }

    @Test void earnedOnce() {
        JsonObject state = new JsonObject();
        assertEquals(List.of("first_task"), Achievements.check(state, event("done", "Готово"), 14));
        assertEquals(List.of(), Achievements.check(state, event("done", "Готово"), 14));
        assertEquals(List.of("night_shift"), Achievements.check(state, event("done", "Готово"), 3));
        JsonObject long_ = event("done", "");
        long_.addProperty("seconds", 900.0);
        assertEquals(List.of("marathon"), Achievements.check(state, long_, 12));
        assertEquals(4, state.get("count_done").getAsInt());
    }

    @Test void toolsAndCounters() {
        JsonObject state = new JsonObject();
        JsonObject commit = event("tool", "git commit -m \"fix\"");
        commit.addProperty("tool", "Bash");
        assertEquals(List.of("first_commit"), Achievements.check(state, commit, 12));
        JsonObject read = event("tool", "Read git commit notes.md");
        read.addProperty("tool", "Read");
        assertEquals(List.of(), Achievements.check(state, read, 12));
        JsonObject tests = event("tool", "python -m unittest discover");
        tests.addProperty("tool", "Bash");
        for (int i = 0; i < 99; i++) assertEquals(List.of(), Achievements.check(state, tests, 12));
        assertEquals(List.of("tests_100"), Achievements.check(state, tests, 12));
        assertEquals(List.of("guest"), Achievements.check(state, event("guest_token", ""), 12));
        for (String[] a : Achievements.ALL) assertNotNull(Achievements.find(a[0]));
    }
}
