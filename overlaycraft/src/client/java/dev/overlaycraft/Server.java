package dev.overlaycraft;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import net.minecraft.client.Minecraft;
import java.io.InputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.HexFormat;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/**
 * Local HTTP on 127.0.0.1:18745 (nothing listens on the network).
 *
 * /ext/*  the browser extension: POST /ext/state (tabs, order), GET /ext/poll (commands,
 *         long poll). Requests from web pages (an http(s) Origin) are refused, so a site
 *         can't feed the overlay or read the commands.
 * /api/*  Claude (claude-extension/server.py) and any local script with the token from
 *         ~/.overlaycraft/connection.json: GET /api/state, POST /api/control.
 */
final class Server {
    static final int PORT = 18745;
    static final Path CONNECTION = Path.of(System.getProperty("user.home"), ".overlaycraft", "connection.json");
    private static final Gson GSON = new Gson();
    private static HttpServer server;
    private static String token;
    static String error = "";

    private Server() { }

    static void start() {
        try {
            token = loadToken();
            server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), PORT), 16);
            server.setExecutor(Executors.newCachedThreadPool(r -> {
                Thread t = new Thread(r, "overlaycraft-http");
                t.setDaemon(true);
                return t;
            }));
            server.createContext("/", Server::handle);
            server.start();
        } catch (Exception e) {
            error = "порт " + PORT + ": " + e.getMessage();
            Overlaycraft.LOG.warn("Overlaycraft server did not start: {}", e.toString());
        }
    }

    static void stop() { if (server != null) server.stop(0); }

    private static String loadToken() throws Exception {
        try {
            JsonObject o = JsonParser.parseString(Files.readString(CONNECTION)).getAsJsonObject();
            if (o.has("token") && o.get("token").getAsString().length() >= 32) return o.get("token").getAsString();
        } catch (Exception ignored) { }
        byte[] bytes = new byte[24];
        new SecureRandom().nextBytes(bytes);
        String fresh = HexFormat.of().formatHex(bytes);
        JsonObject o = new JsonObject();
        o.addProperty("port", PORT);
        o.addProperty("token", fresh);
        Files.createDirectories(CONNECTION.getParent());
        Files.writeString(CONNECTION, GSON.toJson(o));
        return fresh;
    }

    private static void handle(HttpExchange x) {
        try (x) {
            String path = x.getRequestURI().getPath(), method = x.getRequestMethod();
            String origin = x.getRequestHeaders().getFirst("Origin");
            if (origin != null && (origin.startsWith("http://") || origin.startsWith("https://"))) { send(x, 403, error("web pages are not allowed")); return; }
            if (path.startsWith("/ext/")) {
                if (path.equals("/ext/state") && method.equals("POST")) {
                    JsonObject body = body(x);
                    onMain(() -> Browser.report(body));
                    JsonObject ok = new JsonObject();
                    ok.addProperty("ok", true);
                    send(x, 200, ok);
                } else if (path.equals("/ext/done") && method.equals("POST")) {
                    JsonObject body = body(x);
                    onMain(() -> Browser.done(body));
                    send(x, 200, new JsonObject());
                } else if (path.equals("/ext/poll")) {
                    JsonArray commands = new JsonArray();
                    JsonObject first = Browser.COMMANDS.poll(20, TimeUnit.SECONDS);
                    if (first != null) {
                        commands.add(first);
                        JsonObject more;
                        while ((more = Browser.COMMANDS.poll()) != null) commands.add(more);
                    }
                    onMain(() -> Browser.seen = System.currentTimeMillis());
                    JsonObject o = new JsonObject();
                    o.add("commands", commands);
                    send(x, 200, o);
                } else send(x, 404, error("not found"));
                return;
            }
            if (path.startsWith("/api/")) {
                if (!authorized(x.getRequestHeaders().getFirst("Authorization"))) { send(x, 401, error("bad token")); return; }
                if (path.equals("/api/state")) {
                    send(x, 200, onMainGet(Server::state));
                } else if (path.equals("/api/control") && method.equals("POST")) {
                    JsonObject body = body(x);
                    send(x, 200, onMainGet(() -> Control.run(body)));
                } else send(x, 404, error("not found"));
                return;
            }
            send(x, 404, error("not found"));
        } catch (Exception e) {
            try { send(x, 500, error(e.toString())); } catch (Exception ignored) { }
        }
    }

    private static boolean authorized(String header) {
        if (header == null || !header.startsWith("Bearer ") || token == null) return false;
        return MessageDigest.isEqual(header.substring(7).trim().getBytes(StandardCharsets.UTF_8), token.getBytes(StandardCharsets.UTF_8));
    }

    /** Everything the overlay knows, for Claude. */
    static JsonObject state() {
        JsonObject o = new JsonObject();
        JsonArray media = new JsonArray();
        Media.Session shown = Media.shown();
        for (Media.Session s : Media.sessions) {
            JsonObject m = new JsonObject();
            m.addProperty("session", s.id);
            m.addProperty("source", Browser.source(s).name());
            m.addProperty("title", s.title);
            m.addProperty("artist", s.artist);
            m.addProperty("status", s.status);
            m.addProperty("position", Media.time(s.position()));
            if (s.dur > 0) m.addProperty("duration", Media.time(s.dur));
            m.addProperty("shown", s == shown);
            media.add(m);
        }
        o.add("media", media);
        if (!Media.error.isEmpty()) o.addProperty("media_error", Media.error);
        Browser.Order order = Browser.order;
        if (order != null && order.active()) {
            JsonObject f = new JsonObject();
            f.addProperty("service", order.service);
            f.addProperty("place", order.place);
            f.addProperty("status", order.status);
            f.addProperty("step", order.step);
            if (order.minutesLeft() >= 0) f.addProperty("minutes_left", order.minutesLeft());
            f.addProperty("manual", order.manual);
            o.add("food", f);
        }
        JsonArray tabs = new JsonArray();
        for (Browser.Tab t : Browser.tabs) {
            JsonObject j = new JsonObject();
            j.addProperty("tab", t.id);
            j.addProperty("title", t.title);
            j.addProperty("url", t.url);
            if (t.audible) j.addProperty("audible", true);
            if (t.active) j.addProperty("active", true);
            tabs.add(j);
        }
        o.add("browser_tabs", tabs);
        o.addProperty("browser_extension", Browser.connected() ? "connected" : "not connected");
        o.addProperty("yandex_eda", Browser.edaState);
        JsonObject overlay = new JsonObject();
        overlay.addProperty("enabled", Config.enabled);
        overlay.addProperty("screens", Config.screens);
        overlay.addProperty("opacity", Config.opacity);
        JsonObject widgets = new JsonObject();
        for (var e : Config.WIDGETS.entrySet()) {
            JsonObject w = new JsonObject();
            w.addProperty("visible", e.getValue().visible);
            w.addProperty("compact", e.getValue().compact);
            w.addProperty("x", Math.round(e.getValue().fx * 100) + "%");
            w.addProperty("y", Math.round(e.getValue().fy * 100) + "%");
            widgets.add(e.getKey(), w);
        }
        overlay.add("widgets", widgets);
        o.add("overlay", overlay);
        return o;
    }

    private static void onMain(Runnable r) { Minecraft.getInstance().execute(r); }

    private static JsonObject onMainGet(java.util.function.Supplier<JsonObject> s) throws Exception {
        CompletableFuture<JsonObject> f = new CompletableFuture<>();
        Minecraft.getInstance().execute(() -> {
            try { f.complete(s.get()); } catch (Exception e) { f.completeExceptionally(e); }
        });
        return f.get(5, TimeUnit.SECONDS);
    }

    private static JsonObject body(HttpExchange x) throws Exception {
        try (InputStream in = x.getRequestBody()) {
            byte[] bytes = in.readNBytes(2_000_000);
            String text = new String(bytes, StandardCharsets.UTF_8);
            return text.isBlank() ? new JsonObject() : JsonParser.parseString(text).getAsJsonObject();
        }
    }

    static JsonObject error(String text) {
        JsonObject o = new JsonObject();
        o.addProperty("error", text);
        return o;
    }

    private static void send(HttpExchange x, int code, JsonObject o) throws Exception {
        byte[] bytes = GSON.toJson(o).getBytes(StandardCharsets.UTF_8);
        x.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        x.sendResponseHeaders(code, bytes.length);
        x.getResponseBody().write(bytes);
    }
}
