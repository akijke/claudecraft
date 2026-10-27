package dev.claudechat;

import net.minecraft.client.Minecraft;
import org.lwjgl.sdl.SDLDialog;
import org.lwjgl.sdl.SDL_DialogFileCallback;
import org.lwjgl.system.MemoryUtil;

/**
 * Native "choose folder" dialog of the OS (SDL3, as used by Minecraft 26.3), attached
 * to the game window. The chosen folder becomes a project: /cc project pick <path>.
 */
final class FolderPicker {
    private static SDL_DialogFileCallback callback;  // kept alive until SDL calls it

    private FolderPicker() { }

    static void pick(ClaudeChat mod) {
        Minecraft client = Minecraft.getInstance();
        client.execute(() -> {
            if (callback != null) return;  // a dialog is already open
            callback = SDL_DialogFileCallback.create((userdata, filelist, filter) -> {
                String path = null;
                if (filelist != 0) {
                    long first = MemoryUtil.memGetAddress(filelist);
                    if (first != 0) path = MemoryUtil.memUTF8(first);
                }
                String chosen = path;
                client.execute(() -> {
                    SDL_DialogFileCallback done = callback;
                    callback = null;
                    if (done != null) done.free();
                    if (chosen != null && !chosen.isBlank()) mod.send("cc", mod.command("cc", "project", "pick " + chosen));
                });
            });
            SDLDialog.SDL_ShowOpenFolderDialog(callback, 0, client.getWindow().handle(),
                System.getProperty("user.home"), false);
        });
    }
}
