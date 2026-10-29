package dev.claudechat;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.mojang.blaze3d.platform.NativeImage;
import com.mojang.blaze3d.systems.RenderSystem;
import com.mojang.renderpearl.api.textures.FilterMode;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.texture.DynamicTexture;
import net.minecraft.resources.Identifier;
import java.awt.RenderingHints;
import java.awt.image.BufferedImage;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Generated / agent-made pictures and videos (bridge/backends/media.py): the gallery index the
 * bridge keeps in config/claude-chat/media/index.json, and GPU textures for showing them.
 * Images are decoded off the render thread (ImageIO: PNG, JPG, GIF, BMP — WebP is not supported)
 * and uploaded on it; a small LRU keeps memory bounded.
 */
final class MediaStore {
    record Entry(String id, String kind, Path path, Path poster, String prompt, String provider, double created) {
        boolean video() { return "video".equals(kind); }
        /** The picture to show: the image itself, or the video's poster frame (may be null). */
        Path still() { return video() ? poster : path; }
        String name() { return path.getFileName().toString(); }
        String label() { return prompt == null || prompt.isBlank() ? name() : prompt; }
    }

    record Tex(Identifier id, int width, int height) { }

    /** Linear filtering: scaled pictures look smooth instead of blocky. */
    static final class SmoothTexture extends DynamicTexture {
        SmoothTexture(NativeImage image) {
            super(() -> "claudecraft media", image);
            sampler = RenderSystem.getSamplerCache().getClampToEdge(FilterMode.LINEAR);
        }
    }

    private static final Tex FAILED = new Tex(null, 0, 0);
    private static final Tex LOADING = new Tex(null, 0, 0);
    private static final Map<String, Tex> CACHE = new LinkedHashMap<>(16, 0.75f, true);
    private static final ExecutorService DECODER = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "claudecraft-media"); t.setDaemon(true); return t;
    });
    private static long pixels;
    private static int counter;
    private static Path dirOverride;
    private static List<Entry> cached = List.of();
    private static long cachedStamp = -1;

    private MediaStore() { }

    static Path dir() {
        return dirOverride != null ? dirOverride : FabricLoader.getInstance().getConfigDir().resolve("claude-chat/media");
    }

    /** DevShots: a sample gallery somewhere else. */
    static void useDir(Path dir) { dirOverride = dir; cachedStamp = -1; }

    /** Newest first; re-read when index.json changes. */
    static synchronized List<Entry> list() {
        Path index = dir().resolve("index.json");
        long stamp;
        try { stamp = Files.getLastModifiedTime(index).toMillis() ^ Files.size(index); }
        catch (IOException e) { return List.of(); }
        if (stamp == cachedStamp) return cached;
        List<Entry> out = new ArrayList<>();
        try {
            JsonArray rows = JsonParser.parseString(Files.readString(index)).getAsJsonArray();
            for (JsonElement row : rows) {
                Entry e = entry(row.getAsJsonObject());
                if (e != null) out.add(0, e);
            }
        } catch (Exception ignored) { }
        cachedStamp = stamp;
        cached = List.copyOf(out);
        return cached;
    }

    static int indexOf(List<Entry> items, String id) {
        for (int i = 0; i < items.size(); i++) if (items.get(i).id().equals(id)) return i;
        return -1;
    }

    /** A gallery entry from a bridge `media` event or an index.json row. */
    static Entry entry(JsonObject o) {
        String path = str(o, "path");
        if (path.isEmpty()) return null;
        String poster = str(o, "poster");
        String kind = o.has("media") ? str(o, "media") : str(o, "kind");
        try {
            return new Entry(str(o, "id"), kind.equals("video") ? "video" : "image", Path.of(path),
                poster.isEmpty() ? null : Path.of(poster), str(o, "prompt"), str(o, "provider"),
                o.has("created") && !o.get("created").isJsonNull() ? o.get("created").getAsDouble() : 0);
        } catch (RuntimeException e) { return null; }
    }

    private static String str(JsonObject o, String key) {
        return o.has(key) && !o.get(key).isJsonNull() ? o.get(key).getAsString() : "";
    }

    /** Texture of `file` with its longer side at most `maxSide`; null while loading or if it cannot be read. */
    static synchronized Tex texture(Path file, int maxSide) {
        if (file == null) return null;
        String key = maxSide + "|" + file;
        Tex tex = CACHE.get(key);
        if (tex == FAILED || tex == LOADING) return null;
        if (tex != null) return tex;
        CACHE.put(key, LOADING);
        DECODER.execute(() -> {
            NativeImage image;
            try { image = decode(file, maxSide); }
            catch (Exception | OutOfMemoryError e) { synchronized (MediaStore.class) { CACHE.put(key, FAILED); } return; }
            Minecraft.getInstance().execute(() -> upload(key, image));
        });
        return null;
    }

    /** Same as texture() but decodes now (render thread): dev screenshots need the picture in the first frame. */
    static synchronized Tex textureNow(Path file, int maxSide) {
        String key = maxSide + "|" + file;
        Tex tex = CACHE.get(key);
        if (tex != null && tex != LOADING && tex != FAILED) return tex;
        try { upload(key, decode(file, maxSide)); } catch (Exception e) { CACHE.put(key, FAILED); return null; }
        return CACHE.get(key);
    }

    private static synchronized void upload(String key, NativeImage image) {
        Identifier id = Identifier.fromNamespaceAndPath("claude_chat", "media/" + (counter++));
        Minecraft.getInstance().getTextureManager().register(id, new SmoothTexture(image));
        CACHE.put(key, new Tex(id, image.getWidth(), image.getHeight()));
        pixels += (long) image.getWidth() * image.getHeight();
        // keep ~24 M pixels (about 100 MB): drop the least recently drawn ones
        var it = CACHE.entrySet().iterator();
        while (pixels > 24_000_000L && it.hasNext()) {
            var old = it.next();
            if (old.getKey().equals(key)) continue;
            Tex t = old.getValue();
            if (t.id() != null) {
                Minecraft.getInstance().getTextureManager().release(t.id());
                pixels -= (long) t.width() * t.height();
            }
            it.remove();
        }
    }

    /** Forget a file's textures (e.g. after it changed on disk). */
    static synchronized void forget(Path file) {
        var it = CACHE.entrySet().iterator();
        while (it.hasNext()) {
            var e = it.next();
            if (!e.getKey().endsWith("|" + file)) continue;
            Tex t = e.getValue();
            if (t.id() != null) { Minecraft.getInstance().getTextureManager().release(t.id()); pixels -= (long) t.width() * t.height(); }
            it.remove();
        }
    }

    static NativeImage decode(Path file, int maxSide) throws IOException {
        BufferedImage source = javax.imageio.ImageIO.read(file.toFile());
        if (source == null) throw new IOException("unsupported image " + file);
        int w = source.getWidth(), h = source.getHeight();
        double scale = Math.min(1.0, maxSide / (double) Math.max(w, h));
        int tw = Math.max(1, (int) Math.round(w * scale)), th = Math.max(1, (int) Math.round(h * scale));
        BufferedImage img = source;
        if (tw != w || th != h || source.getType() != BufferedImage.TYPE_INT_ARGB) {
            img = new BufferedImage(tw, th, BufferedImage.TYPE_INT_ARGB);
            var g = img.createGraphics();
            g.setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BILINEAR);
            g.setRenderingHint(RenderingHints.KEY_RENDERING, RenderingHints.VALUE_RENDER_QUALITY);
            g.drawImage(source, 0, 0, tw, th, null);
            g.dispose();
        }
        int[] argb = img.getRGB(0, 0, tw, th, null, 0, tw);
        NativeImage out = new NativeImage(tw, th, false);
        for (int y = 0; y < th; y++)
            for (int x = 0; x < tw; x++) out.setPixel(x, y, argb[y * tw + x]);
        return out;
    }

    /** Frame sequence of a video, made by the bridge with ffmpeg (media/frames/<id>/frames.json + 0001.jpg …). */
    record Frames(Path dir, int count, double fps) {
        Path frame(int i) { return dir.resolve(String.format(java.util.Locale.ROOT, "%04d.jpg", i + 1)); }
    }

    static Frames frames(Entry e) {
        Path folder = dir().resolve("frames").resolve(e.id());
        try {
            JsonObject info = JsonParser.parseString(Files.readString(folder.resolve("frames.json"))).getAsJsonObject();
            int count = info.get("count").getAsInt();
            return count > 0 ? new Frames(folder, count, Math.max(1, info.get("fps").getAsDouble())) : null;
        } catch (Exception ex) { return null; }
    }

    static ExecutorService decoder() { return DECODER; }
}
