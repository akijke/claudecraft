package dev.claudechat;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.CommonComponents;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.Style;
import net.minecraft.util.FormattedCharSequence;
import java.util.ArrayList;
import java.util.List;

/**
 * Secret entry for a connector (/claude key <id>). Values go straight to the bridge in
 * a JSON field — never through the chat input, chat history or bridge events.
 */
final class KeyScreen extends Screen {
    private final ClaudeChat mod;
    private final String connector;
    private final JsonArray fields;
    private final List<EditBox> boxes = new ArrayList<>();
    private final List<String> vars = new ArrayList<>();
    private int top;

    KeyScreen(ClaudeChat mod, String connector, JsonArray fields) {
        super(Component.literal("Ключ: " + connector));
        this.mod = mod;
        this.connector = connector;
        this.fields = fields;
    }

    @Override protected void init() {
        boxes.clear();
        vars.clear();
        int w = Math.min(320, width - 40), x = width / 2 - w / 2;
        top = Math.max(20, height / 2 - 30 - fields.size() * 22);
        int y = top + 24;
        for (JsonElement f : fields) {
            JsonObject field = f.getAsJsonObject();
            EditBox box = new EditBox(font, x, y + 12, w, 18, Component.literal(field.get("var").getAsString()));
            box.setMaxLength(4000);
            box.setHint(Component.literal(field.get("set").getAsBoolean() ? "задан — оставь пустым, чтобы не менять"
                : field.get("hint").getAsString()));
            box.addFormatter((text, offset) -> FormattedCharSequence.forward("•".repeat(text.length()), Style.EMPTY));
            addRenderableWidget(box);
            boxes.add(box);
            vars.add(field.get("var").getAsString());
            y += 34;
        }
        addRenderableWidget(Button.builder(Component.literal("✔ Сохранить и подключить"), b -> save())
            .bounds(x, y + 8, w / 2 - 2, 20).build());
        addRenderableWidget(Button.builder(CommonComponents.GUI_CANCEL, b -> onClose())
            .bounds(x + w / 2 + 2, y + 8, w / 2 - 2, 20).build());
        if (!boxes.isEmpty()) setInitialFocus(boxes.get(0));
    }

    private void save() {
        JsonObject secret = new JsonObject();
        for (int i = 0; i < boxes.size(); i++) {
            String value = boxes.get(i).getValue().strip();
            if (!value.isEmpty()) secret.addProperty(vars.get(i), value);
        }
        JsonObject data = mod.command("cc", "connect", connector);
        data.add("secret", secret);
        mod.send("cc", data);
        onClose();
    }

    @Override public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float delta) {
        super.extractRenderState(g, mouseX, mouseY, delta);
        int w = Math.min(320, width - 40), x = width / 2 - w / 2;
        g.centeredText(font, Component.literal("🔑 " + connector).withColor(0xF2C14E), width / 2, top, 0xFFFFFFFF);
        g.centeredText(font, "Ключ хранится только в config/claude-chat/secrets.json", width / 2, top + 11, 0xFF8A8A8A);
        int y = top + 24;
        for (JsonElement f : fields) {
            JsonObject field = f.getAsJsonObject();
            g.text(font, field.get("var").getAsString() + (field.get("required").getAsBoolean() ? "" : "  (необязательно)"),
                x, y, field.get("set").getAsBoolean() ? 0xFF6BD08B : 0xFFE8E8E8);
            y += 34;
        }
        if (fields.isEmpty()) g.centeredText(font, "Открой /cc connectors и нажми «подключить» ещё раз", width / 2, top + 40, 0xFFA0A0A0);
    }
}
