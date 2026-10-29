package dev.overlaycraft;

import net.minecraft.client.Minecraft;
import net.minecraft.client.Screenshot;
import net.minecraft.client.gui.components.AbstractButton;
import net.minecraft.client.gui.components.events.GuiEventListener;
import net.minecraft.client.gui.screens.ChatScreen;
import net.minecraft.client.gui.screens.ConfirmScreen;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.TitleScreen;
import net.minecraft.client.gui.screens.inventory.InventoryScreen;
import net.minecraft.client.gui.screens.worldselection.CreateWorldScreen;
import net.minecraft.client.input.MouseButtonInfo;
import net.minecraft.network.chat.Component;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * gradlew :overlaycraft:runLiveTest — a test world: the cards while playing, in chat (drag,
 * fold), in the inventory (hidden, then "behind"). Report and screenshots in run/livetest.
 * Media is real (whatever plays on this PC); the order and tabs are samples.
 */
final class LiveTest {
    static final boolean ENABLED = Boolean.getBoolean("overlaycraft.livetest");
    private static int ticks, step, waited;
    private static final List<String> REPORT = new ArrayList<>();
    private static int passed, failed;
    private static double grabX, grabY, before, volume = -1;

    private LiveTest() { }

    private static Minecraft mc() { return Minecraft.getInstance(); }
    private static Screen screen() { return mc().gui.screen(); }

    private static boolean press(String label) {
        Screen s = screen();
        if (s == null) return false;
        for (GuiEventListener child : s.children())
            if (child instanceof AbstractButton b && b.active && b.getMessage().getString().contains(label)) {
                b.onPress(new MouseButtonInfo(0, 0));
                return true;
            }
        return false;
    }

    private static void check(String what, boolean ok) {
        REPORT.add((ok ? "- ✔ " : "- ✖ ") + what);
        if (ok) passed++; else failed++;
    }

    private static void shot(String name) {
        Screenshot.grab(mc(), true);
        REPORT.add("  - screenshot: " + name + " (run/screenshots, newest)");
    }

    static void tick(Minecraft client) {
        if (!ENABLED) return;
        ticks++;
        waited++;
        switch (step) {
            case 0 -> { client.options.pauseOnLostFocus = false; if (volume < 0) { volume = client.options.getSoundSourceOptionInstance(net.minecraft.sounds.SoundSource.MASTER).get(); client.options.getSoundSourceOptionInstance(net.minecraft.sounds.SoundSource.MASTER).set(0.0); } if (screen() instanceof TitleScreen && client.gui.overlay() == null && waited > 40) next(); }
            case 1 -> { CreateWorldScreen.testWorld(client, () -> { }); next(); }
            case 2 -> {
                if (screen() instanceof CreateWorldScreen) press(Component.translatable("selectWorld.create").getString());
                if (screen() instanceof ConfirmScreen && waited % 20 == 0)
                    for (String key : new String[]{"gui.yes", "gui.proceed", "gui.continue", "selectWorld.create"})
                        if (press(Component.translatable(key).getString())) break;
                if (client.player != null && client.level != null && screen() == null) next();
                if (waited > 2400) finish("world did not load");
            }
            case 3 -> {
                if (waited < 100) return;
                Browser.seen = System.currentTimeMillis() + 3_600_000L;
                Browser.tabs = Browser.parseTabs(com.google.gson.JsonParser.parseString(
                    "[{\"id\":1,\"window\":1,\"title\":\"Плейлист — SoundCloud\",\"url\":\"https://soundcloud.com/x\",\"audible\":false,\"active\":true,\"focused\":true},"
                    + "{\"id\":2,\"window\":2,\"title\":\"huevo.su\",\"url\":\"https://huevo.su/\",\"active\":true}]").getAsJsonArray());
                Control.run(com.google.gson.JsonParser.parseString("{\"action\":\"food\",\"place\":\"Шаурма №1\",\"status\":\"Курьер едет к вам\",\"minutes\":12}").getAsJsonObject());
                check("manual order from status gets step 2 (on the way)", Browser.order != null && Browser.order.step == 2);
                REPORT.add("- media sessions on this PC: " + Media.sessions.size() + (Media.error.isEmpty() ? "" : " (" + Media.error + ")"));
                next();
            }
            case 4 -> { if (waited == 20) { shot("ingame"); next(); } }
            case 5 -> { if (waited == 20) { client.setScreenAndShow(new ChatScreen("", false)); next(); } }
            case 6 -> {
                if (waited < 20) return;
                int[] r = Hud.cardRect("music");
                if (waited == 20) check("cards are clickable in chat", r != null);
                if (r == null) { next(); next(); return; }
                Hud.mouseX = r[0] + 30; Hud.mouseY = r[1] + 6;
                if (waited == 30) { shot("chat-hover"); next(); }
            }
            case 7 -> {
                // the real input path: the window's mouse events, as a person's hand makes them
                var mouse = client.mouseHandler;
                long window = client.getWindow().handle();
                double k = client.getWindow().getGuiScale();
                if (waited == 1) {
                    int[] r = Hud.cardRect("music");
                    grabX = r[0] + 30; grabY = r[1] + 6;
                    before = Config.widget("music").fx;
                    mouse.onMove(window, grabX * k, grabY * k, 0, 0);
                }
                if (waited == 3) mouse.onButton(window, new MouseButtonInfo(0, 0), 1);
                if (waited >= 4 && waited <= 14) {
                    double t = (waited - 4) / 10.0;
                    mouse.onMove(window, (grabX + (60 - grabX) * t) * k, (grabY + (50 - grabY) * t) * k, 0, 0);
                }
                if (waited == 16) mouse.onButton(window, new MouseButtonInfo(0, 0), 0);
                if (waited == 20) {
                    check("dragging the music card with the mouse moves it left (fx " + String.format("%.2f", before) + " → "
                        + String.format("%.2f", Config.widget("music").fx) + ")", Config.widget("music").fx < 0.5);
                    int[] t = Hud.cardRect("tabs");
                    if (t != null) mouse.onMove(window, (t[0] + 40) * k, (t[1] + 4) * k, 0, 0);
                }
                if (waited == 22) mouse.onButton(window, new MouseButtonInfo(1, 0), 1);
                if (waited == 23) mouse.onButton(window, new MouseButtonInfo(1, 0), 0);
                if (waited == 26) {
                    check("right click folds the tabs card", Config.widget("tabs").compact);
                    check("the chat stayed open", screen() instanceof ChatScreen);
                    next();
                }
            }
            case 8 -> {
                if (waited == 20) shot("chat-dragged");
                if (waited == 25) {
                    int[] r = Hud.cardRect("music");
                    check("music card is now at the left (x=" + (r == null ? "?" : r[0]) + ")", r != null && r[0] < 100);
                    client.setScreenAndShow(new InventoryScreen(client.player));
                    next();
                }
            }
            case 9 -> {
                if (waited == 20) shot("inventory-hidden");
                if (waited == 22) { Config.screens = "behind"; }
                if (waited == 40) { shot("inventory-behind"); Config.screens = "hide"; client.setScreenAndShow(null); next(); }
            }
            case 10 -> {
                if (waited == 20) {
                    // put the layout back
                    Config.WIDGETS.clear();
                    Config.WIDGETS.putAll(Config.defaults());
                    Config.save();
                    finish(null);
                }
            }
            default -> { }
        }
    }

    private static void next() { step++; waited = 0; }

    private static void finish(String error) {
        if (volume >= 0) { mc().options.getSoundSourceOptionInstance(net.minecraft.sounds.SoundSource.MASTER).set(volume); mc().options.save(); }  // the test ran muted
        if (error != null) check(error, false);
        REPORT.add(0, "# Overlaycraft live test: " + passed + " passed, " + failed + " failed\n");
        try {
            Path out = mc().gameDirectory.toPath().resolve("livetest/overlay-report.md");
            Files.createDirectories(out.getParent());
            Files.writeString(out, String.join("\n", REPORT) + "\n", StandardCharsets.UTF_8);
        } catch (Exception ignored) { }
        mc().stop();
    }
}
