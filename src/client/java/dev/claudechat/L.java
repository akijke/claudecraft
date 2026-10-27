package dev.claudechat;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;

/**
 * UI language of the mod: English by default, Russian optional (settings screen).
 * Source strings in the code are Russian; L.t() looks up the English text in
 * assets/claude_chat/claudecraft_en.json. The bridge reads the same setting from
 * client.json and translates its own messages.
 */
final class L {
    private static Map<String, String> english;

    private L() { }

    static boolean russian() {
        ClaudeChat mod = ClaudeChat.instance();
        return mod != null && "ru".equals(mod.launcher.language());
    }

    static String t(String text) {
        if (russian()) return text;
        if (english == null) english = load();
        return english.getOrDefault(text, text);
    }

    private static Map<String, String> load() {
        Map<String, String> map = new HashMap<>();
        try (InputStream in = L.class.getResourceAsStream("/assets/claude_chat/claudecraft_en.json")) {
            if (in == null) return map;
            JsonObject json = JsonParser.parseReader(new InputStreamReader(in, StandardCharsets.UTF_8)).getAsJsonObject();
            for (String key : json.keySet()) map.put(key, json.get(key).getAsString());
        } catch (Exception ignored) { }
        return map;
    }
}
