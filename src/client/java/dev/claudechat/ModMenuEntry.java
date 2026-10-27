package dev.claudechat;

import com.terraformersmc.modmenu.api.ConfigScreenFactory;
import com.terraformersmc.modmenu.api.ModMenuApi;

/** Optional Mod Menu integration: "Configure" in the mods list opens the settings screen. */
public final class ModMenuEntry implements ModMenuApi {
    @Override public ConfigScreenFactory<?> getModConfigScreenFactory() {
        return parent -> new SettingsScreen(ClaudeChat.instance(), parent);
    }
}
