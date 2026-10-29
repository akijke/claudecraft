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
        "tokens", "usage", "auto", "model", "effort", "undo", "compact", "context", "doctor", "commands", "slash", "perms", "permissions", "plan", "replace", "worktree", "wt", "budget", "palette", "goto", "rewind", "fork", "ide", "export", "search", "open_session", "dashboard", "spend", "pr", "prs", "issues", "issue", "watch", "crossreview", "xreview", "memory", "mem", "#", "remember", "skills", "hooks", "bg", "background", "style", "img", "image", "pty",
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
        SHORTCUTS.put("pty", "pty");
        SHORTCUTS.put("palette", "palette");
        SHORTCUTS.put("ide", "ide");
        SHORTCUTS.put("goto", "goto");
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
    // /cc shot: hide the GUI for a couple of frames, grab the frame, send it to Claude.
    private int shotTicks;
    private boolean paletteHeld;
    private String shotMode, shotQuestion;
    private boolean shotHidGui, shotReopen;
    private final DevShots devShots = new DevShots(this);
    private final LiveTest liveTest = new LiveTest(this);
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
    private final Map<String, KeyMapping> extraKeys = new LinkedHashMap<>();

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
        // More windows, unbound by default: set them in Options → Controls → Key Binds → Claudecraft
        String[] names = {"files", "terminal", "ide", "palette", "goto", "screenshot", "settings"};
        for (String name : names)
            extraKeys.put(name, KeyMappingHelper.registerKeyMapping(new KeyMapping("key.claude_chat." + name,
                InputConstants.UNKNOWN.getValue(), category)));
        ClientCommandRegistrationCallback.EVENT.register((dispatcher, context) -> {
            for (String mode : new String[]{"c", "cc"}) {
                dispatcher.register(literal(mode)
                    // Blocks invocations by untrusted server-provided clickable messages.
                    .requires(source -> source.attended())
                    .executes(c -> { help(mode); return 1; })
                    .then(argument("input", StringArgumentType.greedyString()).suggests((c, builder) -> {
                        // Tab: actions, and slash commands after "/" (built-in, ~/.claude/commands, project)
                        String typed = builder.getRemaining();
                        int at = typed.lastIndexOf(' ') + 1;
                        if (typed.startsWith("@", at)) {  // @path: suggest project files
                            refreshFiles();
                            var sub = builder.createOffset(builder.getStart() + at);
                            for (String f : mentionCandidates(typed.substring(at + 1), 20)) sub.suggest("@" + f);
                            return sub.buildFuture();
                        }
                        if (typed.contains(" ")) return builder.buildFuture();
                        if (typed.startsWith("/")) {
                            refreshCommands(mode);
                            for (String[] cmd : slashCommands)
                                if (("/" + cmd[0]).startsWith(typed))
                                    builder.suggest("/" + cmd[0], Component.literal(cmd[2]));
                        } else {
                            for (String a : new TreeSet<>(ACTIONS)) if (a.startsWith(typed)) builder.suggest(a);
                        }
                        return builder.buildFuture();
                    }).executes(c -> {
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
        Theme.load();
        ClientTickEvents.END_CLIENT_TICK.register(client -> {
            devShots.tick(client);
            liveTest.tick(client);
            if (shotTicks > 0 && --shotTicks == 0) takeShot(client);
            Theme.tick(client);
            // Ctrl+Shift+P in the game (no screen open): the command palette
            boolean palette = client.gui.screen() == null && client.player != null
                && InputConstants.isKeyDown(InputConstants.KEY_P) && InputConstants.isKeyDown(InputConstants.KEY_LCONTROL)
                && InputConstants.isKeyDown(InputConstants.KEY_LSHIFT);
            if (palette && !paletteHeld) open(() -> new PaletteScreen(this, null, false));
            paletteHeld = palette;
            while (openKey.consumeClick()) open(() -> new ConversationScreen(this));
            for (var entry : extraKeys.entrySet()) {
                while (entry.getValue().consumeClick()) {
                    switch (entry.getKey()) {
                        case "files" -> open(() -> new FileScreen(this, null));
                        case "terminal" -> open(() -> TerminalScreen.reopen(this, null));
                        case "ide" -> open(() -> new IdeScreen(this, null));
                        case "palette" -> { refreshCommands("cc"); open(() -> new PaletteScreen(this, null, false)); }
                        case "goto" -> open(() -> new PaletteScreen(this, null, true));
                        case "screenshot" -> screenshot("cc", "");
                        case "settings" -> open(() -> new SettingsScreen(this, null));
                        default -> { }
                    }
                }
            }
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

    /** Project files (relative paths) for @-mentions; refreshed in the background. */
    static volatile List<String> projectFiles = List.of();
    private volatile long filesFetched;

    void refreshFiles() {
        long now = System.currentTimeMillis();
        if (now - filesFetched < 3_000) return;
        filesFetched = now;
        io.execute(() -> {
            try {
                JsonObject r = request("/index?mode=cc", null);
                List<String> list = new ArrayList<>();
                for (JsonElement e : r.getAsJsonArray("files")) list.add(e.getAsString());
                projectFiles = list;
            } catch (Exception ignored) { }
        });
    }

    /** Best project files for "@query" (fuzzy, like Ctrl+P). */
    static List<String> mentionCandidates(String query, int limit) {
        String q = query.toLowerCase(Locale.ROOT);
        return projectFiles.stream().map(f -> new Object[]{f, PaletteScreen.score(f, q)})
            .filter(o -> (int) o[1] > 0).sorted((a, b) -> Integer.compare((int) b[1], (int) a[1]))
            .limit(limit).map(o -> (String) o[0]).toList();
    }

    /** name, argument hint, description of every slash command; refreshed in the background. */
    static volatile List<String[]> slashCommands = List.of();
    private volatile long commandsFetched;

    void refreshCommands(String mode) {
        long now = System.currentTimeMillis();
        if (now - commandsFetched < 10_000) return;
        commandsFetched = now;
        io.execute(() -> {
            try {
                JsonObject r = request("/commands?mode=" + mode, null);
                List<String[]> list = new ArrayList<>();
                for (JsonElement e : r.getAsJsonArray("commands")) {
                    JsonObject c = e.getAsJsonObject();
                    list.add(new String[]{c.get("name").getAsString(), c.get("hint").getAsString(),
                        c.get("description").getAsString(), c.get("source").getAsString()});
                }
                slashCommands = list;
            } catch (Exception ignored) { }
        });
    }

    /** Game screenshot for Claude (without HUD and menus); the question goes with it. */
    void screenshot(String mode, String question) {
        Minecraft client = Minecraft.getInstance();
        shotMode = mode;
        shotQuestion = question == null ? "" : question.strip();
        shotReopen = client.gui.screen() instanceof ConversationScreen;
        if (client.gui.screen() != null) client.setScreenAndShow(null);
        shotHidGui = !client.gui.hud.isHidden();
        if (shotHidGui) client.gui.hud.toggle();
        shotTicks = 4;  // the chat/K screen closes and a clean frame is drawn first
    }

    private void takeShot(Minecraft client) {
        var target = client.gameRenderer.mainRenderTarget();
        int factor = Math.max(1, (int) Math.ceil(Math.max(target.width / 1600.0, target.height / 1600.0)));
        String mode = shotMode, question = shotQuestion;
        boolean reopen = shotReopen;
        net.minecraft.client.Screenshot.takeScreenshot(target, factor, image -> {
            client.execute(() -> {
                if (shotHidGui && client.gui.hud.isHidden()) client.gui.hud.toggle();
                if (reopen) client.setScreenAndShow(new ConversationScreen(this));
            });
            Path file = FabricLoader.getInstance().getConfigDir().resolve("claude-chat/shots/shot-"
                + new java.text.SimpleDateFormat("yyyyMMdd-HHmmss").format(new Date()) + ".png");
            io.execute(() -> {
                try (image) {
                    Files.createDirectories(file.getParent());
                    image.writeToFile(file);
                    keepNewestShots(file.getParent(), 30);
                    send(mode, command(mode, "img", "\"" + file + "\" " + question));
                } catch (Exception e) {
                    Minecraft.getInstance().execute(() -> say(mode, L.t("Ошибка: ") + e.getMessage()));
                }
            });
        });
    }

    private static void keepNewestShots(Path folder, int keep) throws java.io.IOException {
        try (var files = Files.list(folder)) {
            List<Path> shots = files.filter(f -> f.getFileName().toString().startsWith("shot-"))
                .sorted(Comparator.reverseOrder()).toList();
            for (Path old : shots.subList(Math.min(keep, shots.size()), shots.size())) Files.deleteIfExists(old);
        }
    }

    /** /claude [settings|ui|key <connector>]. */
    private void claudeCommand(String input) {
        String[] parts = input.strip().split("\\s+", 2);
        switch (parts[0]) {
            case "ui", "window", "chat" -> open(() -> new ConversationScreen(this));
            case "key" -> {
                String id = parts.length > 1 ? parts[1].strip() : "";
                open(() -> new KeyScreen(this, id, keyFields.getOrDefault(id, new JsonArray())));
            }
            case "sound", "sounds" -> {
                // /claude sound [on|off] · sound done|ask|error <id|off|default> · sound volume 0.5 · sound test [kind]
                String[] args = parts.length > 1 ? parts[1].strip().split("\\s+", 2) : new String[0];
                if (args.length == 0 || args[0].equals("on") || args[0].equals("off")) {
                    boolean on = args.length == 0 ? !launcher.flag("sound", true) : args[0].equals("on");
                    launcher.setFlag("sound", on);
                    say("c", "sound: " + (on ? L.t("вкл") : L.t("выкл")));
                    soundMenu();
                } else if (args[0].equals("test")) {
                    Sounds.play(args.length > 1 ? args[1] : "done");
                } else if (args[0].equals("list")) {
                    soundMenu();
                } else if (Sounds.DEFAULTS.containsKey(args[0]) || args[0].equals("volume")) {
                    if (args.length < 2) { soundMenu(); return; }
                    try {
                        Sounds.set(args[0], args[1].strip());
                        if (!args[0].equals("volume")) Sounds.play(args[0]);
                        soundMenu();
                    } catch (RuntimeException e) { say("c", L.t("Неверный звук: ") + args[1]); }
                } else say("c", L.t("Формат: /claude sound [on|off|test|done|ask|error|volume] …"));
            }
            case "theme" -> {
                String name = parts.length > 1 ? parts[1].strip() : "";
                if (!name.isEmpty()) Theme.set(name);
                themeMenu();
            }
            case "scale" -> {
                String v = parts.length > 1 ? parts[1].strip() : "";
                if (!v.isEmpty()) Theme.setScale(v.equals("auto") ? 0 : Integer.parseInt(v.replaceAll("\\D", "0")));
                themeMenu();
            }
            case "osnotify" -> {
                String value = parts.length > 1 ? parts[1].strip() : "";
                boolean on = value.isEmpty() ? !launcher.flag("osnotify", true) : !value.equals("off");
                launcher.setFlag("osnotify", on);
                say("c", "osnotify: " + (on ? L.t("вкл") : L.t("выкл")) + L.t(" — системные уведомления, когда игра свёрнута"));
                if (value.equals("test")) OsNotify.show("Claudecraft", L.t("Проверка уведомления"));
            }
            case "hud", "toasts" -> {
                // /claude hud|toasts|sound [on|off]: toggles without an argument.
                String value = parts.length > 1 ? parts[1].strip() : "";
                boolean on = value.isEmpty() ? !launcher.flag(parts[0], true) : !value.equals("off");
                launcher.setFlag(parts[0], on);
                say("c", parts[0] + ": " + (on ? L.t("вкл") : L.t("выкл")));
            }
            default -> open(() -> new SettingsScreen(this, null));
        }
    }

    /** Theme and window-scale choices. */
    private void themeMenu() {
        int c = ChatStyle.AQUA;
        var row = ChatStyle.lit("◑ " + L.t("Тема") + "  ", ChatStyle.GOLD).withStyle(st -> st.withBold(true));
        for (String name : Theme.NAMES)
            row.append(ChatStyle.button((name.equals(Theme.current()) ? "● " : "") + name, "/claude theme " + name, false,
                name.equals(Theme.current()) ? ChatStyle.GREEN : c, null)).append(" ");
        show(ChatStyle.line("c", row));
        int scale = Theme.scaleSetting();
        var scales = ChatStyle.lit("  " + L.t("Масштаб окон мода") + "  ", ChatStyle.TEXT);
        for (int i = 0; i <= 4; i++) {
            String label = i == 0 ? "auto" : String.valueOf(i);
            scales.append(ChatStyle.button((i == scale ? "● " : "") + label, "/claude scale " + label, false,
                i == scale ? ChatStyle.GREEN : c, i == 0 ? L.t("Как в настройках игры") : null)).append(" ");
        }
        show(ChatStyle.line("c", scales));
        show(ChatStyle.line("c", ChatStyle.lit(L.t("  терминал: Ctrl+= / Ctrl+- — размер шрифта"), ChatStyle.FAINT)));
    }

    /** Sound settings with clickable choices. */
    private void soundMenu() {
        int c = ChatStyle.AQUA;
        show(ChatStyle.line("c", ChatStyle.lit("♪ " + L.t("Звуки"), ChatStyle.GOLD).withStyle(s -> s.withBold(true))
            .append(ChatStyle.lit("  " + (launcher.flag("sound", true) ? L.t("вкл") : L.t("выкл")) + "  ", ChatStyle.DIM))
            .append(ChatStyle.button(launcher.flag("sound", true) ? L.t("выключить") : L.t("включить"),
                "/claude sound " + (launcher.flag("sound", true) ? "off" : "on"), false, c, null))
            .append(ChatStyle.lit(String.format(java.util.Locale.ROOT, L.t("  громкость %.0f%% "), Sounds.volume() * 100), ChatStyle.DIM))
            .append(ChatStyle.button("−", "/claude sound volume " + Math.max(0, Sounds.volume() - 0.2f), false, c, null))
            .append(ChatStyle.button("+", "/claude sound volume " + Math.min(1, Sounds.volume() + 0.2f), false, c, null))));
        for (String kind : Sounds.DEFAULTS.keySet()) {
            String label = kind.equals("done") ? L.t("готово") : kind.equals("ask") ? L.t("вопрос") : L.t("ошибка");
            Component row = ChatStyle.line("c", ChatStyle.lit(String.format("  %-9s ", label), ChatStyle.TEXT)
                .append(ChatStyle.lit(Sounds.sound(kind).replace("minecraft:", "") + "  ", ChatStyle.DIM))
                .append(ChatStyle.button("▶", "/claude sound test " + kind, false, ChatStyle.GREEN, L.t("Послушать")))
                .append(ChatStyle.lit(" ", ChatStyle.DIM))
                .append(ChatStyle.button(L.t("другой"), "/claude sound " + kind + " ", true, c,
                    L.t("Любой id звука, напр. ") + String.join(", ", java.util.Arrays.copyOf(Sounds.SUGGESTED, 4))
                        + L.t("…; свои — через ресурспак")))
                .append(ChatStyle.lit(" ", ChatStyle.DIM))
                .append(ChatStyle.button(L.t("выкл"), "/claude sound " + kind + " off", false, ChatStyle.DIM, null))
                .append(ChatStyle.lit(" ", ChatStyle.DIM))
                .append(ChatStyle.button(L.t("сброс"), "/claude sound " + kind + " default", false, ChatStyle.DIM, null)));
            show(row);
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
                    else dispatch(mode, "/" + text.substring(1));  // slash command (/review, project commands …)
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
        if (action.startsWith("/") && action.length() > 1) { send(mode, command(mode, "slash", input.strip().substring(1))); return; }
        if (action.equals("settings")) { open(() -> new SettingsScreen(this, null)); return; }
        if (action.equals("ui") || action.equals("window")) { open(() -> new ConversationScreen(this)); return; }
        if (action.equals("files") || action.equals("fm")) { open(() -> new FileScreen(this, null)); return; }
        if (action.equals("shot") || action.equals("screenshot")) { screenshot(mode, text); return; }
        if (action.equals("dashboard") || action.equals("spend")) { open(() -> new DashboardScreen(this, null)); return; }
        if (action.equals("ide")) {
            String file = text.isBlank() ? null : text.strip();
            open(() -> new IdeScreen(this, file));
            return;
        }
        if (action.equals("palette") || action.equals("goto")) {
            refreshCommands(mode);
            boolean goTo = action.equals("goto");
            open(() -> new PaletteScreen(this, null, goTo));
            return;
        }
        if (action.equals("pty")) {  // real terminal window: /pty, /pty new [shell], /pty <shell>
            String arg = text.strip();
            if (arg.isEmpty()) open(() -> TerminalScreen.reopen(this, null));
            else open(() -> new TerminalScreen(this, null, arg.startsWith("new") ? arg.substring(3).strip() : arg, ""));
            return;
        }
        if (Set.of("edit", "nano", "vim").contains(action)) {
            if (text.isBlank()) { say(mode, L.t("Формат: /edit <файл>")); return; }
            String[] target = PathLinks.splitLine(text.strip().replaceAll("^\"|\"$", ""));
            int line = target[1] == null ? 0 : Integer.parseInt(target[1]);
            open(() -> new EditorScreen(this, null, target[0]).at(line));
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
        if (kind.equals("open_editor") && event.has("path")) {  // the bridge asks to open a file in the editor
            String path = event.get("path").getAsString();
            open(() -> new EditorScreen(this, null, path));
            return;
        }
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
            {c + "compact", L.t("[фокус] — сжать историю, когда контекст заполнен · context — сколько занято")},
            {c + "model", L.t("модель и усилие для этого чата (opus, sonnet, haiku · low…max)")},
            {c + "/review", L.t("slash-команды: /review, /init, /test… и свои из .claude/commands (Tab — подсказки) · commands — список")},
            {c + "perms", L.t("правила разрешений allow/deny/ask: perms allow Bash(git *) · perms rm <правило>")},
            {c + "plan", L.t("<задача> — сначала план (только чтение), потом «Принять / Править / Отклонить»")},
            {c + "replace", L.t("[-r] [-c] [--in *.java] <что> -> <на что> — замена по проекту с превью · в редакторе Ctrl+F / Ctrl+H")},
            {"/cc worktree", L.t("worktrees параллельных задач: merge · diff · drop (spawn --here — без worktree)")},
            {c + "dashboard", L.t("графики расходов по дням, проектам и моделям")},
            {c + "pr", L.t("pull requests: список, CI, ревью · pr create — Claude пишет описание · pr reply <N> — ответ на ревью")},
            {c + "issues", L.t("issues GitHub · issue <N> — прочитать · issue do <N> — Claude: ветка, работа, PR «Closes #N»")},
            {c + "watch", L.t("<команда> — при изменениях файлов запускать тесты/сборку, при падении — кнопка «Claude, почини» · watch stop")},
            {c + "crossreview", L.t("[фокус] — ревью изменений другим ИИ: Codexcraft ↔ Claudecraft (или агент code-reviewer)")},
            {c + "budget", L.t("лимиты $ / токенов: budget task $0.50 · chat 2M · day $5 · resume")},
            {"/ide", L.t("[файл] — чат | редактор | терминал в одном окне (Ctrl+1/2/3, Ctrl+P — файл, разделители тянутся)")},
            {"/palette", L.t("все команды с поиском (Ctrl+Shift+P) · /goto — быстрый переход к файлу (Ctrl+P в окнах мода)")},
            {c + "rewind", L.t("[номер] — откатить разговор и файлы к любому прошлому запросу")},
            {c + "memory", L.t("CLAUDE.md проекта и пользователя: открыть, вкл/выкл · /cc # <заметка> — дописать в CLAUDE.md")},
            {c + "skills", L.t("скиллы .claude/skills проекта и ~/.claude/skills: вкл/выкл, открыть SKILL.md")},
            {c + "hooks", L.t("хуки из settings.json: список, add <Событие> [matcher] -- <команда>, rm, log")},
            {c + "style", L.t("стиль ответов на проект (concise, explanatory, learning…) · style append <текст> — добавка к промпту")},
            {c + "fork", L.t("[номер] — новый чат-ветка с этого места разговора (исходный не меняется)")},
            {c + "export", L.t("[md|html] — сохранить чат в claude-exports/ проекта")},
            {c + "search", L.t("<текст> — поиск по всем чатам и сессиям проекта (и начатым на ПК)")},
            {"/cc doctor", L.t("проверить Python, git, gh, node, CLI, вход, порты и права — с советами")},
            {"/cc undo", L.t("откатить файлы, изменённые Claude в последнем запросе · undo list — история")},
            {c + "shot", L.t("[вопрос] — скриншот игры для Claude · img <файл> [вопрос] — любая картинка")},
            {"/pty", L.t("[ps|cmd|bash|pwsh|wsl] — настоящий терминал (vim, htop, REPL) · /pty new — ещё один")},
            {"/edit", L.t("<файл> — встроенный редактор с подсветкой (Ctrl+S сохранить)")},
            {"/claude theme", L.t("тема dark | light | contrast · /claude scale auto|1..4 — масштаб окон мода")},
            {"/claude osnotify", L.t("уведомления Windows и мигание в панели задач, когда игра свёрнута — вкл/выкл")},
            {"/claude sound", L.t("звуки: готово / вопрос / ошибка — свой звук на каждое, громкость, проверка")},
            {"/claude hud",L.t("индикатор задач на экране · /claude toasts · /claude sound — вкл/выкл")},
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
