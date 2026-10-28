package dev.claudechat;

import net.minecraft.client.Minecraft;
import org.lwjgl.sdl.SDLVideo;
import java.util.List;
import java.util.Locale;

/**
 * System notifications when the game window is in the background or minimized:
 * Windows — a toast through PowerShell's WinRT API plus a flashing taskbar button
 * (SDL_FlashWindow); Linux — notify-send; macOS — osascript. No extra
 * libraries. Switch off: /claude osnotify off.
 */
final class OsNotify {
    private OsNotify() { }

    static boolean gameInBackground() {
        // Minecraft 26.3 runs on SDL3: the window handle is an SDL_Window*
        long flags = SDLVideo.SDL_GetWindowFlags(Minecraft.getInstance().getWindow().handle());
        return (flags & SDLVideo.SDL_WINDOW_INPUT_FOCUS) == 0 || (flags & SDLVideo.SDL_WINDOW_MINIMIZED) != 0;
    }

    static boolean enabled() {
        ClaudeChat mod = ClaudeChat.instance();
        return mod == null || mod.launcher.flag("osnotify", true);
    }

    /** Shows `title / text` as a system notification (if the game is in the background). */
    static void show(String title, String text) {
        if (!enabled() || !gameInBackground()) return;
        long window = Minecraft.getInstance().getWindow().handle();
        SDLVideo.SDL_FlashWindow(window, SDLVideo.SDL_FLASH_UNTIL_FOCUSED);  // taskbar flash / dock bounce
        List<String> command = command(title, text == null ? "" : text);
        if (command.isEmpty()) return;
        Thread t = new Thread(() -> {
            try {
                Process p = new ProcessBuilder(command).redirectErrorStream(true).start();
                p.getOutputStream().close();
                p.getInputStream().transferTo(java.io.OutputStream.nullOutputStream());
            } catch (Exception ignored) { }  // a missing notifier must never break the game
        }, "claudecraft-notify");
        t.setDaemon(true);
        t.start();
    }

    static List<String> command(String title, String text) {
        String os = System.getProperty("os.name").toLowerCase(Locale.ROOT);
        if (os.contains("win")) {
            String xml = "<toast><visual><binding template='ToastGeneric'><text>" + escapeXml(title) + "</text><text>"
                + escapeXml(text) + "</text></binding></visual></toast>";
            // Single quotes are doubled for the PowerShell string; the script reads it from an env-free argument.
            String script = "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
                + "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null;"
                + "$x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml('" + xml.replace("'", "''") + "');"
                + "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
                + "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show("
                + "[Windows.UI.Notifications.ToastNotification]::new($x))";
            return List.of("powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script);
        }
        if (os.contains("mac")) {
            return List.of("osascript", "-e", "display notification \"" + text.replace("\"", "'") + "\" with title \""
                + title.replace("\"", "'") + "\"");
        }
        return List.of("notify-send", "-a", "Claudecraft", title, text);
    }

    private static String escapeXml(String s) {
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&apos;").replace("\"", "&quot;")
            .replace("\n", " ");
    }
}
