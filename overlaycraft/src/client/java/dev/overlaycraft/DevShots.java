package dev.overlaycraft;

import com.google.gson.JsonParser;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Screenshot;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.Component;
import java.util.List;

/**
 * gradlew :overlaycraft:runShots — draws the cards with sample data on a preview screen in
 * every state they have (in game, in chat, folded, each order stage, "log in", "connect the
 * browser", faded under another screen), saves one screenshot per state to run/screenshots
 * (in this order, names in run/screenshots/shots.txt) and quits. Starts no helper, server
 * or browser; the overlay settings are not saved.
 */
final class DevShots {
    static final boolean ENABLED = Boolean.getBoolean("overlaycraft.devshots");
    private static int ticks;
    private static boolean chat, faded;

    private DevShots() { }

    /** The game's menu background with the cards over it. */
    private static final class Preview extends Screen {
        Preview() { super(Component.literal("Overlaycraft preview")); }

        @Override public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float delta) {
            super.extractRenderState(g, mouseX, mouseY, delta);
            Hud.draw(g, chat, faded);
        }
    }

    private record State(String name, Runnable setup) { }

    private static final List<State> STATES = List.of(
        new State("01-ingame", () -> { }),
        new State("02-chat-hover", () -> { chat = true; hover("music"); }),
        new State("03-compact", () -> { chat = false; for (var w : Config.WIDGETS.values()) w.compact = true; }),
        new State("04-order-accepted", () -> { unfold(); order(0, "Заказ принят", 35); }),
        new State("05-order-cooking", () -> order(1, "Ресторан готовит заказ", 24)),
        new State("06-order-delivered", () -> order(3, "Заказ доставлен", -1)),
        new State("07-eda-login", () -> { Browser.order = null; Browser.edaState = "login"; chat = true; hover("none"); }),
        new State("08-not-connected", () -> { Browser.seen = 0; Browser.tabs = List.of(); }),
        new State("09-behind-screen", () -> { sample(); chat = false; faded = true; }));

    static void tick(Minecraft client) {
        if (!ENABLED) return;
        ticks++;
        if (ticks == 80) {
            Config.WIDGETS.clear();
            Config.WIDGETS.putAll(Config.defaults());
            sample();
            client.setScreenAndShow(new Preview());
            log("");
        }
        // one state every 30 ticks: set it up, give it 20 ticks to draw, grab
        int i = (ticks - 100) / 30, phase = (ticks - 100) % 30;
        if (ticks < 100) return;
        if (i >= STATES.size()) { if (phase == 0) client.stop(); return; }
        if (phase == 0) STATES.get(i).setup.run();
        if (phase == 20) { Screenshot.grab(client, true); log(STATES.get(i).name + "\n"); }
    }

    private static void log(String line) {
        try {
            var file = Minecraft.getInstance().gameDirectory.toPath().resolve("screenshots/shots.txt");
            java.nio.file.Files.createDirectories(file.getParent());
            if (line.isEmpty()) java.nio.file.Files.writeString(file, "");
            else java.nio.file.Files.writeString(file, line, java.nio.file.StandardOpenOption.APPEND);
        } catch (Exception ignored) { }
    }

    private static void hover(String card) {
        var w = Minecraft.getInstance().getWindow();
        if (card.equals("music")) {
            Hud.mouseX = w.getGuiScaledWidth() - 60;
            Hud.mouseY = (int) (0.25 * (w.getGuiScaledHeight() - 48)) + 20;
        } else { Hud.mouseX = -100; Hud.mouseY = -100; }
    }

    private static void unfold() { for (var w : Config.WIDGETS.values()) w.compact = false; }

    private static void order(int step, String status, int minutes) {
        Browser.Order o = new Browser.Order();
        o.place = "Шаурма №1";
        o.status = status;
        o.step = step;
        o.etaAt = minutes < 0 ? 0 : System.currentTimeMillis() + minutes * 60_000L;
        o.url = "https://eda.yandex.ru/tracking";
        Browser.order = o;
    }

    private static void sample() {
        Media.sessions = Media.parse(JsonParser.parseString("["
            + "{\"id\":\"OperaSoftware.OperaGXWebBrowser.1\",\"title\":\"Ирак (POCKET061)\",\"artist\":\"Sanya\",\"status\":\"Playing\",\"pos\":41,\"dur\":190.8,\"current\":true},"
            + "{\"id\":\"TelegramMessengerLLP.TelegramDesktop\",\"title\":\"voice.ogg\",\"artist\":\"Telegram\",\"status\":\"Paused\",\"pos\":3,\"dur\":20}]").getAsJsonArray());
        Browser.seen = System.currentTimeMillis() + 3_600_000L;
        Browser.edaState = "ok";
        Browser.tabs = Browser.parseTabs(JsonParser.parseString("["
            + "{\"id\":1,\"window\":1,\"title\":\"Ирак (POCKET061) — SoundCloud\",\"url\":\"https://soundcloud.com/sanya/irak\",\"audible\":true,\"active\":false},"
            + "{\"id\":2,\"window\":1,\"title\":\"Отслеживание заказа — Яндекс Еда\",\"url\":\"https://eda.yandex.ru/tracking\",\"active\":true,\"focused\":true},"
            + "{\"id\":3,\"window\":2,\"title\":\"huevo.su — плейлист\",\"url\":\"https://huevo.su/\",\"active\":true}]").getAsJsonArray());
        order(2, "Курьер едет к вам", 12);
    }
}
