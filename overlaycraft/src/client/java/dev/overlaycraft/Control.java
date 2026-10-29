package dev.overlaycraft;

import com.google.gson.JsonObject;

/** The actions Claude (POST /api/control) and the /ov commands share. Main thread. */
final class Control {
    private Control() { }

    static JsonObject run(JsonObject c) {
        String action = Browser.str(c, "action");
        switch (action) {
            case "media" -> {
                String command = Browser.str(c, "command");
                if (!Media.control(command, Browser.str(c, "session")))
                    return Server.error(Media.sessions.isEmpty() ? "nothing is playing" : "unknown command: " + command);
            }
            case "pick" -> {
                String session = Browser.str(c, "session");
                if (Media.sessions.stream().noneMatch(s -> s.id.equals(session))) return Server.error("no such session");
                Media.picked = session;
            }
            case "focus" -> {
                if (!Browser.connected()) return Server.error("the browser extension is not connected");
                Browser.focus(Browser.intOf(c, "tab"));
            }
            case "open" -> {
                String url = Browser.str(c, "url");
                if (!(url.startsWith("https://") || url.startsWith("http://"))) return Server.error("only http(s) addresses");
                Browser.open(url);
            }
            case "food" -> {
                // corrects the order on screen (keeps its place, link, time) or starts one
                Browser.Order old = Browser.order != null && Browser.order.active() ? Browser.order : null;
                Browser.Order o = new Browser.Order();
                if (old != null) { o.service = old.service; o.place = old.place; o.status = old.status; o.url = old.url; o.step = old.step; o.etaAt = old.etaAt; }
                o.manual = true;
                o.updated = System.currentTimeMillis();
                if (c.has("service")) o.service = Browser.str(c, "service");
                if (c.has("place")) o.place = Browser.str(c, "place");
                if (c.has("status")) {
                    o.status = Browser.str(c, "status");
                    String s = o.status.toLowerCase(java.util.Locale.ROOT);
                    if (s.contains("отмен")) o.step = -1;
                    else if (s.contains("доставлен") || s.contains("получен")) o.step = 3;
                    else if (s.contains("пути") || s.contains("курьер") || s.contains("едет")) o.step = 2;
                    else if (s.contains("готов") || s.contains("собира")) o.step = 1;
                    else if (s.contains("принят")) o.step = 0;
                }
                if (c.has("step")) o.step = Math.max(-1, Math.min(3, Browser.intOf(c, "step")));
                if (c.has("minutes")) o.etaAt = System.currentTimeMillis() + Math.round(c.get("minutes").getAsDouble() * 60_000);
                if (o.status.isEmpty() && o.place.isEmpty()) return Server.error("give a status or a place");
                Browser.setOrder(o);
            }
            case "food_clear" -> Browser.order = null;
            case "widget" -> {
                String name = Browser.str(c, "name");
                if (!Config.WIDGETS.containsKey(name)) return Server.error("widgets: " + String.join(", ", Config.WIDGETS.keySet()));
                Config.Widget w = Config.widget(name);
                if (c.has("visible")) w.visible = c.get("visible").getAsBoolean();
                if (c.has("compact")) w.compact = c.get("compact").getAsBoolean();
                if (c.has("x")) w.fx = Config.clamp(c.get("x").getAsDouble() / 100.0);
                if (c.has("y")) w.fy = Config.clamp(c.get("y").getAsDouble() / 100.0);
                Config.save();
            }
            case "overlay" -> {
                if (c.has("enabled")) Config.enabled = c.get("enabled").getAsBoolean();
                if (c.has("screens")) Config.screens = "behind".equals(Browser.str(c, "screens")) ? "behind" : "hide";
                if (c.has("opacity")) Config.opacity = Config.clamp(c.get("opacity").getAsInt(), 0, 100);
                if (c.has("reset") && c.get("reset").getAsBoolean()) { Config.WIDGETS.clear(); Config.WIDGETS.putAll(Config.defaults()); }
                Config.save();
            }
            default -> {
                return Server.error("actions: media, pick, focus, open, food, food_clear, widget, overlay");
            }
        }
        JsonObject ok = Server.state();
        ok.addProperty("ok", true);
        return ok;
    }
}
