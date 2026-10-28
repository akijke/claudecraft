package dev.claudechat;

import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.resources.sounds.SimpleSoundInstance;
import net.minecraft.resources.Identifier;
import net.minecraft.sounds.SoundEvent;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Sounds for "done", "Claude asks" and "error", chosen per event in client.json → "sounds"
 * (/claude sound done|ask|error <id|off|default>, volume 0..1, test). Any sound id
 * works, including ones added by a resource pack (assets/<ns>/sounds.json), so custom
 * sounds need no code.
 */
final class Sounds {
    static final Map<String, String> DEFAULTS = new LinkedHashMap<>();
    static {
        DEFAULTS.put("done", "minecraft:entity.experience_orb.pickup");
        DEFAULTS.put("ask", "minecraft:block.note_block.pling");
        DEFAULTS.put("error", "minecraft:block.note_block.bass");
    }
    /** A few vanilla sounds that work well as notifications (offered as buttons). */
    static final String[] SUGGESTED = {
        "minecraft:entity.experience_orb.pickup", "minecraft:block.note_block.pling", "minecraft:block.note_block.bell",
        "minecraft:block.note_block.chime", "minecraft:block.note_block.bass", "minecraft:entity.player.levelup",
        "minecraft:block.amethyst_block.chime", "minecraft:ui.button.click", "minecraft:entity.villager.no",
        "minecraft:block.bell.use",
    };

    private Sounds() { }

    private static JsonObject settings() {
        ClaudeChat mod = ClaudeChat.instance();
        if (mod == null) return new JsonObject();
        JsonObject all = mod.launcher.settings;
        if (!all.has("sounds") || !all.get("sounds").isJsonObject()) all.add("sounds", new JsonObject());
        return all.getAsJsonObject("sounds");
    }

    static String sound(String kind) {
        JsonObject s = settings();
        return s.has(kind) ? s.get(kind).getAsString() : DEFAULTS.getOrDefault(kind, "off");
    }

    static float volume() {
        JsonObject s = settings();
        return s.has("volume") ? Math.max(0f, Math.min(1f, s.get("volume").getAsFloat())) : 0.8f;
    }

    static void set(String kind, String value) {
        JsonObject s = settings();
        if (kind.equals("volume")) s.addProperty("volume", Math.max(0f, Math.min(1f, Float.parseFloat(value))));
        else if (value.equals("default")) s.remove(kind);
        else {
            if (!value.equals("off")) Identifier.parse(value);  // throws on a malformed id
            s.addProperty(kind, value.contains(":") || value.equals("off") ? value : "minecraft:" + value);
        }
        ClaudeChat.instance().launcher.saveAll();
    }

    /** Plays the sound for `kind` (done | ask | error) unless it is off. */
    static void play(String kind) {
        String id = sound(kind);
        if (id.equals("off")) return;
        try {
            SoundEvent event = SoundEvent.createVariableRangeEvent(Identifier.parse(id));
            float pitch = kind.equals("ask") ? 1.6f : 1.0f;
            Minecraft.getInstance().getSoundManager().play(SimpleSoundInstance.forUI(event, pitch, volume()));
        } catch (RuntimeException ignored) { }  // unknown id: silent instead of a crash
    }
}
