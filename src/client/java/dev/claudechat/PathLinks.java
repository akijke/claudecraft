package dev.claudechat;

import net.minecraft.network.chat.ClickEvent;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.HoverEvent;
import net.minecraft.network.chat.MutableComponent;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * `path:line[:col]` and Python's `File "x.py", line 12` in answers, tool output and the
 * terminal become links that open the in-game editor on that line (/edit path:line).
 */
final class PathLinks {
    // A file name with an extension, optional folders/drive before it, then :line[:col].
    // Not after a word char, '/', '\' or '.', so URLs like https://host.com:443 are skipped.
    static final Pattern PATH_LINE = Pattern.compile(
        "(?<![\\w/\\\\.:])((?:[A-Za-z]:[\\\\/]|\\.{1,2}[\\\\/]|[\\\\/])?(?:[\\w.\\-]+[\\\\/])*[\\w\\-]+(?:\\.[\\w\\-]+)*\\.[A-Za-z][A-Za-z0-9]{0,7})"
            + ":(\\d{1,6})(?::(\\d{1,4}))?(?![\\w])");
    static final Pattern PYTHON = Pattern.compile("File \"([^\"]+)\", line (\\d{1,6})");
    static final Pattern FILE_URL = Pattern.compile("file:///((?:[A-Za-z]:)?[^\\s:]+):(\\d{1,6})(?::\\d{1,4})?");

    record Link(int start, int end, String path, int line) {
        String command() { return "/edit " + path + ":" + line; }
    }

    private PathLinks() { }

    static List<Link> find(String text) {
        List<Link> out = new ArrayList<>();
        Matcher m = PYTHON.matcher(text);
        while (m.find()) out.add(new Link(m.start(), m.end(), m.group(1), Integer.parseInt(m.group(2))));
        m = FILE_URL.matcher(text);
        while (m.find()) out.add(new Link(m.start(), m.end(), m.group(1), Integer.parseInt(m.group(2))));
        m = PATH_LINE.matcher(text);
        while (m.find()) {
            int s = m.start(), e = m.end();
            if (out.stream().anyMatch(l -> s < l.end() && e > l.start())) continue;
            out.add(new Link(s, e, m.group(1), Integer.parseInt(m.group(2))));
        }
        out.sort((a, b) -> Integer.compare(a.start(), b.start()));
        return out;
    }

    /** Text with links in `linkColor`; plain parts in `color`. */
    static MutableComponent linkify(String text, int color, int linkColor) {
        MutableComponent result = Component.literal("");
        int last = 0;
        for (Link l : find(text)) {
            if (l.start() > last) result.append(Component.literal(text.substring(last, l.start())).withColor(color));
            String shown = text.substring(l.start(), l.end());
            result.append(Component.literal(shown).withStyle(s -> s.withColor(linkColor).withUnderlined(true)
                .withClickEvent(new ClickEvent.RunCommand(l.command()))
                .withHoverEvent(new HoverEvent.ShowText(Component.literal(L.t("Открыть в редакторе: строка ") + l.line())))));
            last = l.end();
        }
        if (last < text.length()) result.append(Component.literal(text.substring(last)).withColor(color));
        return result;
    }

    /** "C:\a\b.java:12" → {"C:\a\b.java", "12"}; no line → {text, null}. Handles drive letters. */
    static String[] splitLine(String arg) {
        Matcher m = Pattern.compile("^(.*?):(\\d{1,6})(?::\\d{1,4})?$").matcher(arg.strip());
        if (m.matches() && !m.group(1).isEmpty() && !m.group(1).matches("[A-Za-z]")) return new String[]{m.group(1), m.group(2)};
        return new String[]{arg.strip(), null};
    }
}
