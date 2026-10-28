package dev.claudechat;

import com.google.gson.JsonObject;
import com.mojang.blaze3d.platform.InputConstants;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Screenshot;
import net.minecraft.client.gui.components.AbstractButton;
import net.minecraft.client.gui.components.events.GuiEventListener;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.worldselection.CreateWorldScreen;
import net.minecraft.client.input.CharacterEvent;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.client.input.MouseButtonInfo;
import net.minecraft.network.chat.Component;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.function.BooleanSupplier;

/**
 * Automated test in a live world (gradlew runLiveTest, -Dclaudecraft.livetest=true).
 * Starts the real bridge, creates a test world, then drives the mod the way a
 * player does: chat commands, the K window, clicks on buttons by label, the file
 * window, the terminal, the editor (types and saves with Ctrl+S), settings.
 * Each step waits for an observable result (bridge events, screens, files on disk)
 * and takes a screenshot. The report goes to run/livetest/report.md; the client
 * exits when done. No Claude requests are made (no tokens are spent).
 */
final class LiveTest {
    static final boolean ENABLED = Boolean.getBoolean("claudecraft.livetest");

    private record Step(String name, Runnable action, BooleanSupplier done, int timeoutTicks, boolean shot) { }
    private record Result(String name, boolean ok, double seconds, String detail, String shot) { }

    private final ClaudeChat mod;
    private final Deque<Step> steps = new ArrayDeque<>();
    private final List<Result> results = new ArrayList<>();
    private Step current;
    private int ticks, waited, number;
    private long started;
    private boolean finished;
    private Path editFile;
    private TerminalScreen terminal;
    private final List<String> confirmations = new ArrayList<>();

    LiveTest(ClaudeChat mod) {
        this.mod = mod;
        if (ENABLED) plan();
    }

    private Minecraft mc() { return Minecraft.getInstance(); }
    private Screen screen() { return mc().gui.screen(); }

    private void step(String name, Runnable action, BooleanSupplier done, int timeoutSeconds) {
        steps.add(new Step(name, action, done, timeoutSeconds * 20, true));
    }

    private void quiet(String name, Runnable action, BooleanSupplier done, int timeoutSeconds) {
        steps.add(new Step(name, action, done, timeoutSeconds * 20, false));
    }

    private boolean event(String kind) {
        for (Deque<JsonObject> events : mod.log.values())
            for (JsonObject e : events)
                if (e.has("kind") && e.get("kind").getAsString().equals(kind) && e.get("seq").getAsLong() > mark) return true;
        return false;
    }

    private long mark;  // only events after the step started count

    private void markEvents() {
        mark = 0;
        for (Deque<JsonObject> events : mod.log.values())
            for (JsonObject e : events) if (e.has("seq")) mark = Math.max(mark, e.get("seq").getAsLong());
    }

    /** Presses the first button whose label contains `label` (like a mouse click). */
    private boolean press(String label) {
        Screen s = screen();
        if (s == null) return false;
        for (GuiEventListener child : s.children()) {
            if (child instanceof AbstractButton b && b.active && b.getMessage().getString().contains(label)) {
                b.onPress(new MouseButtonInfo(0, 0));
                return true;
            }
        }
        return false;
    }

    private void plan() {
        boolean windows = PortablePython.isWindows();
        quiet("bridge starts", () -> { }, () -> {
            var st = mod.launcher.state();
            return st == BridgeLauncher.State.RUNNING || st == BridgeLauncher.State.EXTERNAL;
        }, 600);
        quiet("game finished loading", () -> { },
            () -> screen() instanceof net.minecraft.client.gui.screens.TitleScreen && mc().gui.overlay() == null && waited > 40, 300);
        quiet("create a test world", () -> CreateWorldScreen.testWorld(mc(), () -> { }), () -> {
            if (screen() instanceof CreateWorldScreen) press(Component.translatable("selectWorld.create").getString());
            if (screen() instanceof net.minecraft.client.gui.screens.ConfirmScreen c && waited % 20 == 0) {
                confirmations.add(c.getTitle().getString());
                for (String key : new String[]{"gui.yes", "gui.proceed", "gui.continue", "selectWorld.create"})
                    if (press(Component.translatable(key).getString())) break;
            }
            return mc().player != null && mc().level != null && screen() == null;
        }, 240);
        step("world is loaded", () -> { }, () -> waited > 100, 20);
        step("/cc help prints the help", () -> mod.runLine("cc", "/cc help"), () -> waited > 10, 5);
        step("/cc pwd answers from the bridge", () -> { markEvents(); mod.runLine("cc", "/cc pwd"); }, () -> event("cwd"), 20);
        step("/ls lists files", () -> { markEvents(); mod.runLine("cc", "/ls"); }, () -> event("listing"), 20);
        step("/cc undo list (Claude's edits history)", () -> { markEvents(); mod.runLine("cc", "/cc undo list"); },
            () -> event("undo_list"), 20);
        step("/cc model shows the model picker", () -> { markEvents(); mod.runLine("cc", "/cc model"); }, () -> event("model"), 20);
        step("/cc commands lists slash commands", () -> { markEvents(); mod.runLine("cc", "/cc commands"); },
            () -> event("commands"), 20);
        step("/cc perms adds and shows a rule", () -> { markEvents(); mod.runLine("cc", "/cc perms ask Bash(rm:*)"); },
            () -> event("perms"), 20);
        step("/cc worktree lists worktrees", () -> { markEvents(); mod.runLine("cc", "/cc worktree"); },
            () -> event("worktrees"), 20);
        step("/cc budget shows limits", () -> { markEvents(); mod.runLine("cc", "/cc budget"); }, () -> event("budget"), 20);
        step("/claude sound sets and plays a custom sound", () -> {
            mod.runLine("c", "/claude sound done block.note_block.bell");
            mod.runLine("c", "/claude sound test error");
        }, () -> waited > 10 && Sounds.sound("done").equals("minecraft:block.note_block.bell"), 10);
        quiet("restore the default sound", () -> mod.runLine("c", "/claude sound done default"),
            () -> Sounds.sound("done").equals(Sounds.DEFAULTS.get("done")), 5);
        step("/cc # remembers, /cc memory shows CLAUDE.md", () -> {
            markEvents();
            mod.runLine("cc", "/cc # livetest note");
            mod.runLine("cc", "/cc memory");
        }, () -> event("memory"), 20);
        step("/cc skills lists skills", () -> { markEvents(); mod.runLine("cc", "/cc skills"); }, () -> event("skills"), 20);
        step("K window opens", () -> mc().setScreenAndShow(new ConversationScreen(mod)),
            () -> screen() instanceof ConversationScreen && waited > 20, 10);
        step("↑ in the K window recalls the last prompt", () -> {
            InputHistory.add("livetest: earlier prompt");
            mc().setScreenAndShow(new ConversationScreen(mod));
        }, () -> {
            if (!(screen() instanceof ConversationScreen k)) return false;
            if (waited == 20) { k.inputForTest(""); k.keyPressed(new KeyEvent(InputConstants.KEY_UP, 0, 0)); }
            return waited > 20 && k.inputValue().equals("livetest: earlier prompt");
        }, 10);
        step("Tab completes /rev to /review", () -> mod.refreshCommands("cc"), () -> {
            if (!(screen() instanceof ConversationScreen k)) return false;
            if (waited == 30) { k.inputForTest("/rev"); k.keyPressed(new KeyEvent(InputConstants.KEY_TAB, 0, 0)); }
            return waited > 30 && k.inputValue().equals("/review ");
        }, 10);
        step("Ctrl+Shift+P opens the palette, 'doct' finds Diagnostics", () -> {
            if (screen() instanceof ConversationScreen k) k.keyPressed(new KeyEvent(InputConstants.KEY_P, 0, 3));
        }, () -> {
            if (!(screen() instanceof PaletteScreen p)) return false;
            if (waited == 10) p.typeForTest("doct");
            return waited > 15 && p.firstForTest().contains("doctor");
        }, 10);
        step("Ctrl+P lists project files and opens one", () -> {
            mc().setScreenAndShow(new ConversationScreen(mod));
        }, () -> {
            if (waited == 5 && screen() instanceof ConversationScreen k) k.keyPressed(new KeyEvent(InputConstants.KEY_P, 0, 2));
            if (waited == 40 && screen() instanceof PaletteScreen p) { p.typeForTest("livelines"); }
            if (waited == 50 && screen() instanceof PaletteScreen p) p.keyPressed(new KeyEvent(InputConstants.KEY_RETURN, 0, 0));
            return waited > 50 && screen() instanceof EditorScreen ed && ed.isLoaded();
        }, 15);
        quiet("back to the K window", () -> mc().setScreenAndShow(new ConversationScreen(mod)),
            () -> screen() instanceof ConversationScreen && waited > 10, 5);
        step("click 'Files' in the K window", () -> press(L.t("Файлы")),
            () -> screen() instanceof FileScreen && waited > 30, 10);
        step("terminal runs a command", () -> {
            terminal = new TerminalScreen(mod, null, windows ? "cmd" : "sh", "");
            mc().setScreenAndShow(terminal);
        }, () -> {
            if (waited == 60) terminal.typeForTest("echo livetest-" + "ok\r");
            return waited > 60 && terminal.screenText().contains("livetest-ok") && waited > 80;
        }, 30);
        step("editor types and saves with Ctrl+S", () -> {
            try {
                editFile = net.fabricmc.loader.api.FabricLoader.getInstance().getConfigDir().resolve("claude-chat/workspace/livetest-edit.txt");
                Files.writeString(editFile, "first line\n");
            } catch (IOException e) { throw new RuntimeException(e); }
            mc().setScreenAndShow(new EditorScreen(mod, null, editFile.toString()));
        }, () -> {
            if (waited == 40) {
                EditorScreen editor = (EditorScreen) screen();
                editor.keyPressed(new KeyEvent(InputConstants.KEY_END, 0, 2));  // Ctrl+End
                editor.keyPressed(new KeyEvent(InputConstants.KEY_RETURN, 0, 0));
                for (char c : "typed in Minecraft".toCharArray()) editor.charTyped(new CharacterEvent(c));
                editor.keyPressed(new KeyEvent(InputConstants.KEY_S, 0, 2));
            }
            try {
                return waited > 60 && Files.readString(editFile).contains("typed in Minecraft");
            } catch (IOException e) { return false; }
        }, 20);
        step("/edit file:20 opens the editor on line 20", () -> {
            try {
                Path f = net.fabricmc.loader.api.FabricLoader.getInstance().getConfigDir().resolve("claude-chat/workspace/livetest-lines.txt");
                StringBuilder b = new StringBuilder();
                for (int i = 1; i <= 40; i++) b.append("line ").append(i).append('\n');
                Files.writeString(f, b.toString());
            } catch (IOException e) { throw new RuntimeException(e); }
            mod.runLine("cc", "/edit livetest-lines.txt:20");
        }, () -> screen() instanceof EditorScreen ed && ed.isLoaded() && ed.cursorRow() == 19 && waited > 20, 10);
        step("editor Ctrl+H replaces all matches", () -> { }, () -> {
            if (!(screen() instanceof EditorScreen ed) || !ed.isLoaded()) return false;
            if (waited == 10) ed.findForTest("line 1", "LINE ONE", true);
            return waited > 20 && ed.textForTest().contains("LINE ONE0") && !ed.textForTest().contains("line 1\n");
        }, 10);
        step("/cc replace previews and applies in the project", () -> {
            markEvents();
            mod.runLine("cc", "/cc replace --in livetest-lines.txt line 40 -> last line");
        }, () -> {
            if (waited == 10) {
                for (Deque<JsonObject> events : mod.log.values())
                    for (JsonObject e : events)
                        if (e.get("kind").getAsString().equals("replace_preview") && e.get("seq").getAsLong() > mark)
                            mod.runLine("cc", "/cc replace apply " + e.get("token").getAsString());
            }
            try {
                return waited > 10 && Files.readString(net.fabricmc.loader.api.FabricLoader.getInstance().getConfigDir()
                    .resolve("claude-chat/workspace/livetest-lines.txt")).contains("last line");
            } catch (IOException e) { return false; }
        }, 20);
        step("settings screen", () -> mc().setScreenAndShow(new SettingsScreen(mod, null)), () -> waited > 60, 10);
        quiet("close screens", () -> mc().setScreenAndShow(null), () -> waited > 5, 5);
    }

    void tick(Minecraft client) {
        if (!ENABLED || finished) return;
        ticks++;
        if (started == 0) started = System.currentTimeMillis();
        if (current == null) {
            current = steps.poll();
            if (current == null) { finish(client); return; }
            waited = 0;
            stepStart = System.currentTimeMillis();
            try { current.action().run(); }
            catch (Exception e) { record(false, "action failed: " + e); return; }
        }
        waited++;
        boolean done;
        try { done = current.done().getAsBoolean(); }
        catch (Exception e) { record(false, "check failed: " + e); return; }
        if (done) record(true, "");
        else if (waited > current.timeoutTicks()) record(false, "timed out after " + current.timeoutTicks() / 20
            + " s; screen: " + (screen() == null ? "none" : screen().getClass().getSimpleName()));
    }

    private long stepStart;

    private void record(boolean ok, String detail) {
        Step step = current;
        current = null;
        String shot = null;
        if (step.shot()) {
            number++;
            shot = String.format("livetest-%02d.png", number);
            String name = shot;
            Screenshot.grab(mc().gameDirectory, name, mc().gameRenderer.mainRenderTarget(), 1, c -> { });
        }
        if (!ok && step.name().equals("bridge starts")) detail += " (" + mod.launcher.state().text() + ": " + mod.launcher.detail() + ")";
        results.add(new Result(step.name(), ok, (System.currentTimeMillis() - stepStart) / 1000.0, detail, shot));
        if (!ok && (step.name().equals("bridge starts") || step.name().equals("create a test world"))) steps.clear();
    }

    private void finish(Minecraft client) {
        finished = true;
        long passed = results.stream().filter(Result::ok).count();
        StringBuilder md = new StringBuilder("# Claudecraft live test\n\n");
        md.append(passed).append(" / ").append(results.size()).append(" steps passed in ")
            .append((System.currentTimeMillis() - started) / 1000).append(" s.\n\n| # | Step | Result | Time | Screenshot | Details |\n|---|---|---|---|---|---|\n");
        int i = 1;
        for (Result r : results)
            md.append("| ").append(i++).append(" | ").append(r.name()).append(" | ").append(r.ok() ? "✔" : "✖ FAIL")
                .append(" | ").append(String.format("%.1f s", r.seconds())).append(" | ")
                .append(r.shot() == null ? "" : "screenshots/" + r.shot()).append(" | ").append(r.detail().replace("|", "/")).append(" |\n");
        if (!confirmations.isEmpty()) md.append("\nConfirmations answered: ").append(String.join(" · ", confirmations)).append("\n");
        try {
            Path out = client.gameDirectory.toPath().resolve("livetest/report.md");
            Files.createDirectories(out.getParent());
            Files.writeString(out, md.toString(), StandardCharsets.UTF_8);
            if (editFile != null) Files.deleteIfExists(editFile);
        } catch (IOException ignored) { }
        System.out.println("[claudecraft livetest] " + passed + "/" + results.size() + " passed");
        // the last screenshot is written asynchronously: give it a moment
        Thread stopper = new Thread(() -> {
            try { Thread.sleep(1500); } catch (InterruptedException ignored) { }
            client.execute(client::stop);
        });
        stopper.setDaemon(true);
        stopper.start();
    }
}
