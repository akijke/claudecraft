package dev.claudechat;

import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.Font;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.AbstractWidget;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.components.Tooltip;
import net.minecraft.client.gui.components.WidgetTooltipHolder;
import net.minecraft.client.input.InputWithModifiers;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.FormattedText;
import net.minecraft.util.FormattedCharSequence;

/**
 * Shared look of the mod's windows: flat buttons and text fields with a 1px border,
 * panels, header bars and scrollbars, all from {@link Theme}. Behaviour is the vanilla
 * widgets' own (keyboard focus, Tab order, tooltips, click sound); only drawing differs.
 */
final class Ui {
    private Ui() { }

    // ---- buttons ----------------------------------------------------------------
    /** A vanilla-behaving button drawn flat. `selected` marks the current tab / chat. */
    static final class Flat extends Button {
        boolean selected;
        private boolean dark, left;
        private Button delegate;  // Ui.flat(vanilla): presses go to the original button

        Flat(int x, int y, int w, int h, Component message, OnPress press) {
            super(x, y, w, h, message, press, DEFAULT_NARRATION);
        }

        /** Always-dark palette (K window, file manager, key screen). */
        Flat dark() { dark = true; return this; }
        Flat selected(boolean on) { selected = on; return this; }
        /** Label at the left edge (list rows) instead of centred. */
        Flat left() { left = true; return this; }

        @Override public void onPress(InputWithModifiers input) {
            if (delegate != null) delegate.onPress(input); else super.onPress(input);
        }

        @Override protected void extractContents(GuiGraphicsExtractor g, int mouseX, int mouseY, float delta) {
            drawButton(g, this, dark ? Theme.darkUi() : Theme.ui());
        }
    }

    /** Same calls as Button.builder(…).bounds(…).tooltip(…).build(), result drawn flat. */
    static final class Builder {
        private final Component message;
        private final Button.OnPress press;
        private int x, y, w = 150, h = 20;
        private Tooltip tooltip;
        private boolean dark, left;

        private Builder(Component message, Button.OnPress press) { this.message = message; this.press = press; }
        Builder bounds(int x, int y, int w, int h) { this.x = x; this.y = y; this.w = w; this.h = h; return this; }
        Builder pos(int x, int y) { this.x = x; this.y = y; return this; }
        Builder size(int w, int h) { this.w = w; this.h = h; return this; }
        Builder width(int w) { this.w = w; return this; }
        Builder tooltip(Tooltip t) { tooltip = t; return this; }
        Builder dark() { dark = true; return this; }
        Builder left() { left = true; return this; }
        Flat build() {
            Flat b = new Flat(x, y, w, h, message, press);
            if (tooltip != null) b.setTooltip(tooltip);
            if (dark) b.dark();
            if (left) b.left();
            return b;
        }
    }

    static Builder button(Component message, Button.OnPress press) { return new Builder(message, press); }

    /** Redraws an already built vanilla button flat (same bounds, label, tooltip, action). */
    static Flat flat(Button vanilla) {
        Flat b = new Flat(vanilla.getX(), vanilla.getY(), vanilla.getWidth(), vanilla.getHeight(), vanilla.getMessage(), x -> { });
        b.delegate = vanilla;
        b.active = vanilla.active;
        try {
            for (java.lang.reflect.Field f : AbstractWidget.class.getDeclaredFields())
                if (f.getType() == WidgetTooltipHolder.class) {
                    f.setAccessible(true);
                    Tooltip t = ((WidgetTooltipHolder) f.get(vanilla)).get();
                    if (t != null) b.setTooltip(t);
                }
        } catch (ReflectiveOperationException | RuntimeException ignored) { }
        return b;
    }

    static void drawButton(GuiGraphicsExtractor g, Flat b, Theme.Pal p) {
        Font font = Minecraft.getInstance().font;
        int x = b.getX(), y = b.getY(), w = b.getWidth(), h = b.getHeight();
        boolean on = b.active, hover = on && b.isHoveredOrFocused();
        boolean down = hover && b.isHovered() && Minecraft.getInstance().mouseHandler.isLeftPressed();
        int bg = b.selected ? p.selected() : down ? p.down() : hover ? p.hover() : p.bg();
        if (!on && !b.selected) bg = (bg & 0x00FFFFFF) | ((bg >>> 25) << 24);  // half as opaque
        g.fill(x + 1, y + 1, x + w - 1, y + h - 1, bg);
        int line = b.selected ? p.lineHover()
            : b.isFocused() && !b.isHovered() ? p.accent() : hover ? p.lineHover() : p.line();
        // 1px border with the corners left out: reads as slightly rounded
        g.fill(x + 1, y, x + w - 1, y + 1, line);
        g.fill(x + 1, y + h - 1, x + w - 1, y + h, line);
        g.fill(x, y + 1, x + 1, y + h - 1, line);
        g.fill(x + w - 1, y + 1, x + w, y + h - 1, line);
        if (b.selected && b.left) g.fill(x + 1, y + 1, x + 3, y + h - 1, p.accent());  // list row: bar on the left
        else if (b.selected) g.fill(x + 2, y + h - 2, x + w - 2, y + h - 1, p.accent());  // tab: underline
        int color = !on && !b.selected ? p.off() : p.text();
        Component message = b.getMessage();
        if (!on && !b.selected) message = Component.literal(message.getString());  // dim: drop own colours
        FormattedCharSequence label = fit(font, message, w - (b.left ? 10 : 6));
        int tw = font.width(label);
        g.text(font, label, b.left ? x + 6 : x + (w - tw) / 2, y + (h - 8) / 2 + (down ? 1 : 0), color, p.shadow());
    }

    /** The label cut to `room` px with "…". */
    static FormattedCharSequence fit(Font font, Component text, int room) {
        if (font.width(text) <= room) return text.getVisualOrderText();
        FormattedText cut = font.substrByWidth(text, Math.max(0, room - font.width("…")));
        return net.minecraft.locale.Language.getInstance().getVisualOrder(FormattedText.composite(cut, Component.literal("…")));
    }

    // ---- text fields ----------------------------------------------------------------
    /** EditBox with a flat 1px frame (accent when focused) instead of the vanilla black box. */
    static final class Field extends EditBox {
        private boolean dark;

        Field(Font font, int x, int y, int w, int h, Component message) {
            super(font, x, y, w, h, message);
            if (Theme.ui() == Theme.LIGHT_UI) setTextColor(0xFF202020);
        }

        /** Always-dark palette. */
        Field dark() { dark = true; setTextColor(0xFFE0E0E0); return this; }

        // Vanilla draws its sprite when isBordered(); text placement uses the field, which stays true.
        @Override public boolean isBordered() { return false; }
        @Override public int getInnerWidth() { return super.isBordered() ? getWidth() - 8 : getWidth(); }

        @Override public void extractWidgetRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float delta) {
            if (isVisible() && super.isBordered())
                field(g, getX(), getY(), getWidth(), getHeight(), isFocused(), isHovered(), dark ? Theme.darkUi() : Theme.ui());
            super.extractWidgetRenderState(g, mouseX, mouseY, delta);
        }
    }

    /** Background and frame of a text input at (x, y, w, h). */
    static void field(GuiGraphicsExtractor g, int x, int y, int w, int h, boolean focused, boolean hover, Theme.Pal p) {
        g.fill(x, y, x + w, y + h, p.field());
        g.outline(x, y, w, h, focused ? p.accent() : hover ? p.lineHover() : p.line());
    }

    // ---- panels -------------------------------------------------------------------
    /** Filled rectangle with a 1px border. */
    static void panel(GuiGraphicsExtractor g, int x, int y, int w, int h, int fill, int border) {
        g.fill(x, y, x + w, y + h, fill);
        g.outline(x, y, w, h, border);
    }

    /** Header bar across (x, y, w, h) with a hairline below it. */
    static void header(GuiGraphicsExtractor g, int x, int y, int w, int h) {
        g.fill(x, y, x + w, y + h, Theme.HEADER);
        g.fill(x, y + h - 1, x + w, y + h, Theme.LINE);
    }

    /**
     * Slim scrollbar on the track (x, top)–(x + 3, top + height): `first` of `total` rows,
     * `visible` on screen. Nothing when everything fits.
     */
    static void scrollbar(GuiGraphicsExtractor g, int x, int top, int height, int first, int visible, int total) {
        if (total <= visible || height < 8) return;
        g.fill(x, top, x + 3, top + height, Theme.ui() == Theme.LIGHT_UI ? 0x10000000 : 0x14FFFFFF);
        int thumb = Math.max(10, height * visible / total);
        int ty = top + (height - thumb) * Math.max(0, Math.min(first, total - visible)) / Math.max(1, total - visible);
        int color = Theme.ui() == Theme.LIGHT_UI ? 0x60000000 : 0x70FFFFFF;
        g.fill(x, ty + 1, x + 3, ty + thumb - 1, color);  // ends cut: a slightly rounded thumb
        g.fill(x + 1, ty, x + 2, ty + thumb, color);
    }
}
