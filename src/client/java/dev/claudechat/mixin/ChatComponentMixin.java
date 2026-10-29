package dev.claudechat.mixin;

import dev.claudechat.ChatSize;
import net.minecraft.client.gui.components.ChatComponent;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** Lets ChatSize resize the vanilla chat past the limits of the game's options. */
@Mixin(ChatComponent.class)
abstract class ChatComponentMixin {
    @Inject(method = "getWidth()I", at = @At("HEAD"), cancellable = true)
    private void claudecraft$width(CallbackInfoReturnable<Integer> cir) {
        int w = ChatSize.width(((ChatComponent) (Object) this).isChatFocused());
        if (w > 0) cir.setReturnValue(w);
    }

    @Inject(method = "getHeight()I", at = @At("HEAD"), cancellable = true)
    private void claudecraft$height(CallbackInfoReturnable<Integer> cir) {
        int h = ChatSize.height(((ChatComponent) (Object) this).isChatFocused());
        if (h > 0) cir.setReturnValue(h);
    }
}
