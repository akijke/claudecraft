package dev.claudechat;

import com.google.gson.*;
import com.mojang.brigadier.arguments.StringArgumentType;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.command.v2.ClientCommandRegistrationCallback;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientLifecycleEvents;
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
    final ExecutorService io = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "claude-bridge-http"); t.setDaemon(true); return t;
    });
    private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
    private final AtomicBoolean polling = new AtomicBoolean();
    // Short aliases are resolved by the bridge: queue=q, force=f, c/chats=chat.
    private final Set<String> actions = Set.of("q", "queue", "f", "force", "c", "chats", "chat",
        "cancel", "restart", "new", "select", "rename", "status", "clearqueue", "reply", "pending",
        "local", "reload", "tools", "capabilities", "tokens", "usage", "auto",
        "ls", "dir", "cd", "pwd", "cat", "type");
    private final Set<String> needsText = Set.of("q", "queue", "f", "force");
    // "settings" is handled in the mod itself (opens SettingsScreen), never sent to the bridge.
    private Path connection;
    final BridgeLauncher launcher = new BridgeLauncher(this::bridgeAnswers);
    private volatile boolean openSettings;
    private final DevShots devShots = new DevShots(this);
    private volatile long cursor;
    // Bridge instance id: when it changes the bridge restarted and its event numbers reset.
    private volatile String boot = "";
    private long nextPoll;
    private long lastFailure;
    private volatile long epoch;

    @Override public void onInitializeClient() {
        connection = FabricLoader.getInstance().getConfigDir().resolve("claude-chat/connection.json");
        if (launcher.autoStart() && !DevShots.ENABLED) launcher.start();
        ClientLifecycleEvents.CLIENT_STOPPING.register(client -> launcher.stop());
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
            dispatcher.register(literal("claude").requires(source -> source.attended())
                .executes(c -> { openSettings = true; return 1; }));
        });
        ClientTickEvents.END_CLIENT_TICK.register(client -> {
            devShots.tick(client);
            if (openSettings) {
                // Deferred: the chat screen closes right after a command runs.
                openSettings = false;
                client.setScreenAndShow(new SettingsScreen(this, null));
            }
            long now = System.currentTimeMillis();
            if (client.player == null || now < nextPoll || !polling.compareAndSet(false, true)) return;
            nextPoll = now + 500;
            long ticket = epoch;
            io.execute(() -> {
                try {
                    String sentBoot = boot;
                    JsonObject response = request("/events?after=" + cursor + "&boot="
                        + java.net.URLEncoder.encode(sentBoot, java.nio.charset.StandardCharsets.UTF_8), null);
                    long nextCursor = response.get("cursor").getAsLong();
                    String nextBoot = response.has("boot") ? response.get("boot").getAsString() : "";
                    client.execute(() -> {
                        if (ticket != epoch || client.player == null) return;
                        for (JsonElement row : response.getAsJsonArray("events")) render(row.getAsJsonObject());
                        cursor = nextCursor;
                        boot = nextBoot;
                    });
                } catch (Exception e) {
                    if (now - lastFailure > 30000) {
                        lastFailure = now;
                        client.execute(() -> {
                            if (ticket == epoch) say("c", bridgeProblem());
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
        if (action.equals("settings")) { openSettings = true; return; }
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

    /** Explains why the bridge does not answer, based on the launcher state. */
    private String bridgeProblem() {
        return switch (launcher.state()) {
            case INSTALLING, STARTING -> "Мост запускается: " + launcher.detail();
            case FAILED -> "Мост не запустился: " + launcher.detail() + " — /c settings";
            case OFF, STOPPED -> "Мост выключен. Включи автозапуск или перезапусти в /c settings.";
            default -> "Мост не отвечает. /c settings — перезапуск и лог.";
        };
    }

    boolean bridgeAnswers() {
        try { request("/events?boot=", null); return true; }
        catch (Exception e) { return false; }
    }

    JsonObject command(String mode, String action, String text) {
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
    JsonObject request(String path, JsonObject body) throws Exception {
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
        for (Component line : ChatStyle.render(event)) show(line);
    }

    /** Local (non-bridge) notice in the same style as bridge status lines. */
    private void say(String mode, String text) {
        for (String line : ChatStyle.clean(text).split("\\R")) {
            if (!line.isBlank()) show(ChatStyle.line(mode, ChatStyle.lit("· " + line, ChatStyle.DIM)));
        }
    }

    private void show(Component text) {
        Minecraft client = Minecraft.getInstance();
        // Client-only system line: never sent to the server.
        if (client.player != null) client.gui.chatListener().handleSystemMessage(text, false);
    }

    private void help(String mode) {
        String c = "/" + mode + " ";
        int accent = mode.equals("cc") ? ChatStyle.CODE_MODE : ChatStyle.CLAUDE;
        show(ChatStyle.line(mode, ChatStyle.lit(mode.equals("cc") ? "✦ Claude Code" : "✦ Claude", accent)
            .withStyle(s -> s.withBold(true)).append(ChatStyle.lit("   мост: " + launcher.state().label, ChatStyle.DIM))));
        String[][] rows = {
            {c + "<запрос>", "в очередь (то же: q, queue)"},
            {c + "f <текст>", "дописать Claude, пока он работает (force)"},
            {c + "c", "чаты · new · rename · status · cancel · restart"},
            {c + "auto off|edits|all", "авторазрешение действий"},
            {c + "tokens", "расход токенов"},
            {c + "ls · cd · pwd · cat", "файлы и рабочая папка"},
            {"/claude", "настройки, вход в аккаунт, API-ключ"},
        };
        for (String[] row : rows)
            show(ChatStyle.line(mode, Component.literal("  ").append(Component.literal(row[0]).withStyle(s -> s
                .withColor(ChatStyle.AQUA).withClickEvent(new ClickEvent.SuggestCommand(row[0].split(" [<·|]")[0] + " "))))
                .append(ChatStyle.lit("  " + row[1], ChatStyle.DIM))));
        show(ChatStyle.line(mode, Component.literal("  ")
            .append(ChatStyle.button("⚙ Настройки", c + "settings", false, ChatStyle.GOLD, null)).append(" ")
            .append(ChatStyle.button("☰ Чаты", c + "chats", false, ChatStyle.AQUA, null)).append(" ")
            .append(ChatStyle.button("Σ Токены", c + "tokens", false, ChatStyle.AQUA, null)).append(" ")
            .append(ChatStyle.button("⚡ Auto", c + "auto", false, ChatStyle.AQUA, null)).append(" ")
            .append(ChatStyle.button("📁 ls", c + "ls", false, ChatStyle.AQUA, null))));
    }
}
