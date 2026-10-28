package dev.claudechat;

import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.MutableComponent;
import org.junit.jupiter.api.Test;
import java.util.ArrayList;
import java.util.List;
import static org.junit.jupiter.api.Assertions.*;

class HighlighterTest {
    /** text → colour of each top-level part. */
    private static List<String> parts(MutableComponent c) {
        List<String> out = new ArrayList<>();
        for (Component part : c.getSiblings()) {
            var color = part.getStyle().getColor();
            out.add(part.getString() + "=" + (color == null ? "none" : String.format("%06X", color.getValue())));
        }
        return out;
    }

    private static int colorOf(MutableComponent c, String text) {
        for (Component part : c.getSiblings())
            if (part.getString().equals(text)) return part.getStyle().getColor().getValue();
        throw new AssertionError(text + " not in " + parts(c));
    }

    @Test void javaKeywordsStringsCommentsAnnotations() {
        boolean[] block = {false};
        MutableComponent line = Highlighter.line("@Override public String s = \"hi\"; // note", "java", block);
        assertEquals("@Override public String s = \"hi\"; // note", line.getString());
        assertEquals(Highlighter.ANNOTATION, colorOf(line, "@Override"));
        assertEquals(Highlighter.KEYWORD, colorOf(line, "public"));
        assertEquals(Highlighter.STRING, colorOf(line, "\"hi\""));
        assertEquals(Highlighter.COMMENT, colorOf(line, "// note"));
    }

    @Test void blockCommentSpansLines() {
        boolean[] block = {false};
        Highlighter.line("int a; /* start", "java", block);
        assertTrue(block[0]);
        MutableComponent middle = Highlighter.line("still comment", "java", block);
        assertEquals(Highlighter.COMMENT, colorOf(middle, "still comment"));
        Highlighter.line("end */ int b;", "java", block);
        assertFalse(block[0]);
    }

    @Test void hashCommentsJsonKeysPlainText() {
        boolean[] block = {false};
        assertEquals(Highlighter.COMMENT, colorOf(Highlighter.line("x = 1  # set x", "py", block), "# set x"));
        assertEquals(Highlighter.KEY, colorOf(Highlighter.line("\"name\": \"value\"", "json", block), "\"name\""));
        MutableComponent txt = Highlighter.line("for in class 42", "txt", block);
        assertEquals(1, txt.getSiblings().size());
        assertEquals(Highlighter.PLAIN, colorOf(txt, "for in class 42"));
    }

    @Test void fenceNamesAndFileNames() {
        boolean[] block = {false};
        assertEquals(Highlighter.KEYWORD, colorOf(Highlighter.line("def f(): pass", "python", block), "def"));
        assertEquals("java", Highlighter.language("Main.JAVA"));
        assertEquals("dockerfile", Highlighter.language("Dockerfile"));
        assertEquals("", Highlighter.language("LICENSE"));
    }
}
