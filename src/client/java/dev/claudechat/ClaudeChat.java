package dev.claudechat;

import com.google.gson.*;
import com.mojang.brigadier.arguments.StringArgumentType;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.command.v2.ClientCommandRegistrationCallback;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.Minecraft;
import net.minecraft.network.chat.ClickEvent;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.MutableComponent;
import net.minecraft.ChatFormatting;
import java.net.URI;
import java.net.http.*;
import java.nio.file.*;
import java.time.Duration;
import java.util.Set;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import static net.fabricmc.fabric.api.client.command.v2.ClientCommands.*;

/** UI only: all SDK credentials, agent processes and conversation scheduling live in the local bridge. */
public final class ClaudeChat implements ClientModInitializer {
    private final ExecutorService io = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "claude-bridge-http"); t.setDaemon(true); return t;
    });
    private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
    private final AtomicBoolean polling = new AtomicBoolean();
    // Short aliases are resolved by the bridge: queue=q, force=f, c/chats=chat.
    private final Set<String> actions = Set.of("q", "queue", "f", "force", "c", "chats", "chat",
        "cancel", "restart", "new", "select", "rename", "status", "clearqueue", "reply", "pending",
        "local", "reload", "tools", "capabilities");
    private final Set<String> needsText = Set.of("q", "queue", "f", "force");
    private Path connection;
    private volatile long cursor;
    private long nextPoll;
    private long lastFailure;
    private volatile long epoch;

    @Override public void onInitializeClient() {
        connection = FabricLoader.getInstance().getConfigDir().resolve("claude-chat/connection.json");
        ClientCommandRegistrationCallback.EVENT.register((dispatcher, context) -> {
            for (String mode : new String[]{"c", "cc"}) {
                dispatcher.register(literal(mode)
                    // Blocks invocations by untrusted server-provided clickable messages.
                    .requires(source -> source.attended())
                    .executes(c -> { help(mode); return 1; })
                    .then(argument("input", StringArgumentType.greedyString()).executes(c -> {
                        dispatch(mode, StringArgumentType.getString(c, "input")); return 1;
                    })));
            }
        });
        ClientTickEvents.END_CLIENT_TICK.register(client -> {
            long now = System.currentTimeMillis();
            if (client.player == null || now < nextPoll || !polling.compareAndSet(false, true)) return;
            nextPoll = now + 500;
            long ticket = epoch;
            io.execute(() -> {
                try {
                    JsonObject response = request("/events?after=" + cursor, null);
                    long nextCursor = response.get("cursor").getAsLong();
                    client.execute(() -> {
                        if (ticket != epoch || client.player == null) return;
                        for (JsonElement row : response.getAsJsonArray("events")) render(row.getAsJsonObject());
                        cursor = nextCursor;
                    });
                } catch (Exception e) {
                    if (now - lastFailure > 30000) {
                        lastFailure = now;
                        client.execute(() -> {
                            if (ticket == epoch) say("c", "Мост не подключён. Запусти bridge.py; /c help — инструкция.");
                        });
                    }
                } finally { polling.set(false); }
            });
        });
        ClientPlayConnectionEvents.DISCONNECT.register((handler, client) -> {
            epoch++;
            io.execute(() -> {
                try { request("/command", command("c", "disconnect", "")); }
                catch (Exception ignored) { }
            });
        });
    }

    private void dispatch(String mode, String input) {
        String[] split = input.strip().split("\\s+", 2);
        String action = split[0];
        String text = split.length == 2 ? split[1] : "";
        if (action.equals("help")) { help(mode); return; }
        if (!actions.contains(action)) { action = "q"; text = input; }
        JsonObject data = command(mode, action, text);
        if (action.equals("reply")) {
            String[] answer = text.split("\\s+", 2);
            if (answer.length != 2) { say(mode, "Формат: /" + mode + " reply <id> <ответ>"); return; }
            data.addProperty("request", answer[0]);
            data.addProperty("text", answer[1]);
        }
        if (needsText.contains(action) && text.isBlank()) {
            say(mode, "Введи запрос после /" + mode + " " + action); return;
        }
        send(mode, data);
    }

    private JsonObject command(String mode, String action, String text) {
        JsonObject data = new JsonObject();
        data.addProperty("mode", mode); data.addProperty("action", action); data.addProperty("text", text);
        return data;
    }
    private void send(String mode, JsonObject data) {
        long ticket = epoch;
        io.execute(() -> {
            try { request("/command", data); }
            catch (Exception e) {
                Minecraft.getInstance().execute(() -> {
                    if (ticket == epoch) say(mode, "Ошибка моста: " + e.getMessage());
                });
            }
        });
    }
    private JsonObject request(String path, JsonObject body) throws Exception {
        if (!Files.isRegularFile(connection)) throw new IllegalStateException("Нет connection.json; запусти bridge.py --game-dir <папка игры>");
        JsonObject config = JsonParser.parseString(Files.readString(connection)).getAsJsonObject();
        int port = config.get("port").getAsInt();
        if (port < 1024 || port > 65535) throw new IllegalArgumentException("Некорректный порт моста");
        // The host is never taken from configuration or a server chat message.
        HttpRequest.Builder builder = HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port + path))
            .timeout(Duration.ofSeconds(15)).header("Authorization", "Bearer " + config.get("token").getAsString());
        if (body == null) builder.GET();
        else builder.header("Content-Type", "application/json").POST(HttpRequest.BodyPublishers.ofString(body.toString()));
        HttpResponse<String> response = http.send(builder.build(), HttpResponse.BodyHandlers.ofString());
        if (response.body().length() > 4000000) throw new IllegalStateException("Слишком большой ответ моста");
        JsonObject result = JsonParser.parseString(response.body()).getAsJsonObject();
        if (response.statusCode() != 200) throw new IllegalStateException(
            result.has("error") ? result.get("error").getAsString() : "HTTP " + response.statusCode());
        return result;
    }

    private void render(JsonObject event) {
        String mode = event.get("mode").getAsString();
        String id = event.get("chat").getAsString();
        String tag = mode + ":" + id.substring(0, Math.min(6, id.length()));
        say(tag, event.get("text").getAsString());
        String kind = event.get("kind").getAsString();
        if (kind.equals("chats")) {
            for (JsonElement item : event.getAsJsonArray("rows")) {
                JsonObject row = item.getAsJsonObject();
                show(Component.literal(row.get("selected").getAsBoolean() ? "→ " : "  ")
                    .append(button(row.get("label").getAsString(), "/" + mode + " select " + row.get("id").getAsString(), false)));
            }
            MutableComponent buttons = button("+ Локальный", "/" + mode + " new local", false);
            if (mode.equals("c")) buttons.append(" ").append(button("+ API", "/c new api", false));
            else buttons.append(" ").append(button("Импорт CLI", "/cc local", false));
            show(buttons);
        } else if (kind.equals("permission") || kind.equals("question")) {
            String prefix = "/" + mode + " reply " + event.get("request").getAsString() + " ";
            JsonArray choices = event.getAsJsonArray("choices");
            for (int i = 0; i < choices.size(); i++) {
                // For multi-select, suggest numbers rather than submitting the first click.
                boolean multiple = event.has("multiple") && event.get("multiple").getAsBoolean();
                show(button((i + 1) + ". " + choices.get(i).getAsString(), prefix + (i + 1), multiple));
            }
            if (kind.equals("question")) {
                show(button("Свой ответ", prefix, true));
                if (event.has("multiple") && event.get("multiple").getAsBoolean())
                    say(tag, "Для нескольких вариантов: " + prefix + "1,3");
            }
        } else if (kind.equals("url") && event.has("url")) {
            try {
                URI uri = URI.create(event.get("url").getAsString());
                if (("https".equals(uri.getScheme()) || "http".equals(uri.getScheme())) && uri.getHost() != null) {
                    show(Component.literal("[Открыть браузер] " + uri).withStyle(style -> style
                        .withColor(ChatFormatting.AQUA).withUnderlined(true)
                        .withClickEvent(new ClickEvent.OpenUrl(uri))));
                }
            } catch (IllegalArgumentException ignored) { }
        }
    }
    private MutableComponent button(String label, String command, boolean suggest) {
        return Component.literal("[" + clean(label) + "]").withStyle(style -> style
            .withColor(ChatFormatting.AQUA).withUnderlined(true).withClickEvent(suggest
                ? new ClickEvent.SuggestCommand(command) : new ClickEvent.RunCommand(command)));
    }
    private String clean(String text) {
        return text.replaceAll("\\u001B\\[[;\\d]*[ -/]*[@-~]", "").replace("§", "")
            .replaceAll("[\\p{Cntrl}&&[^\\n\\t]]", "");
    }
    private void say(String mode, String text) {
        for (String line : clean(text).split("\\R")) {
            if (!line.isBlank()) show(Component.literal("[" + mode + "] " + line));
        }
    }
    private void show(Component text) {
        Minecraft client = Minecraft.getInstance();
        // Client-only system line: never sent to the server.
        if (client.player != null) client.gui.chatListener().handleSystemMessage(text, false);
    }
    private void help(String mode) {
        say(mode, "/" + mode + " <запрос> или q <запрос> — в очередь, после текущей работы.");
        say(mode, "f <запрос> (force) — дописать Claude, пока он работает, без отмены задачи."
            + (mode.equals("c") ? " В API-чатах f недоступно во время ответа." : ""));
        say(mode, "c | chats — выбор чата | new local" + (mode.equals("c") ? " | new api" : " | local (импорт CLI)")
            + " | rename <имя> | cancel | restart | status | clearqueue | pending | reload | tools");
        say(mode, "restart перезапускает запрос/процесс SDK с сохранением истории; new начинает новый диалог.");
        say(mode, "Мост: Python 3.11+, pip install -r bridge/requirements.txt; python bridge/bridge.py --game-dir <папка игры>");
        show(button("Чаты", "/" + mode + " chats", false).append(" ")
            .append(button("Статус", "/" + mode + " status", false)).append(" ")
            .append(button("Ожидающие вопросы", "/" + mode + " pending", false)));
    }
}
