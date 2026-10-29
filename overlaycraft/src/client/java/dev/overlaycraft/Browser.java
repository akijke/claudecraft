package dev.overlaycraft;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.net.URI;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.LinkedBlockingQueue;

/**
 * What the browser extension reports (browser-extension/): open tabs and the Yandex Eda
 * order, plus the commands the game sends back to it (focus a tab, open a page).
 * Fields are read and written on the main thread; the queue is shared with the server.
 */
final class Browser {
    static final class Tab {
        int id, window;
        String title = "", url = "";
        boolean audible, active, focused;

        String host() {
            try {
                String h = URI.create(url).getHost();
                return h == null ? "" : h.startsWith("www.") ? h.substring(4) : h;
            } catch (Exception e) { return ""; }
        }
    }

    /** The current food order. step: 0 accepted, 1 cooking, 2 on the way, 3 delivered, -1 cancelled. */
    static final class Order {
        String service = "Яндекс Еда", place = "", status = "", url = "";
        int step;
        long etaAt;          // epoch ms when it should arrive, 0 = unknown
        long updated = System.currentTimeMillis();
        boolean manual;

        /** Minutes left, or -1 when unknown. */
        int minutesLeft() {
            if (etaAt <= 0) return -1;
            return (int) Math.max(0, Math.ceil((etaAt - System.currentTimeMillis()) / 60_000.0));
        }

        /** Shown while it is under way, and for 15 minutes after it was delivered or cancelled. */
        boolean active() {
            long age = System.currentTimeMillis() - updated;
            if (step >= 3 || step < 0) return age < 15 * 60_000L;
            return age < 4 * 3_600_000L;
        }
    }

    static List<Tab> tabs = List.of();
    static Order order;
    /** When the extension last reported (0 = never this session). */
    static long seen;
    static String browserName = "";
    /** What the extension knows about Yandex Eda: ok | login | error | unknown. */
    static String edaState = "unknown";
    static final LinkedBlockingQueue<JsonObject> COMMANDS = new LinkedBlockingQueue<>();

    private Browser() { }

    static boolean connected() { return System.currentTimeMillis() - seen < 75_000; }

    /** POST /ext/state from the extension (main thread). */
    static void report(JsonObject body) {
        seen = System.currentTimeMillis();
        if (body.has("browser")) browserName = body.get("browser").getAsString();
        if (body.has("tabs") && body.get("tabs").isJsonArray()) tabs = parseTabs(body.getAsJsonArray("tabs"));
        if (body.has("order") && body.get("order").isJsonObject()) {
            Order o = parseOrder(body.getAsJsonObject("order"));
            // a manual entry wins until the extension sees a newer real update
            if (o != null && (order == null || !order.manual || o.updated > order.updated)) setOrder(o);
            // the extension follows no order: drop the one it gave (a manual one stays)
            if (o == null && bool(body.getAsJsonObject("order"), "none") && order != null && !order.manual) order = null;
        }
        if (body.has("eda")) edaState = str(body, "eda");
    }

    /** Replaces the order; a new step (cooking, on the way, delivered...) gets a toast and a sound. */
    static void setOrder(Order o) {
        Order old = order;
        order = o;
        if (o == null || (old != null && old.step == o.step)) return;
        if (old == null && o.step <= 0) return;  // a fresh order: the card is enough
        String head = switch (o.step) {
            case -1 -> "✖ Заказ отменён";
            case 1 -> "Ресторан готовит заказ";
            case 2 -> "Курьер в пути";
            case 3 -> "✔ Заказ доставлен";
            default -> "Заказ принят";
        };
        int left = o.minutesLeft();
        Overlaycraft.notify(head, o.place + (left > 0 && o.step < 3 ? " · ~" + left + " мин" : ""), o.step == 2 || o.step == 3);
    }

    static List<Tab> parseTabs(JsonArray array) {
        List<Tab> list = new ArrayList<>();
        for (JsonElement el : array) {
            if (!el.isJsonObject()) continue;
            JsonObject t = el.getAsJsonObject();
            Tab x = new Tab();
            x.id = intOf(t, "id"); x.window = intOf(t, "window");
            x.title = str(t, "title"); x.url = str(t, "url");
            x.audible = bool(t, "audible"); x.active = bool(t, "active"); x.focused = bool(t, "focused");
            list.add(x);
        }
        return list;
    }

    static Order parseOrder(JsonObject o) {
        if (bool(o, "none")) return null;
        Order x = new Order();
        if (o.has("service")) x.service = str(o, "service");
        x.place = str(o, "place");
        x.status = str(o, "status");
        x.url = str(o, "url");
        x.step = o.has("step") ? intOf(o, "step") : 0;
        if (o.has("etaAt")) x.etaAt = (long) o.get("etaAt").getAsDouble();
        else if (o.has("etaMinutes")) x.etaAt = System.currentTimeMillis() + (long) (o.get("etaMinutes").getAsDouble() * 60_000);
        if (o.has("updated")) x.updated = (long) o.get("updated").getAsDouble();
        x.manual = bool(o, "manual");
        if (x.status.isEmpty() && x.place.isEmpty()) return null;
        return x;
    }

    /** Media commands sent to a tab, by id: [action, Windows session] for the fallback. */
    private static final java.util.Map<Integer, String[]> PENDING = new java.util.HashMap<>();
    private static int nextId = 1;

    /** Next/prev (or play/pause) in a tab's own player; Windows does it if the page can't. */
    static void media(int tab, String action, String session) {
        int id = nextId++;
        PENDING.put(id, new String[]{action, session});
        JsonObject c = new JsonObject();
        c.addProperty("type", "media");
        c.addProperty("tab", tab);
        c.addProperty("action", action);
        c.addProperty("id", id);
        COMMANDS.offer(c);
    }

    /** POST /ext/done: how the page carried the command out. */
    static void done(JsonObject body) {
        String[] p = PENDING.remove(intOf(body, "id"));
        if (p == null) return;
        String how = str(body, "how");
        if (how.equals("none") || how.equals("no-script") || how.equals("timeout")) Media.windows(p[0], p[1]);
    }

    /** Asks the extension to switch to a tab (and bring its window up). */
    static void focus(int tab) {
        JsonObject c = new JsonObject();
        c.addProperty("type", "focus");
        c.addProperty("tab", tab);
        COMMANDS.offer(c);
    }

    /** Opens a page: through the extension (reuses a tab with the same address), else the system browser. */
    static void open(String url) {
        if (url == null || !(url.startsWith("https://") || url.startsWith("http://"))) return;
        if (connected()) {
            JsonObject c = new JsonObject();
            c.addProperty("type", "open");
            c.addProperty("url", url);
            COMMANDS.offer(c);
        } else {
            openOutside(url);
        }
    }

    /** A web address or a folder, opened by the system (default browser / Explorer). */
    static void openOutside(String target) {
        try {
            String os = System.getProperty("os.name", "").toLowerCase(Locale.ROOT);
            // rundll32 takes the address as one argument: no shell, so "&" in a link is safe
            if (os.contains("win")) new ProcessBuilder("rundll32.exe", "url.dll,FileProtocolHandler", target).start();
            else new ProcessBuilder(os.contains("mac") ? "open" : "xdg-open", target).start();
        } catch (Exception e) {
            Overlaycraft.LOG.warn("Can't open {}: {}", target, e.toString());
        }
    }

    // ---- where a media session comes from -----------------------------------------

    record Source(String name, int color) { }

    static int ACCENT = 0xFFD97757;  // Claudecraft orange (Palette follows its theme)

    /** Names the service of a media session, by its app and the browser tab that plays it. */
    static Source source(Media.Session s) {
        String id = s.id.toLowerCase(Locale.ROOT);
        if (id.contains("telegram")) return new Source("Telegram", 0xFF5CC8E6);
        if (id.contains("spotify")) return new Source("Spotify", 0xFF6BD08B);
        if (id.contains("yandex.music") || id.contains("yandexmusic")) return new Source("Я.Музыка", 0xFFF2C14E);
        Tab tab = tabFor(s);
        if (tab != null) {
            Source site = site(tab.host());
            if (site != null) return site;
            return new Source(tab.host(), ACCENT);
        }
        return new Source(appName(id), ACCENT);
    }

    static Source site(String host) {
        if (host.endsWith("youtube.com") || host.equals("youtu.be")) return new Source(host.startsWith("music.") ? "YT Music" : "YouTube", 0xFFF0626A);
        if (host.endsWith("soundcloud.com")) return new Source("SoundCloud", 0xFFFF8A3D);
        if (host.endsWith("vk.com") || host.endsWith("vk.ru")) return new Source("VK", 0xFF6CA8FF);
        if (host.endsWith("huevo.su")) return new Source("huevo.su", 0xFFB48EF0);
        if (host.endsWith("web.telegram.org")) return new Source("Telegram", 0xFF5CC8E6);
        if (host.startsWith("music.yandex")) return new Source("Я.Музыка", 0xFFF2C14E);
        if (host.endsWith("spotify.com")) return new Source("Spotify", 0xFF6BD08B);
        return null;
    }

    /** The tab that plays this session: an audible tab whose title holds the track's title. */
    static Tab tabFor(Media.Session s) {
        if (s.title.isEmpty()) return null;
        String title = s.title.toLowerCase(Locale.ROOT);
        Tab audible = null;
        int count = 0;
        for (Tab t : tabs) {
            String tt = t.title.toLowerCase(Locale.ROOT);
            if (!tt.isEmpty() && (tt.contains(title) || title.contains(tt))) return t;
            if (t.audible) { audible = t; count++; }
        }
        return count == 1 ? audible : null;  // one tab plays sound: it is that one
    }

    private static String appName(String id) {
        if (id.contains("opera")) return "Opera";
        if (id.contains("chrome")) return "Chrome";
        if (id.contains("msedge") || id.contains("edge")) return "Edge";
        if (id.contains("yandex")) return "Яндекс Браузер";
        if (id.contains("firefox")) return "Firefox";
        if (id.contains("brave")) return "Brave";
        if (id.contains("vivaldi")) return "Vivaldi";
        String name = id.replaceAll("\\.exe$", "");
        int dot = name.lastIndexOf('.');
        return dot >= 0 && dot < name.length() - 1 && name.length() > 20 ? name.substring(dot + 1) : name;
    }

    static String str(JsonObject o, String k) { return o.has(k) && !o.get(k).isJsonNull() ? o.get(k).getAsString() : ""; }
    static boolean bool(JsonObject o, String k) { return o.has(k) && !o.get(k).isJsonNull() && o.get(k).getAsBoolean(); }
    static int intOf(JsonObject o, String k) {
        try { return o.has(k) && !o.get(k).isJsonNull() ? o.get(k).getAsInt() : 0; } catch (Exception e) { return 0; }
    }
}
