package dev.claudechat;

import net.fabricmc.fabric.api.client.screen.v1.ScreenEvents;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.screens.ChatScreen;
import java.lang.reflect.Field;

/**
 * Keeps what was typed in the vanilla chat (T) when it is closed without sending:
 * vanilla 26.3 throws it away on Esc unless "Save chat drafts" is on. The text is
 * saved while typing (so a crash or quitting the game keeps it too) and put back
 * the next time the chat opens empty. Sending clears it.
 */
final class ChatDraft {
    private static final String KEY = "vanilla-chat";
    private static Field input, exitReason;
    private static String saved;

    private ChatDraft() { }

    static void register() {
        try {
            input = ChatScreen.class.getDeclaredField("input");
            input.setAccessible(true);
            exitReason = ChatScreen.class.getDeclaredField("exitReason");
            exitReason.setAccessible(true);
        } catch (ReflectiveOperationException e) { return; }
        ScreenEvents.AFTER_INIT.register((client, screen, w, h) -> {
            if (!(screen instanceof ChatScreen chat)) return;
            EditBox box = box(chat);
            if (box == null) return;
            String draft = InputHistory.draft(KEY);
            String now = box.getValue();
            // T opens it empty, "/" opens it with "/": only then is there nothing to overwrite
            if (!draft.isEmpty() && (now.isEmpty() || now.equals("/") && draft.startsWith("/"))) box.setValue(draft);
            saved = box.getValue();
            ScreenEvents.afterTick(screen).register(s -> {
                String value = box.getValue();
                if (!value.equals(saved)) { saved = value; InputHistory.setDraft(KEY, value); }
            });
            ScreenEvents.remove(screen).register(s -> {
                if (sent(chat)) InputHistory.setDraft(KEY, "");
                else InputHistory.setDraft(KEY, box.getValue());
            });
        });
    }

    private static EditBox box(ChatScreen chat) {
        try { return (EditBox) input.get(chat); } catch (Exception e) { return null; }
    }

    private static boolean sent(ChatScreen chat) {
        try { return String.valueOf(exitReason.get(chat)).equals("DONE"); } catch (Exception e) { return false; }
    }
}
