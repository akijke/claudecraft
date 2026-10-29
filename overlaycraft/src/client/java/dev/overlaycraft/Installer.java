package dev.overlaycraft;

import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.fabricmc.loader.api.FabricLoader;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;

/** Puts the two companions where they are used: the browser extension and Claude's tool. */
final class Installer {
    private static final String[] BROWSER_FILES = {"manifest.json", "background.js", "eda-hook.js", "eda-relay.js", "media-hook.js", "media-relay.js"};

    private Installer() { }

    /** config/overlaycraft/browser-extension: the folder to load as an unpacked extension. */
    static Path unpackBrowserExtension() {
        Path dir = Config.DIR.resolve("browser-extension");
        for (String name : BROWSER_FILES) copy("/overlaycraft/browser-extension/" + name, dir.resolve(name));
        return dir;
    }

    /**
     * Claudecraft's MCP extension (<workspace>/extensions/overlaycraft): gives Claude the
     * overlay_* tools. Written only when Claudecraft is set up in this game folder.
     */
    static String installClaudeExtension() {
        Path config = FabricLoader.getInstance().getConfigDir().resolve("claude-chat");
        if (!Files.isDirectory(config)) return "Claudecraft не найден в этой папке игры — инструменты для Claude не установлены. "
            + "Для Claude Code на ПК: claude mcp add overlaycraft -- python \"" + Config.DIR.resolve("claude-extension/server.py") + "\"";
        Path workspace = config.resolve("workspace");
        try {
            Path bridgeConfig = config.resolve("bridge-config.json");
            if (Files.exists(bridgeConfig)) {
                JsonObject o = JsonParser.parseString(Files.readString(bridgeConfig)).getAsJsonObject();
                if (o.has("workspace")) workspace = Path.of(o.get("workspace").getAsString());
            }
        } catch (Exception ignored) { }
        // a copy outside the workspace too, for Claude Code on the PC (claude mcp add)
        copy("/overlaycraft/claude-extension/server.py", Config.DIR.resolve("claude-extension/server.py"));
        if (!Files.isDirectory(workspace)) return "Папка workspace Claudecraft не найдена: " + workspace;
        Path ext = workspace.resolve("extensions/overlaycraft");
        boolean changed = copy("/overlaycraft/claude-extension/server.py", ext.resolve("server.py"));
        // the bridge's own Python runs it (the server needs nothing beyond the standard library)
        Path venv = config.resolve("runtime/venv/" + (isWindows() ? "Scripts/python.exe" : "bin/python"));
        JsonObject manifest = new JsonObject();
        manifest.addProperty("id", "overlaycraft");
        manifest.addProperty("enabled", true);
        JsonObject server = new JsonObject();
        server.addProperty("command", Files.exists(venv) ? venv.toString() : isWindows() ? "python" : "python3");
        JsonArray args = new JsonArray();
        args.add("${extension}/server.py");
        server.add("args", args);
        manifest.add("server", server);
        changed |= write(ext.resolve("manifest.json"), new GsonBuilder().setPrettyPrinting().create().toJson(manifest).getBytes(StandardCharsets.UTF_8));
        return changed ? "Инструменты оверлея для Claude установлены: " + ext + " — выполни /cc reload"
            : "Инструменты оверлея для Claude уже стоят (" + ext + "). Claude видит overlay_status, overlay_media и другие.";
    }

    /**
     * Opens the extensions page of the browser that plays (else the first one installed);
     * returns its name, or "" when none was found. Windows only.
     */
    static String openExtensionsPage() {
        if (!isWindows()) return "";
        String local = System.getenv("LOCALAPPDATA"), pf = System.getenv("ProgramFiles"), pf86 = System.getenv("ProgramFiles(x86)");
        // {name, hint in the media session id, exe, page}
        String[][] browsers = {
            {"Opera GX", "operagx", local + "\\Programs\\Opera GX\\opera.exe", "opera://extensions"},
            {"Opera", "opera", local + "\\Programs\\Opera\\opera.exe", "opera://extensions"},
            {"Яндекс Браузер", "yandex", local + "\\Yandex\\YandexBrowser\\Application\\browser.exe", "browser://extensions"},
            {"Chrome", "chrome", pf + "\\Google\\Chrome\\Application\\chrome.exe", "chrome://extensions"},
            {"Chrome", "chrome", local + "\\Google\\Chrome\\Application\\chrome.exe", "chrome://extensions"},
            {"Edge", "edge", pf86 + "\\Microsoft\\Edge\\Application\\msedge.exe", "edge://extensions"},
        };
        StringBuilder ids = new StringBuilder();
        for (Media.Session s : Media.sessions) ids.append(s.id.toLowerCase()).append(' ');
        String[] pick = null;
        for (String[] b : browsers)
            if (Files.exists(Path.of(b[2])) && ids.toString().contains(b[1])) { pick = b; break; }
        if (pick == null) for (String[] b : browsers) if (Files.exists(Path.of(b[2]))) { pick = b; break; }
        if (pick == null) return "";
        try {
            new ProcessBuilder(pick[2], pick[3]).start();
            return pick[0];
        } catch (Exception e) {
            return "";
        }
    }

    private static boolean isWindows() { return System.getProperty("os.name", "").toLowerCase().contains("win"); }

    /** Copies a resource from the jar; true when the file changed. */
    private static boolean copy(String resource, Path target) {
        try (InputStream in = Installer.class.getResourceAsStream(resource)) {
            if (in == null) { Overlaycraft.LOG.warn("Missing resource {}", resource); return false; }
            return write(target, in.readAllBytes());
        } catch (Exception e) {
            Overlaycraft.LOG.warn("Can't write {}: {}", target, e.toString());
            return false;
        }
    }

    private static boolean write(Path target, byte[] bytes) {
        try {
            if (Files.exists(target) && Arrays.equals(Files.readAllBytes(target), bytes)) return false;
            Files.createDirectories(target.getParent());
            Files.write(target, bytes);
            return true;
        } catch (Exception e) {
            Overlaycraft.LOG.warn("Can't write {}: {}", target, e.toString());
            return false;
        }
    }
}
