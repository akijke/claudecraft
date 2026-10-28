package dev.claudechat;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.fabricmc.loader.api.FabricLoader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * Prompts sent from the K window (newest last) and the unsent draft per mode,
 * kept in config/claude-chat/input-history.json so they survive closing the window
 * and restarting the game. ↑/↓ in the input box walk through them.
 */
final class InputHistory {
    static final int LIMIT = 200;
    private static final Path FILE = FabricLoader.getInstance().getConfigDir().resolve("claude-chat/input-history.json");
    private static final List<String> items = new ArrayList<>();
    private static final JsonObject drafts = new JsonObject();
    private static boolean loaded;

    private final List<String> view;
    private int index;          // == size: editing the new line
    private String pending = "";  // what was typed before browsing started

    private InputHistory() {
        load();
        view = new ArrayList<>(items);
        index = view.size();
    }

    static InputHistory cursor() { return new InputHistory(); }

    static synchronized void load() {
        if (loaded) return;
        loaded = true;
        try {
            if (!Files.isRegularFile(FILE)) return;
            JsonObject o = JsonParser.parseString(Files.readString(FILE, StandardCharsets.UTF_8)).getAsJsonObject();
            for (JsonElement e : o.getAsJsonArray("items")) items.add(e.getAsString());
            if (o.has("drafts")) for (String k : o.getAsJsonObject("drafts").keySet())
                drafts.addProperty(k, o.getAsJsonObject("drafts").get(k).getAsString());
        } catch (Exception ignored) { }
    }

    private static synchronized void save() {
        try {
            JsonObject o = new JsonObject();
            JsonArray a = new JsonArray();
            items.forEach(a::add);
            o.add("items", a);
            o.add("drafts", drafts);
            Files.createDirectories(FILE.getParent());
            Files.writeString(FILE, o.toString(), StandardCharsets.UTF_8);
        } catch (Exception ignored) { }
    }

    static synchronized void add(String text) {
        load();
        text = text.strip();
        if (text.isEmpty()) return;
        items.remove(text);  // re-used prompts move to the end, no duplicates
        items.add(text);
        while (items.size() > LIMIT) items.remove(0);
        save();
    }

    static synchronized String draft(String mode) {
        load();
        return drafts.has(mode) ? drafts.get(mode).getAsString() : "";
    }

    static synchronized void setDraft(String mode, String text) {
        load();
        if (text == null || text.isBlank()) drafts.remove(mode); else drafts.addProperty(mode, text);
        save();
    }

    static synchronized List<String> all() { load(); return new ArrayList<>(items); }

    /** Older prompt, or null when there is none. `current` is what the box shows now. */
    String older(String current) {
        if (index == view.size()) pending = current;
        if (index == 0) return null;
        return view.get(--index);
    }

    /** Newer prompt; past the newest returns what was typed before browsing. */
    String newer() {
        if (index >= view.size()) return null;
        index++;
        return index == view.size() ? pending : view.get(index);
    }

    boolean browsing() { return index < view.size(); }
}
