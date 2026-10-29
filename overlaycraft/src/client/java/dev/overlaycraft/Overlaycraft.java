package dev.overlaycraft;

import com.google.gson.JsonObject;
import com.mojang.brigadier.arguments.StringArgumentType;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.command.v2.ClientCommandRegistrationCallback;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientLifecycleEvents;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.minecraft.client.Minecraft;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.Style;
import net.minecraft.network.chat.TextColor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import java.util.List;
import static net.fabricmc.fabric.api.client.command.v2.ClientCommands.argument;
import static net.fabricmc.fabric.api.client.command.v2.ClientCommands.literal;

/**
 * Overlaycraft: a translucent HUD over the game with what plays (YouTube, SoundCloud, VK,
 * huevo.su, Telegram...), the Yandex Eda order and the browser's tabs. Shown while playing
 * and in chat (where the cards are dragged and clicked), hidden in other screens.
 * Controlled with /ov, with the mouse in chat, and by Claude (claude-extension/).
 */
public final class Overlaycraft implements ClientModInitializer {
    static final Logger LOG = LoggerFactory.getLogger("overlaycraft");
    private static final List<String> WIDGET_NAMES = List.of("music", "food", "tabs");

    @Override public void onInitializeClient() {
        Config.load();
        Hud.register();
        if (!DevShots.ENABLED) {
            Server.start();
            Installer.unpackBrowserExtension();
            Installer.installClaudeExtension();
        }
        ClientTickEvents.END_CLIENT_TICK.register(client -> { Palette.tick(); Media.tick(); DevShots.tick(client); LiveTest.tick(client); });
        ClientLifecycleEvents.CLIENT_STOPPING.register(client -> { Media.stop(); Server.stop(); });
        ClientCommandRegistrationCallback.EVENT.register((dispatcher, context) -> dispatcher.register(literal("ov")
            .requires(source -> source.attended())
            .executes(c -> { help(); return 1; })
            .then(argument("input", StringArgumentType.greedyString()).suggests((c, b) -> {
                String typed = b.getRemaining();
                for (String s : List.of("on", "off", "show", "hide", "compact", "screens hide", "screens behind", "opacity",
                        "reset", "toggle", "play", "pause", "next", "prev", "food", "food clear", "ext", "claude", "status"))
                    if (s.startsWith(typed)) b.suggest(s);
                for (String w : WIDGET_NAMES)
                    for (String verb : List.of("show ", "hide ", "compact "))
                        if ((verb + w).startsWith(typed) && typed.startsWith(verb)) b.suggest(verb + w);
                return b.buildFuture();
            }).executes(c -> { command(StringArgumentType.getString(c, "input").trim()); return 1; }))));
    }

    private static void command(String input) {
        String[] parts = input.split("\\s+", 2);
        String verb = parts[0], rest = parts.length > 1 ? parts[1].trim() : "";
        JsonObject c = new JsonObject();
        switch (verb) {
            case "on", "off" -> {
                c.addProperty("action", "overlay");
                c.addProperty("enabled", verb.equals("on"));
                run(c, verb.equals("on") ? "Оверлей включён" : "Оверлей выключен (/ov on — вернуть)");
            }
            case "show", "hide", "compact" -> {
                if (rest.equals("all") && verb.equals("show")) {
                    for (String w : WIDGET_NAMES) Config.widget(w).visible = true;
                    Config.save();
                    say("Показаны все карточки");
                    return;
                }
                if (!WIDGET_NAMES.contains(rest)) { say("Карточки: music, food, tabs (или show all)"); return; }
                c.addProperty("action", "widget");
                c.addProperty("name", rest);
                if (verb.equals("compact")) c.addProperty("compact", !Config.widget(rest).compact);
                else c.addProperty("visible", verb.equals("show"));
                run(c, "Готово");
            }
            case "screens" -> {
                if (!rest.equals("hide") && !rest.equals("behind")) {
                    say("/ov screens hide — в инвентаре и меню не видно; behind — остаётся под размытием экрана. Сейчас: " + Config.screens);
                    return;
                }
                c.addProperty("action", "overlay");
                c.addProperty("screens", rest);
                run(c, rest.equals("hide") ? "В инвентаре и меню оверлея не будет" : "В инвентаре и меню оверлей уходит на задний план");
            }
            case "opacity" -> {
                try {
                    c.addProperty("action", "overlay");
                    c.addProperty("opacity", Integer.parseInt(rest.replace("%", "")));
                    run(c, "Непрозрачность фона: " + Config.clamp(Integer.parseInt(rest.replace("%", "")), 0, 100) + "%");
                } catch (NumberFormatException e) {
                    say("/ov opacity 0..100 — сейчас " + Config.opacity + "%");
                }
            }
            case "reset" -> {
                c.addProperty("action", "overlay");
                c.addProperty("reset", true);
                run(c, "Карточки вернулись на места по умолчанию");
            }
            case "toggle", "play", "pause", "next", "prev", "stop" -> {
                c.addProperty("action", "media");
                c.addProperty("command", verb);
                run(c, null);
            }
            case "food" -> food(rest);
            case "ext" -> connectBrowser();
            case "claude" -> say(Installer.installClaudeExtension());
            case "status" -> status();
            default -> help();
        }
    }

    /**
     * /ov ext and the card's "Подключить": unpacks the extension, puts its folder on the
     * clipboard and opens the browser's extensions page; the person loads it unpacked.
     * (A browser installs an unpacked extension only by the person's own hand.)
     */
    static void connectBrowser() {
        var dir = Installer.unpackBrowserExtension();
        if (Browser.connected()) {
            say("Браузер уже подключён ✔ (" + Browser.browserName + "). Обновил файлы расширения: на странице расширений нажми ⟳ у Overlaycraft.");
            Installer.openExtensionsPage();
            return;
        }
        Minecraft.getInstance().keyboardHandler.setClipboard(dir.toString());
        String browser = Installer.openExtensionsPage();
        say("Подключение браузера" + (browser.isEmpty() ? "" : " (" + browser + ")") + ":");
        say("1. " + (browser.isEmpty() ? "Открой страницу расширений (opera://extensions, chrome://extensions)" : "Открылась страница расширений")
            + " → включи «Режим разработчика»");
        say("2. «Загрузить распакованное» → вставь путь Ctrl+V (он уже скопирован) → «Выбрать папку»");
        say("Если Overlaycraft там уже есть — нажми у него ⟳ и проверь, что переключатель включён (после обновления браузер выключает его сам)");
        say("3. Всё: карточка «Браузер» оживёт сама, вкладки перезагружать не нужно. Для заказа войди на eda.yandex.ru в этом браузере");
    }

    /** /ov food 25 Готовят — manual order (minutes optional); /ov food clear. */
    private static void food(String rest) {
        JsonObject c = new JsonObject();
        if (rest.isEmpty()) { say("/ov food [минуты] <статус> — вручную; /ov food clear — убрать"); return; }
        if (rest.equals("clear")) {
            c.addProperty("action", "food_clear");
            run(c, "Заказ убран");
            return;
        }
        c.addProperty("action", "food");
        String[] p = rest.split("\\s+", 2);
        String status = rest;
        try {
            double minutes = Double.parseDouble(p[0].replace("м", "").replace("m", ""));
            c.addProperty("minutes", minutes);
            status = p.length > 1 ? p[1] : "";
        } catch (NumberFormatException ignored) { }
        if (!status.isEmpty()) c.addProperty("status", status);
        else if (Browser.order == null || !Browser.order.manual) c.addProperty("status", "Заказ в пути");
        run(c, "Заказ обновлён");
    }

    private static void run(JsonObject c, String ok) {
        JsonObject result = Control.run(c);
        if (result.has("error")) say("✖ " + result.get("error").getAsString());
        else if (ok != null) say(ok);
    }

    private static void status() {
        Media.Session s = Media.shown();
        say("Оверлей: " + (Config.enabled ? "включён" : "выключен") + " · в других экранах: " + Config.screens
            + " · фон " + Config.opacity + "%");
        say("Музыка: " + (s == null ? (Media.error.isEmpty() ? "ничего не играет" : Media.error)
            : Browser.source(s).name() + " — " + s.title + " (" + s.status + ")"));
        say("Браузер: " + (Browser.connected() ? "подключён, вкладок " + Browser.tabs.size() : "не подключён (/ov ext)")
            + (Server.error.isEmpty() ? "" : " · сервер: " + Server.error));
        Browser.Order o = Browser.order;
        say("Еда: " + (o == null || !o.active() ? (Browser.edaState.equals("login") ? "не вошёл в Яндекс Еду в браузере" : "нет активного заказа") : o.status + (o.minutesLeft() >= 0 ? ", ~" + o.minutesLeft() + " мин" : "")));
    }

    private static void help() {
        say("◆ Overlaycraft — музыка, Яндекс Еда и вкладки браузера поверх игры");
        say("В чате (T): тяни карточку мышью, ПКМ — свернуть в строку, ✖ — скрыть, кнопки ⏮ ⏯ ⏭ работают");
        say("/ov on|off · show|hide|compact music|food|tabs · screens hide|behind · opacity 0..100 · reset");
        say("/ov toggle|next|prev · food [мин] <статус> · food clear · ext (расширение браузера) · claude · status");
    }

    private static final net.minecraft.client.gui.components.toasts.SystemToast.SystemToastId TOAST =
        new net.minecraft.client.gui.components.toasts.SystemToast.SystemToastId(6000L);

    /** A toast in the corner (and a short sound) — for news that matter, like the courier leaving. */
    static void notify(String head, String text, boolean loud) {
        Minecraft mc = Minecraft.getInstance();
        net.minecraft.client.gui.components.toasts.SystemToast.addOrUpdate(mc.gui.toastManager(), TOAST,
            Component.literal(head), Component.literal(text == null ? "" : text));
        try {
            var event = net.minecraft.sounds.SoundEvent.createVariableRangeEvent(net.minecraft.resources.Identifier.parse(
                loud ? "minecraft:block.note_block.bell" : "minecraft:block.note_block.chime"));
            mc.getSoundManager().play(net.minecraft.client.resources.sounds.SimpleSoundInstance.forUI(event, 1.0f, 0.8f));
        } catch (RuntimeException ignored) { }
    }

    static void say(String text) {
        Minecraft mc = Minecraft.getInstance();
        if (mc.player == null) { LOG.info(text); return; }
        Component line = Component.literal("◆ ").withStyle(Style.EMPTY.withColor(TextColor.fromRgb(0xD97757)))
            .append(Component.literal(text).withStyle(Style.EMPTY.withColor(TextColor.fromRgb(0xE8E8E8))));
        mc.gui.chatListener().handleSystemMessage(line, false);
    }
}
