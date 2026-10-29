package dev.overlaycraft;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.minecraft.client.Minecraft;
import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * What plays on the PC, from the Windows media sessions (the ones the volume flyout
 * shows): every browser tab with media (YouTube, SoundCloud, VK, huevo.su...), Telegram
 * Desktop, Spotify, Yandex Music. A PowerShell helper (media.ps1) prints the sessions as
 * JSON lines and takes play/pause/next/prev on stdin. Windows only.
 */
final class Media {
    static final class Session {
        String id = "", title = "", artist = "", status = "";
        double pos, dur;
        boolean current, canNext, canPrev;
        long at = System.currentTimeMillis();

        boolean playing() { return "Playing".equals(status); }

        /** The position now: the helper's snapshot moved on while playing. */
        double position() {
            double p = pos + (playing() ? (System.currentTimeMillis() - at) / 1000.0 : 0);
            return dur > 0 ? Math.min(p, dur) : p;
        }
    }

    /** Main thread only. */
    static List<Session> sessions = List.of();
    /** The session the person picked in the widget (its id), or "" for the one Windows calls current. */
    static String picked = "";
    static String error = "";

    private static Process process;
    private static BufferedWriter input;
    private static long restartAt;
    private static int failures;

    private Media() { }

    static boolean supported() { return System.getProperty("os.name", "").toLowerCase(Locale.ROOT).contains("win"); }

    /** Every tick: keeps the helper running (restarts it after a crash, with a growing pause). */
    static void tick() {
        if (!supported() || DevShots.ENABLED) return;
        if (process != null && process.isAlive()) return;
        long now = System.currentTimeMillis();
        if (now < restartAt) return;
        restartAt = now + Math.min(60_000, 2_000L << Math.min(failures, 5));
        failures++;
        start();
    }

    private static void start() {
        try {
            Path script = Config.DIR.resolve("media.ps1");
            Files.createDirectories(Config.DIR);
            try (InputStream in = Media.class.getResourceAsStream("/overlaycraft/media.ps1")) {
                if (in == null) throw new IllegalStateException("media.ps1 is missing from the jar");
                Files.copy(in, script, StandardCopyOption.REPLACE_EXISTING);
            }
            process = new ProcessBuilder("powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden", "-File", script.toString())
                .redirectError(ProcessBuilder.Redirect.appendTo(Config.DIR.resolve("media.log").toFile()))
                .start();
            input = new BufferedWriter(new OutputStreamWriter(process.getOutputStream(), StandardCharsets.UTF_8));
            Process mine = process;
            Thread reader = new Thread(() -> read(mine), "overlaycraft-media");
            reader.setDaemon(true);
            reader.start();
        } catch (Exception e) {
            error = e.getMessage();
            Overlaycraft.LOG.warn("Media helper failed to start: {}", e.toString());
        }
    }

    private static void read(Process p) {
        try (BufferedReader out = new BufferedReader(new InputStreamReader(p.getInputStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = out.readLine()) != null) {
                if (!line.startsWith("{")) continue;
                JsonObject o;
                try { o = JsonParser.parseString(line).getAsJsonObject(); } catch (Exception e) { continue; }
                if (o.has("error")) {
                    String text = o.get("error").getAsString();
                    Minecraft.getInstance().execute(() -> error = text);
                    continue;
                }
                List<Session> list = parse(o.getAsJsonArray("sessions"));
                Minecraft.getInstance().execute(() -> { sessions = list; error = ""; failures = 0; });
            }
        } catch (Exception ignored) {
            // the helper exited; tick() restarts it
        }
    }

    static List<Session> parse(JsonArray array) {
        List<Session> list = new ArrayList<>();
        if (array == null) return list;
        for (JsonElement el : array) {
            if (!el.isJsonObject()) continue;
            JsonObject s = el.getAsJsonObject();
            Session x = new Session();
            x.id = str(s, "id"); x.title = str(s, "title"); x.artist = str(s, "artist"); x.status = str(s, "status");
            x.pos = num(s, "pos"); x.dur = num(s, "dur");
            x.current = s.has("current") && s.get("current").getAsBoolean();
            x.canNext = !s.has("canNext") || s.get("canNext").getAsBoolean();
            x.canPrev = !s.has("canPrev") || s.get("canPrev").getAsBoolean();
            if (!x.title.isEmpty() || x.playing()) list.add(x);
        }
        return list;
    }

    /** The session the widget shows: the picked one, else a playing one, else the current one. */
    static Session shown() {
        List<Session> list = sessions;
        if (list.isEmpty()) return null;
        for (Session s : list) if (s.id.equals(picked)) return s;
        for (Session s : list) if (s.playing() && s.current) return s;
        for (Session s : list) if (s.playing()) return s;
        for (Session s : list) if (s.current) return s;
        return list.get(0);
    }

    /** Shows the next session in the widget (click on "1/2"). */
    static void cycle() {
        List<Session> list = sessions;
        Session now = shown();
        if (list.size() < 2 || now == null) return;
        picked = list.get((list.indexOf(now) + 1) % list.size()).id;
    }

    /** toggle | play | pause | next | prev | stop, for a session id ("" = the shown one). */
    static boolean control(String action, String id) {
        if (!List.of("toggle", "play", "pause", "next", "prev", "stop").contains(action)) return false;
        Session target = null;
        for (Session s : sessions) if (s.id.equals(id)) target = s;
        if (target == null) target = shown();
        if (target == null) return false;
        // Next/previous in a browser tab: the site's own player through the extension. Windows
        // can't do it where the site gives it no "next" (a single YouTube video).
        if ((action.equals("next") || action.equals("prev")) && Browser.connected()) {
            Browser.Tab tab = Browser.tabFor(target);
            if (tab != null) {
                Browser.media(tab.id, action, target.id);
                return true;
            }
        }
        if (input == null || process == null || !process.isAlive()) return false;
        // Show the change at once; the helper confirms it a moment later
        target.pos = target.position();
        target.at = System.currentTimeMillis();
        if (action.equals("toggle")) target.status = target.playing() ? "Paused" : "Playing";
        if (action.equals("play")) target.status = "Playing";
        if (action.equals("pause")) target.status = "Paused";
        return windows(action, target.id);
    }

    /** The command through the Windows media session (the helper). */
    static boolean windows(String action, String session) {
        if (input == null || process == null || !process.isAlive()) return false;
        try {
            input.write(action + " " + session + "\n");
            input.flush();
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    static void stop() {
        try { if (input != null) { input.write("quit\n"); input.flush(); } } catch (Exception ignored) { }
        if (process != null) process.destroy();
    }

    static String time(double seconds) {
        long s = Math.max(0, Math.round(seconds));
        return s >= 3600 ? String.format("%d:%02d:%02d", s / 3600, s / 60 % 60, s % 60) : String.format("%d:%02d", s / 60, s % 60);
    }

    private static String str(JsonObject o, String k) {
        return o.has(k) && !o.get(k).isJsonNull() ? o.get(k).getAsString() : "";
    }

    private static double num(JsonObject o, String k) {
        try { return o.has(k) && !o.get(k).isJsonNull() ? o.get(k).getAsDouble() : 0; } catch (Exception e) { return 0; }
    }
}
