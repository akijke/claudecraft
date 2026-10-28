package dev.claudechat;

import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.MutableComponent;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Small line-by-line syntax highlighter for the editor, file preview and code blocks.
 * Language comes from the file extension (or the ``` fence); unknown languages get a
 * generic C-like scheme. Block comments are tracked across lines with a one-element state.
 */
final class Highlighter {
    static final int PLAIN = 0xD4D4D4, KEYWORD = 0xC586C0, STRING = 0xCE9178, NUMBER = 0xB5CEA8,
        COMMENT = 0x6A9955, TYPE = 0x4EC9B0, ANNOTATION = 0xDCDCAA, PUNCT = 0xA0A0A0, KEY = 0x9CDCFE, HEADING = 0xF2C14E;

    private static final Set<String> C_LIKE = Set.of("java", "kt", "kts", "js", "jsx", "ts", "tsx", "c", "h", "cpp",
        "hpp", "cs", "go", "rs", "swift", "gradle", "groovy", "scala", "dart", "php", "css", "scss", "json", "jsonc");
    private static final Set<String> HASH = Set.of("py", "sh", "bash", "zsh", "rb", "yml", "yaml", "toml", "ps1",
        "properties", "cfg", "ini", "conf", "dockerfile", "makefile", "r", "pl", "nim", "mcfunction");
    private static final String KEYWORDS = "abstract|and|as|assert|async|await|break|case|catch|class|const|continue|def|"
        + "default|defer|del|do|elif|else|enum|except|export|extends|false|final|finally|fn|for|from|func|function|"
        + "global|go|if|impl|implements|import|in|instanceof|interface|is|lambda|let|loop|match|mod|module|mut|"
        + "namespace|new|nil|none|None|not|null|or|override|package|pass|private|protected|pub|public|raise|record|"
        + "return|sealed|self|static|struct|super|switch|synchronized|this|throw|throws|trait|true|True|False|try|"
        + "type|typeof|use|val|var|void|volatile|when|where|while|with|yield|int|long|float|double|boolean|bool|"
        + "char|byte|short|string|String|echo|fi|then|esac|done|local|readonly|function";
    private static final Pattern TOKENS = Pattern.compile(
        "(?<str>\"(?:\\\\.|[^\"\\\\])*\"?|'(?:\\\\.|[^'\\\\])*'?|`(?:\\\\.|[^`\\\\])*`?)"
        + "|(?<num>\\b(?:0[xX][0-9a-fA-F_]+|\\d[\\d_]*(?:\\.\\d+)?(?:[eE][+-]?\\d+)?)[LlFfDd]?\\b)"
        + "|(?<ann>@[A-Za-z_][\\w.]*)"
        + "|(?<kw>\\b(?:" + KEYWORDS + ")\\b)"
        + "|(?<type>\\b[A-Z][A-Za-z0-9_]*\\b)"
        + "|(?<punct>[{}()\\[\\];,.:<>=+\\-*/%!&|^~?]+)");

    private static final Set<String> PLAIN_LANGS = Set.of("txt", "text", "plain", "plaintext", "log", "csv", "tsv",
        "output", "out", "lock", "license");

    private Highlighter() { }

    static String language(String fileName) {
        String name = fileName == null ? "" : fileName.toLowerCase(Locale.ROOT);
        if (name.endsWith("dockerfile")) return "dockerfile";
        if (name.endsWith("makefile")) return "makefile";
        int dot = name.lastIndexOf('.');
        return dot < 0 ? "" : name.substring(dot + 1);
    }

    /** ``` fence names → the extensions used above. */
    private static String alias(String lang) {
        return switch (lang) {
            case "python", "py3" -> "py";
            case "javascript", "node" -> "js";
            case "typescript" -> "ts";
            case "shell", "console", "shellscript" -> "sh";
            case "powershell", "pwsh" -> "ps1";
            case "kotlin" -> "kt";
            case "rust" -> "rs";
            case "csharp", "c#" -> "cs";
            case "c++" -> "cpp";
            case "golang" -> "go";
            case "ruby" -> "rb";
            case "yaml" -> "yml";
            case "markdown" -> "md";
            default -> lang;
        };
    }

    /** One line as colored text; inBlock[0] carries an open /* comment to the next line. */
    static MutableComponent line(String text, String lang, boolean[] inBlock) {
        lang = alias(lang == null ? "" : lang.toLowerCase(Locale.ROOT));
        MutableComponent out = Component.literal("");
        if (lang.equals("md") || lang.equals("markdown")) return markdown(text);
        if (PLAIN_LANGS.contains(lang)) return colored(out, text, PLAIN);  // prose and logs: no fake keywords
        boolean hash = HASH.contains(lang), cLike = C_LIKE.contains(lang) || !hash;
        if (lang.equals("xml") || lang.equals("html")) return xml(text);
        int i = 0;
        if (inBlock[0]) {
            int end = text.indexOf("*/");
            if (end < 0) return colored(out, text, COMMENT);
            colored(out, text.substring(0, end + 2), COMMENT);
            i = end + 2;
            inBlock[0] = false;
        }
        while (i < text.length()) {
            // comments first: they win over everything on the rest of the line
            int lineComment = indexOfComment(text, i, hash, cLike, lang);
            int blockStart = cLike ? text.indexOf("/*", i) : -1;
            int stop = minPositive(lineComment, blockStart, text.length());
            tokens(out, text.substring(i, stop), lang);
            if (stop == text.length()) break;
            if (stop == lineComment) { colored(out, text.substring(stop), COMMENT); break; }
            int end = text.indexOf("*/", stop + 2);
            if (end < 0) { colored(out, text.substring(stop), COMMENT); inBlock[0] = true; break; }
            colored(out, text.substring(stop, end + 2), COMMENT);
            i = end + 2;
        }
        return out;
    }

    private static int indexOfComment(String text, int from, boolean hash, boolean cLike, String lang) {
        boolean inStr = false;
        char quote = 0;
        for (int i = from; i < text.length(); i++) {
            char c = text.charAt(i);
            if (inStr) {
                if (c == '\\') { i++; continue; }
                if (c == quote) inStr = false;
                continue;
            }
            if (c == '"' || c == '\'' || c == '`') { inStr = true; quote = c; continue; }
            if (cLike && !lang.equals("json") && c == '/' && i + 1 < text.length() && text.charAt(i + 1) == '/') return i;
            if (hash && c == '#') return i;
            if ((lang.equals("lua") || lang.equals("sql")) && c == '-' && i + 1 < text.length() && text.charAt(i + 1) == '-') return i;
        }
        return -1;
    }

    private static int minPositive(int a, int b, int fallback) {
        int m = fallback;
        if (a >= 0) m = Math.min(m, a);
        if (b >= 0) m = Math.min(m, b);
        return m;
    }

    private static void tokens(MutableComponent out, String text, String lang) {
        Matcher m = TOKENS.matcher(text);
        int last = 0;
        boolean keyValue = lang.equals("json") || lang.equals("yml") || lang.equals("yaml") || lang.equals("toml");
        while (m.find()) {
            if (m.start() > last) colored(out, text.substring(last, m.start()), PLAIN);
            int color = PLAIN;
            if (m.group("str") != null) {
                // JSON/YAML keys: a string right before ':' is a key
                color = keyValue && text.substring(m.end()).stripLeading().startsWith(":") ? KEY : STRING;
            } else if (m.group("num") != null) color = NUMBER;
            else if (m.group("ann") != null) color = ANNOTATION;
            else if (m.group("kw") != null) color = KEYWORD;
            else if (m.group("type") != null) color = TYPE;
            else if (m.group("punct") != null) color = PUNCT;
            colored(out, m.group(), color);
            last = m.end();
        }
        if (last < text.length()) colored(out, text.substring(last), keyValue && last == 0 ? KEY : PLAIN);
    }

    private static MutableComponent xml(String text) {
        MutableComponent out = Component.literal("");
        Matcher m = Pattern.compile("(<!--.*?-->)|(</?[\\w:.-]+)|(\"[^\"]*\")|([\\w:.-]+=)|(/?>)").matcher(text);
        int last = 0;
        while (m.find()) {
            if (m.start() > last) colored(out, text.substring(last, m.start()), PLAIN);
            int color = m.group(1) != null ? COMMENT : m.group(2) != null ? KEYWORD : m.group(3) != null ? STRING
                : m.group(4) != null ? KEY : PUNCT;
            colored(out, m.group(), color);
            last = m.end();
        }
        if (last < text.length()) colored(out, text.substring(last), PLAIN);
        return out;
    }

    private static MutableComponent markdown(String text) {
        MutableComponent out = Component.literal("");
        String t = text.stripLeading();
        if (t.startsWith("#")) return colored(out, text, HEADING);
        if (t.startsWith("```")) return colored(out, text, COMMENT);
        if (t.startsWith("- ") || t.startsWith("* ") || t.matches("\\d+[.)] .*")) {
            int lead = text.length() - t.length(), cut = t.indexOf(' ') + 1;
            colored(out, text.substring(0, lead + cut), KEYWORD);
            text = text.substring(lead + cut);
        }
        Matcher m = Pattern.compile("(`[^`]+`)|(\\*\\*[^*]+\\*\\*)|(\\[[^\\]]+]\\([^)]+\\))").matcher(text);
        int last = 0;
        while (m.find()) {
            if (m.start() > last) colored(out, text.substring(last, m.start()), PLAIN);
            colored(out, m.group(), m.group(1) != null ? STRING : m.group(2) != null ? HEADING : TYPE);
            last = m.end();
        }
        if (last < text.length()) colored(out, text.substring(last), PLAIN);
        return out;
    }

    private static MutableComponent colored(MutableComponent out, String text, int color) {
        if (!text.isEmpty()) out.append(Component.literal(text).withColor(color));
        return out;
    }
}
