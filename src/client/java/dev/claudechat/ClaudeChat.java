package dev.claudechat;

import com.google.gson.*;
import com.mojang.blaze3d.platform.InputConstants;
import com.mojang.brigadier.arguments.StringArgumentType;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.command.v2.ClientCommandRegistrationCallback;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientLifecycleEvents;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.keymapping.v1.KeyMappingHelper;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.ClickEvent;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.Identifier;
import java.net.URI;
import java.net.http.*;
import java.nio.file.*;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Supplier;
import static net.fabricmc.fabric.api.client.command.v2.ClientCommands.*;

/** UI only: all SDK credentials, agent processes and conversation scheduling live in the local bridge. */
public final class ClaudeChat implements ClientModInitializer {
    final ExecutorService io = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "claude-bridge-http"); t.setDaemon(true); return t;
    });
    private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
    private final AtomicBoolean polling = new AtomicBoolean();
    /** First words sent to the bridge as actions; anything else is a question (q). */
    static final Set<String> ACTIONS = Set.of(
        "q", "queue", "f", "force", "c", "chats", "chat", "cancel", "restart", "new", "select", "rename",
        "status", "clearqueue", "cq", "reply", "pending", "local", "reload", "tools", "capabilities",
        "tokens", "usage", "auto", "model", "effort",
        // file manager (does not move Claude)
        "ls", "dir", "cd", "pwd", "cat", "type", "tree", "find", "mkdir", "touch", "mv", "cp", "rm",
        "open", "explorer", "code", "edit",
        // projects, git, agents, tasks, connectors
        "project", "projects", "p", "git", "g", "agents", "agent", "spawn", "run", "tasks", "ps", "jobs",
        "stop", "connectors", "mcp", "connect", "disconnect", "detach", "key", "crash",
        // shell and PC sync
        "sh", "shell", "$", "sync", "terminal", "pc", "resume", "term");
    /** Top-level commands → /cc action (so `/ls`, `/mkdir x`, `/ps Get-Process`, `/git push` work). */
    static final Map<String, String> SHORTCUTS = new LinkedHashMap<>();
    static {
        for (String a : new String[]{"ls", "dir", "cd", "pwd", "cat", "tree", "find", "mkdir", "touch", "mv", "cp",
            "rm", "open", "code", "git"}) SHORTCUTS.put(a, a);
        SHORTCUTS.put("edit", "edit");
        SHORTCUTS.put("nano", "edit");
        SHORTCUTS.put("sh", "sh auto");
        SHORTCUTS.put("ps", "sh ps");
        SHORTCUTS.put("powershell", "sh ps");
        SHORTCUTS.put("cmd", "sh cmd");
        SHORTCUTS.put("bash", "sh bash");
        SHORTCUTS.put("kill", "sh kill");
        SHORTCUTS.put("project", "project");
        SHORTCUTS.put("files", "files");
    }
    private final Set<String> needsText = Set.of("q", "queue", "f", "force", "spawn", "run", "cat", "type",
        "find", "mkdir", "touch", "mv", "cp", "rm", "connect", "stop", "disconnect", "detach");
    private Path connection;
    final BridgeLauncher launcher = new BridgeLauncher(this::bridgeAnswers);
    private volatile Supplier<Screen> openNext;
    private final DevShots devShots = new DevShots(this);
    private volatile long cursor;
    // Bridge instance id: when it changes the bridge restarted and its event numbers reset.
    private volatile String boot = "";
    private long nextPoll;
    private long lastFailure;
    private volatile long epoch;
    /** Recent events per chat, for the conversation screen (game chat keeps its own copy). */
    final Map<String, Deque<JsonObject>> log = new HashMap<>();
    /** Last "need_key" fields per connector, for the key entry screen. */
    final Map<String, JsonArray> keyFields = new HashMap<>();
    volatile String notice = "";
    private KeyMapping openKey;

    private static ClaudeChat instance;

    /** The running mod (for entrypoints created by other mods, e.g. Mod Menu). */
    static ClaudeChat instance() { return instance; }

    @Override public void onInitializeClient() {
        instance = this;
        connection =FabricLoader.getInstance().getConfigDir().resolve("claude-chat/connection.json");
        if (launcher.autoStart() && !DevShots.ENABLED) launcher.start();
        ClientLifecycleEvents.CLIENT_STOPPING.register(client -> launcher.stop());
        Notify.register();
        KeyMapping.Category category = KeyMapping.Category.register(Identifier.fromNamespaceAndPath("claude_chat", "main"));
        openKey = KeyMappingHelper.registerKeyMapping(new KeyMapping("key.claude_chat.open",
            InputConstants.KEY_K, category));
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
                .executes(c -> { open(() -> new SettingsScreen(this, null)); return 1; })
                .then(argument("input", StringArgumentType.greedyString()).executes(c -> {
                    claudeCommand(StringArgumentType.getString(c, "input")); return 1;
                })));
            // Terminal-style shortcuts without /cc: /ls, /cd, /mkdir, /sh, /ps, /cmd, /bash, /git …
            for (Map.Entry<String, String> shortcut : SHORTCUTS.entrySet()) {
                String target = shortcut.getValue();
                dispatcher.register(literal(shortcut.getKey()).requires(source -> source.attended())
                    .executes(c -> { dispatch("cc", target); return 1; })
                    .then(argument("input", StringArgumentType.greedyString()).executes(c -> {
                        dispatch("cc", target + " " + StringArgumentType.getString(c, "input")); return 1;
                    })));
            }
        });
        ClientTickEvents.END_CLIENT_TICK.register(client -> {
            devShots.tick(client);
            while (openKey.consumeClick()) open(() -> new ConversationScreen(this));
            Supplier<Screen> next = openNext;
            if (next != null) {
                // Deferred: the chat screen closes right after a command runs.
                openNext = null;
                client.setScreenAndShow(next.get());
            }
            long now = System.currentTimeMillis();
            boolean active = client.player != null || ConversationScreen.isOpen();
            if (!active || now < nextPoll || !polling.compareAndSet(false, true)) return;
            nextPoll = now + 400;
            long ticket = epoch;
            io.execute(() -> {
                try {
                    String sentBoot = boot;
                    JsonObject response = request("/events?after=" + cursor + "&boot="
                        + java.net.URLEncoder.encode(sentBoot, java.nio.charset.StandardCharsets.UTF_8), null);
                    long nextCursor = response.get("cursor").getAsLong();
                    String nextBoot = response.has("boot") ? response.get("boot").getAsString() : "";
                    client.execute(() -> {
                        if (ticket != epoch) return;
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
                try { request("/command", command("c", "leave", "")); }
                catch (Exception ignored) { }
            });
        });
    }

    void open(Supplier<Screen> screen) { openNext = screen; }

    /** /claude [settings|ui|key <connector>]. */
    private void claudeCommand(String input) {
        String[] parts = input.strip().split("\\s+", 2);
        switch (parts[0]) {
            case "ui", "window", "chat" -> open(() -> new ConversationScreen(this));
            case "key" -> {
                String id = parts.length > 1 ? parts[1].strip() : "";
                open(() -> new KeyScreen(this, id, keyFields.getOrDefault(id, new JsonArray())));
            }
            case "hud", "toasts", "sound" -> {
                // /claude hud|toasts|sound [on|off]: toggles without an argument.
                String value = parts.length > 1 ? parts[1].strip() : "";
                boolean on = value.isEmpty() ? !launcher.flag(parts[0], true) : !value.equals("off");
                launcher.setFlag(parts[0], on);
                say("c", parts[0] + ": " + (on ? L.t("вкл") : L.t("выкл")));
            }
            default -> open(() -> new SettingsScreen(this, null));
        }
    }

    /** Runs a line typed in the conversation screen: "/cc git", "/claude", or plain text for `mode`. */
    void runLine(String mode, String line) {
        String text = line.strip();
        if (text.isEmpty()) return;
        if (text.startsWith("$")) { dispatch("cc", "sh " + text.substring(1).strip()); return; }  // $ <shell command>
        if (text.startsWith("/")) {
            String[] parts = text.substring(1).split("\\s+", 2);
            String rest = parts.length > 1 ? parts[1] : "";
            switch (parts[0]) {
                case "c", "cc" -> { if (rest.isBlank()) help(parts[0]); else dispatch(parts[0], rest); }
                case "claude" -> { if (rest.isBlank()) open(() -> new SettingsScreen(this, null)); else claudeCommand(rest); }
                default -> {
                    String target = SHORTCUTS.get(parts[0]);
                    if (target != null) dispatch("cc", rest.isBlank() ? target : target + " " + rest);
                    else notice = L.t("Неизвестная команда: /") + parts[0];
                }
            }
            return;
        }
        dispatch(mode, text);
    }

    void dispatch(String mode, String input) {
        String[] split = input.strip().split("\\s+", 2);
        String action = split[0];
        String text = split.length == 2 ? split[1] : "";
        if (action.equals("help")) { help(mode); return; }
        if (action.equals("settings")) { open(() -> new SettingsScreen(this, null)); return; }
        if (action.equals("ui") || action.equals("window")) { open(() -> new ConversationScreen(this)); return; }
        if (action.equals("files") || action.equals("fm")) { open(() -> new FileScreen(this, null)); return; }
        if (Set.of("edit", "nano", "vim").contains(action)) {
            if (text.isBlank()) { say(mode, L.t("Формат: /edit <файл>")); return; }
            String file = text.strip().replaceAll("^\"|\"$", "");
            open(() -> new EditorScreen(this, null, file));
            return;
        }
        if (Set.of("project", "projects", "p").contains(action) && text.strip().equals("pick")) {
            FolderPicker.pick(this);  // system folder dialog; the bridge gets the chosen path
            return;
        }
        if (!ACTIONS.contains(action)) { action = "q"; text = input; }
        JsonObject data = command(mode, action, text);
        if (action.equals("reply")) {
            String[] answer = text.split("\\s+", 2);
            if (answer.length != 2) { say(mode, L.t("Формат: /") + mode + L.t(" reply <id> <ответ>")); return; }
            data.addProperty("request", answer[0]);
            data.addProperty("text", answer[1]);
        }
        if (needsText.contains(action) && text.isBlank()) {
            say(mode, L.t("Нужен аргумент: /") + mode + " " + action + " …"); return;
        }
        send(mode, data);
    }

    /** Explains why the bridge does not answer, based on the launcher state. */
    private String bridgeProblem() {
        return switch (launcher.state()) {
            case INSTALLING, STARTING -> L.t("Мост запускается: ") + launcher.detail();
            case FAILED -> L.t("Мост не запустился: ") + launcher.detail() + " — /c settings";
            case OFF, STOPPED -> L.t("Мост выключен. Включи автозапуск или перезапусти в /c settings.");
            default -> L.t("Мост не отвечает. /c settings — перезапуск и лог.");
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

    void send(String mode, JsonObject data) {
        long ticket = epoch;
        io.execute(() -> {
            try { request("/command", data); }
            catch (Exception e) {
                Minecraft.getInstance().execute(() -> {
                    if (ticket == epoch) say(mode, L.t("Ошибка: ") + e.getMessage());
                });
            }
        });
    }

    JsonObject request(String path, JsonObject body) throws Exception {
        if (!Files.isRegularFile(connection)) throw new IllegalStateException(L.t("Мост ещё не запущен (нет connection.json)"));
        JsonObject config = JsonParser.parseString(Files.readString(connection)).getAsJsonObject();
        int port = config.get("port").getAsInt();
        if (port < 1024 || port > 65535) throw new IllegalArgumentException(L.t("Некорректный порт моста"));
        // The host is never taken from configuration or a server chat message.
        HttpRequest.Builder builder = HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port + path))
            .timeout(Duration.ofSeconds(15)).header("Authorization", "Bearer " + config.get("token").getAsString());
        if (body == null) builder.GET();
        else builder.header("Content-Type", "application/json").POST(HttpRequest.BodyPublishers.ofString(body.toString()));
        HttpResponse<String> response = http.send(builder.build(), HttpResponse.BodyHandlers.ofString());
        if (response.body().length() > 4000000) throw new IllegalStateException(L.t("Слишком большой ответ моста"));
        JsonObject result = JsonParser.parseString(response.body()).getAsJsonObject();
        if (response.statusCode() != 200) throw new IllegalStateException(
            result.has("error") ? result.get("error").getAsString() : "HTTP " + response.statusCode());
        return result;
    }

    private void render(JsonObject event) {
        String kind = event.has("kind") ? event.get("kind").getAsString() : "";
        String chat = event.has("chat") ? event.get("chat").getAsString() : "";
        if (kind.equals("need_key") && event.has("connector"))
            keyFields.put(event.get("connector").getAsString(), event.getAsJsonArray("fields"));
        if (!kind.equals("history")) Notify.onEvent(event);
        if (kind.equals("history")) {
            // Conversation screen asked for older events of this chat after a reconnect.
            Deque<JsonObject> items = log.computeIfAbsent(chat, k -> new ArrayDeque<>());
            Set<Long> known = new HashSet<>();
            for (JsonObject e : items) known.add(e.get("seq").getAsLong());
            List<JsonObject> merged = new ArrayList<>();
            for (JsonElement item : event.getAsJsonArray("items"))
                if (!known.contains(item.getAsJsonObject().get("seq").getAsLong())) merged.add(item.getAsJsonObject());
            merged.addAll(items);
            merged.sort(Comparator.comparingLong(e -> e.get("seq").getAsLong()));
            items.clear();
            items.addAll(merged.subList(Math.max(0, merged.size() - 500), merged.size()));
            return;
        }
        Deque<JsonObject> items = log.computeIfAbsent(chat, k -> new ArrayDeque<>());
        items.addLast(event);
        while (items.size() > 500) items.removeFirst();
        ConversationScreen.changed();
        for (Component line : ChatStyle.render(event)) show(line);
    }

    /** Local (non-bridge) notice in the same style as bridge status lines. */
    private void say(String mode, String text) {
        notice = text;
        ConversationScreen.changed();
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
            .withStyle(s -> s.withBold(true)).append(ChatStyle.lit(L.t("   мост: ") + launcher.state().text()
                + L.t(" · окно переписки: K"), ChatStyle.DIM))));
        String[][] rows = {
            {c + L.t("<запрос>"), L.t("в очередь (q, queue) · f/force — дописать в работу · cq — очистить очередь")},
            {c + "c", L.t("чаты · new · rename · status · cancel · restart · tokens · auto")},
            {c + "model", L.t("модель и усилие для этого чата (opus, sonnet, haiku · low…max)")},
            {"/claude hud", L.t("индикатор задач на экране · /claude toasts · /claude sound — вкл/выкл")},
            {"/cc project", L.t("проекты = рабочая папка Claude (open, here, new, add)")},
            {c + "ls", L.t("файлы: cd · pwd · cat · tree · find · mkdir · mv · cp · rm · open · code")},
            {"/cc git", L.t("статус · diff · commit · push · pull · branch · log · clone · github")},
            {"/cc agents", L.t("агенты · agent <имя> <задача> · spawn — параллельно · tasks")},
            {"/cc connectors", L.t("GitHub, Playwright, Context7, БД, Sentry, Notion…")},
            {"/cc crash", L.t("разобрать последний крэш / latest.log Minecraft")},
            {"/claude", L.t("настройки, вход в аккаунт, API-ключ")},
        };
        for (String[] row : rows)
            show(ChatStyle.line(mode, Component.literal("  ").append(Component.literal(row[0]).withStyle(s -> s
                .withColor(ChatStyle.AQUA).withClickEvent(new ClickEvent.SuggestCommand(row[0].split(" [<·|]")[0] + " "))))
                .append(ChatStyle.lit("  " + row[1], ChatStyle.DIM))));
        show(ChatStyle.line(mode, Component.literal("  ")
            .append(ChatStyle.button(L.t("▣ Окно (K)"), c + "ui", false, ChatStyle.CODE_MODE, L.t("Отдельный экран переписки"))).append(" ")
            .append(ChatStyle.button(L.t("⚙ Настройки"), c + "settings", false, ChatStyle.GOLD, null)).append(" ")
            .append(ChatStyle.button(L.t("✦ Проекты"), "/cc project", false, ChatStyle.CODE_MODE, null)).append(" ")
            .append(ChatStyle.button("⇄ Git", "/cc git", false, ChatStyle.GREEN, null)).append(" ")
            .append(ChatStyle.button(L.t("▶ Задачи"), "/cc tasks", false, ChatStyle.AQUA, null)).append(" ")
            .append(ChatStyle.button(L.t("📁 Файлы"), c + "ls", false, ChatStyle.AQUA, null))));
    }
}
