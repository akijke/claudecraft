package dev.overlaycraft;

import com.google.gson.JsonParser;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Screenshot;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.Component;

/**
 * gradlew :overlaycraft:runShots — draws the cards with sample data on a preview screen
 * (as in game, then as in chat with the mouse over the music card), saves screenshots to
 * run/screenshots and quits. Starts no helper, server or browser.
 */
final class DevShots {
    static final boolean ENABLED = Boolean.getBoolean("overlaycraft.devshots");
    private static int ticks;
    private static boolean chat;

    private DevShots() { }

    /** The game's menu background with the cards over it. */
    private static final class Preview extends Screen {
        Preview() { super(Component.literal("Overlaycraft preview")); }

        @Override public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float delta) {
            super.extractRenderState(g, mouseX, mouseY, delta);
            Hud.draw(g, chat);
        }
    }

    static void tick(Minecraft client) {
        if (!ENABLED) return;
        switch (++ticks) {
            case 80 -> { sample(); client.setScreenAndShow(new Preview()); }
            case 120 -> Screenshot.grab(client, true);
            case 140 -> {
                chat = true;
                Hud.mouseX = client.getWindow().getGuiScaledWidth() - 60;
                Hud.mouseY = (int) (0.25 * (client.getWindow().getGuiScaledHeight() - 48)) + 20;
            }
            case 160 -> Screenshot.grab(client, true);
            case 180 -> client.stop();
            default -> { }
        }
    }

    private static void sample() {
        Media.sessions = Media.parse(JsonParser.parseString("["
            + "{\"id\":\"OperaSoftware.OperaGXWebBrowser.1\",\"title\":\"Ирак (POCKET061)\",\"artist\":\"Sanya\",\"status\":\"Playing\",\"pos\":41,\"dur\":190.8,\"current\":true},"
            + "{\"id\":\"TelegramMessengerLLP.TelegramDesktop\",\"title\":\"voice.ogg\",\"artist\":\"Telegram\",\"status\":\"Paused\",\"pos\":3,\"dur\":20}]").getAsJsonArray());
        Browser.seen = System.currentTimeMillis() + 3_600_000L;
        Browser.tabs = Browser.parseTabs(JsonParser.parseString("["
            + "{\"id\":1,\"window\":1,\"title\":\"Ирак (POCKET061) — SoundCloud\",\"url\":\"https://soundcloud.com/sanya/irak\",\"audible\":true,\"active\":false},"
            + "{\"id\":2,\"window\":1,\"title\":\"Отслеживание заказа — Яндекс Еда\",\"url\":\"https://eda.yandex.ru/tracking\",\"active\":true,\"focused\":true},"
            + "{\"id\":3,\"window\":2,\"title\":\"huevo.su — плейлист\",\"url\":\"https://huevo.su/\",\"active\":true}]").getAsJsonArray());
        Browser.Order o = new Browser.Order();
        o.place = "Шаурма №1";
        o.status = "Курьер едет к вам";
        o.step = 2;
        o.etaAt = System.currentTimeMillis() + 12 * 60_000L;
        o.url = "https://eda.yandex.ru/tracking";
        Browser.order = o;
    }
}
