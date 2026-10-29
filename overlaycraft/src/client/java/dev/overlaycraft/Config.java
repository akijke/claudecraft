package dev.overlaycraft;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import net.fabricmc.loader.api.FabricLoader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

/** config/overlaycraft/overlay.json: what is shown, where, and how see-through. */
final class Config {
    static final Path DIR = FabricLoader.getInstance().getConfigDir().resolve("overlaycraft");
    private static final Path FILE = DIR.resolve("overlay.json");
    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();

    /** One widget: position as a fraction of the free room (0 = left/top, 1 = right/bottom). */
    static final class Widget {
        double fx, fy;
        boolean visible = true, compact;
        Widget() { }
        Widget(double fx, double fy) { this.fx = fx; this.fy = fy; }
    }

    static boolean enabled = true;
    /** Other screens (inventory, menus): "hide" — not drawn, "behind" — drawn under the screen's blur. */
    static String screens = "hide";
    /** Background opacity of the panels, percent. */
    static int opacity = 55;
    static final Map<String, Widget> WIDGETS = new LinkedHashMap<>();

    private Config() { }

    static Map<String, Widget> defaults() {
        Map<String, Widget> d = new LinkedHashMap<>();
        d.put("music", new Widget(1.0, 0.25));
        d.put("food", new Widget(1.0, 0.25));
        d.put("tabs", new Widget(1.0, 0.25));
        return d;
    }

    static Widget widget(String name) {
        return WIDGETS.computeIfAbsent(name, k -> defaults().getOrDefault(k, new Widget(0.5, 0.5)));
    }

    static void load() {
        WIDGETS.clear();
        WIDGETS.putAll(defaults());
        try {
            if (!Files.exists(FILE)) return;
            JsonObject o = GSON.fromJson(Files.readString(FILE, StandardCharsets.UTF_8), JsonObject.class);
            if (o == null) return;
            if (o.has("enabled")) enabled = o.get("enabled").getAsBoolean();
            if (o.has("screens")) screens = "behind".equals(o.get("screens").getAsString()) ? "behind" : "hide";
            if (o.has("opacity")) opacity = clamp(o.get("opacity").getAsInt(), 0, 100);
            if (o.has("widgets") && o.get("widgets").isJsonObject())
                for (var e : o.getAsJsonObject("widgets").entrySet()) {
                    if (!WIDGETS.containsKey(e.getKey()) || !e.getValue().isJsonObject()) continue;
                    Widget w = GSON.fromJson(e.getValue(), Widget.class);
                    w.fx = clamp(w.fx); w.fy = clamp(w.fy);
                    WIDGETS.put(e.getKey(), w);
                }
        } catch (Exception e) {
            Overlaycraft.LOG.warn("overlay.json is unreadable, using defaults: {}", e.toString());
        }
    }

    static void save() {
        JsonObject o = new JsonObject();
        o.addProperty("enabled", enabled);
        o.addProperty("screens", screens);
        o.addProperty("opacity", opacity);
        o.add("widgets", GSON.toJsonTree(WIDGETS));
        try {
            Files.createDirectories(DIR);
            Files.writeString(FILE, GSON.toJson(o), StandardCharsets.UTF_8);
        } catch (Exception e) {
            Overlaycraft.LOG.warn("Can't save overlay.json: {}", e.toString());
        }
    }

    static double clamp(double v) { return Double.isNaN(v) ? 0 : Math.max(0, Math.min(1, v)); }
    static int clamp(int v, int lo, int hi) { return Math.max(lo, Math.min(hi, v)); }
}
