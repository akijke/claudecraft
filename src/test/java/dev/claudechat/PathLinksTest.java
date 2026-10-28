package dev.claudechat;

import org.junit.jupiter.api.Test;
import java.util.List;
import static org.junit.jupiter.api.Assertions.*;

class PathLinksTest {
    private static List<String> found(String text) {
        return PathLinks.find(text).stream().map(l -> l.path() + "@" + l.line()).toList();
    }

    @Test void compilerErrorsTracebacksAndStackTraces() {
        assertEquals(List.of("src/main/java/Foo.java@42"), found("src/main/java/Foo.java:42: error: cannot find symbol"));
        assertEquals(List.of("C:\\dev\\mod\\src\\Main.java@12"), found("C:\\dev\\mod\\src\\Main.java:12:5 warning"));
        assertEquals(List.of("bridge/bridge.py@310"), found("  File \"bridge/bridge.py\", line 310, in pump"));
        assertEquals(List.of("ChatStyle.java@88"), found("at dev.claudechat.ChatStyle.render(ChatStyle.java:88)"));
        assertEquals(List.of("C:/x/Build.kt@7"), found("e: file:///C:/x/Build.kt:7:3 Unresolved"));
        assertEquals(List.of("bridge/backends/files.py@114", "./README.md@3"), found("bridge/backends/files.py:114 and ./README.md:3"));
    }

    @Test void urlsAndTimesAreNotLinks() {
        assertTrue(found("see https://example.com:443/path and http://localhost:8080").isEmpty());
        assertTrue(found("time 12:30:45 is not a path, nor v1.2:3").isEmpty());
    }

    @Test void commandAndSplit() {
        assertEquals("/edit a/b.py:3", PathLinks.find("a/b.py:3").get(0).command());
        assertArrayEquals(new String[]{"C:\\a\\b.java", "12"}, PathLinks.splitLine("C:\\a\\b.java:12"));
        assertArrayEquals(new String[]{"C:\\a\\b.java", null}, PathLinks.splitLine("C:\\a\\b.java"));
        assertArrayEquals(new String[]{"x.py", "3"}, PathLinks.splitLine("x.py:3:4"));
        assertArrayEquals(new String[]{"C:", null}, PathLinks.splitLine("C:"));
    }

    @Test void linkifyKeepsTheText() {
        assertEquals("see src/A.java:5 now", PathLinks.linkify("see src/A.java:5 now", 0xFFFFFF, 0x00FFFF).getString());
    }
}
