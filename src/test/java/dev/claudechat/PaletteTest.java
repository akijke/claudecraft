package dev.claudechat;

import org.junit.jupiter.api.Test;
import java.util.Comparator;
import java.util.List;
import static org.junit.jupiter.api.Assertions.*;

class PaletteTest {
    private static String best(String query, String... items) {
        return List.of(items).stream().filter(i -> PaletteScreen.score(i, query) > 0)
            .max(Comparator.comparingInt(i -> PaletteScreen.score(i, query))).orElse("");
    }

    @Test void fuzzyOrdering() {
        assertEquals(0, PaletteScreen.score("Main.java", "xyz"));
        assertTrue(PaletteScreen.score("src/Main.java", "main") > 0);
        assertEquals("src/client/ChatStyle.java", best("chst", "src/client/ChatStyle.java", "src/client/ClaudeChat.java", "docs/notes.md"));
        assertEquals("build.gradle", best("bg", "build.gradle", "src/big/thing/Bag.java"));
        assertEquals("Diagnostics", best("diag", "Diagnostics", "Git: diff", "Agents"));
        assertEquals(1, PaletteScreen.score("anything", ""));
    }
}
