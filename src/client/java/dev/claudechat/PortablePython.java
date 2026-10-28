package dev.claudechat;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.io.*;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermission;
import java.security.MessageDigest;
import java.time.Duration;
import java.util.*;
import java.util.function.Consumer;
import java.util.regex.Pattern;
import java.util.stream.Stream;
import java.util.zip.GZIPInputStream;

/**
 * Private Python for players without one: a python-build-standalone build
 * (github.com/astral-sh/python-build-standalone) downloaded once into
 * config/claude-chat/runtime/python, checked against the release's SHA256SUMS and
 * unpacked by a small tar reader (no system tar needed). Used only when no
 * Python 3.11+ is found and the "downloadPython" setting is not switched off.
 */
final class PortablePython {
    static final String RELEASES = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest";
    private static final String[] VERSIONS = {"3.13", "3.12"};

    private PortablePython() { }

    /** python executable inside an unpacked build, or null. */
    static Path executable(Path home) {
        boolean windows = isWindows();
        Path exe = windows ? home.resolve("python/python.exe") : home.resolve("python/bin/python3");
        return Files.isRegularFile(exe) ? exe : null;
    }

    static boolean isWindows() { return System.getProperty("os.name").toLowerCase(Locale.ROOT).contains("win"); }

    /** Target triple of this machine in python-build-standalone names, or null when unsupported. */
    static String triple() {
        String os = System.getProperty("os.name").toLowerCase(Locale.ROOT);
        String arch = System.getProperty("os.arch").toLowerCase(Locale.ROOT);
        String cpu = arch.equals("amd64") || arch.equals("x86_64") ? "x86_64"
            : arch.equals("aarch64") || arch.equals("arm64") ? "aarch64" : null;
        if (cpu == null) return null;
        if (os.contains("win")) return cpu + "-pc-windows-msvc";
        if (os.contains("mac") || os.contains("darwin")) return cpu + "-apple-darwin";
        if (os.contains("linux")) return cpu + "-unknown-linux-gnu";
        return null;
    }

    /** Asset name and URL for this machine from the releases/latest JSON. */
    static String[] pick(JsonObject release, String triple) {
        Map<String, String> urls = new HashMap<>();
        for (JsonElement e : release.getAsJsonArray("assets")) {
            JsonObject a = e.getAsJsonObject();
            urls.put(a.get("name").getAsString(), a.get("browser_download_url").getAsString());
        }
        for (String version : VERSIONS) {
            for (String flavour : new String[]{"install_only_stripped", "install_only"}) {
                Pattern name = Pattern.compile("cpython-" + Pattern.quote(version) + "\\.\\d+\\+\\d+-"
                    + Pattern.quote(triple) + "-" + flavour + "\\.tar\\.gz");
                for (Map.Entry<String, String> asset : urls.entrySet())
                    if (name.matcher(asset.getKey()).matches())
                        return new String[]{asset.getKey(), asset.getValue(), urls.get("SHA256SUMS")};
            }
        }
        return null;
    }

    /** Downloads, verifies and unpacks; returns the python executable. */
    static Path install(Path home, Consumer<String> progress) throws Exception {
        Path ready = executable(home);
        if (ready != null) return ready;
        String triple = triple();
        if (triple == null) throw new IOException(L.t("для этой системы нет готового Python — установи Python 3.11+ сам"));
        HttpClient http = HttpClient.newBuilder().followRedirects(HttpClient.Redirect.NORMAL)
            .connectTimeout(Duration.ofSeconds(20)).build();
        progress.accept(L.t("ищу сборку Python"));
        JsonObject release = JsonParser.parseString(get(http, RELEASES, HttpResponse.BodyHandlers.ofString()))
            .getAsJsonObject();
        String[] asset = pick(release, triple);
        if (asset == null) throw new IOException(L.t("в релизе нет Python для ") + triple);
        if (asset[2] == null) throw new IOException(L.t("в релизе нет SHA256SUMS — загрузка остановлена"));
        String expected = null;
        for (String line : get(http, asset[2], HttpResponse.BodyHandlers.ofString()).split("\\R")) {
            String[] parts = line.strip().split("\\s+");
            if (parts.length == 2 && parts[1].equals(asset[0])) expected = parts[0].toLowerCase(Locale.ROOT);
        }
        if (expected == null) throw new IOException(L.t("нет контрольной суммы для ") + asset[0]);

        Files.createDirectories(home.getParent());
        Path archive = home.resolveSibling("python-download.tar.gz");
        HttpResponse<InputStream> response = http.send(HttpRequest.newBuilder(URI.create(asset[1]))
            .header("User-Agent", "Claudecraft").timeout(Duration.ofMinutes(15)).build(),
            HttpResponse.BodyHandlers.ofInputStream());
        if (response.statusCode() != 200) throw new IOException("HTTP " + response.statusCode() + " " + asset[1]);
        long total = response.headers().firstValueAsLong("content-length").orElse(-1);
        MessageDigest sha = MessageDigest.getInstance("SHA-256");
        try (InputStream in = response.body(); OutputStream out = Files.newOutputStream(archive)) {
            byte[] buf = new byte[1 << 16];
            long done = 0, shown = 0;
            for (int n; (n = in.read(buf)) > 0; ) {
                out.write(buf, 0, n);
                sha.update(buf, 0, n);
                done += n;
                if (done - shown > 1 << 20) {
                    shown = done;
                    progress.accept(L.t("скачиваю Python: ") + (done >> 20) + (total > 0 ? " / " + (total >> 20) : "") + L.t(" МБ"));
                }
            }
        }
        String actual = HexFormat.of().formatHex(sha.digest());
        if (!actual.equals(expected)) {
            Files.deleteIfExists(archive);
            throw new IOException(L.t("контрольная сумма Python не совпала — файл удалён"));
        }
        progress.accept(L.t("распаковываю Python"));
        Path temp = home.resolveSibling("python-unpack");
        deleteTree(temp);
        try (InputStream in = new GZIPInputStream(new BufferedInputStream(Files.newInputStream(archive)))) {
            untar(in, temp);
        }
        deleteTree(home);
        Files.move(temp, home);
        Files.deleteIfExists(archive);
        Path exe = executable(home);
        if (exe == null) throw new IOException(L.t("в архиве Python нет python"));
        return exe;
    }

    private static <T> T get(HttpClient http, String url, HttpResponse.BodyHandler<T> body) throws Exception {
        HttpResponse<T> r = http.send(HttpRequest.newBuilder(URI.create(url)).header("User-Agent", "Claudecraft")
            .header("Accept", "application/vnd.github+json").timeout(Duration.ofSeconds(60)).build(), body);
        if (r.statusCode() != 200) throw new IOException("HTTP " + r.statusCode() + " " + url);
        return r.body();
    }

    /** ustar/pax/GNU tar reader: regular files, directories, symlinks, exec bits. */
    static void untar(InputStream in, Path target) throws IOException {
        Path root = target.toAbsolutePath().normalize();
        Files.createDirectories(root);
        byte[] header = new byte[512];
        String longName = null, linkName = null;
        List<String[]> links = new ArrayList<>();
        while (true) {
            if (in.readNBytes(header, 0, 512) < 512) break;
            boolean empty = true;
            for (byte b : header) if (b != 0) { empty = false; break; }
            if (empty) break;
            String name = str(header, 0, 100), prefix = str(header, 345, 155);
            long size = octal(header, 124, 12);
            int mode = (int) octal(header, 100, 8);
            char type = (char) (header[156] == 0 ? '0' : header[156]);
            String link = str(header, 157, 100);
            if (type == 'x' || type == 'L' || type == 'K' || type == 'g') {
                byte[] data = in.readNBytes((int) size);
                skip(in, pad(size));
                if (type == 'L') longName = new String(data, StandardCharsets.UTF_8).replace("\0", "");
                else if (type == 'K') linkName = new String(data, StandardCharsets.UTF_8).replace("\0", "");
                else if (type == 'x') {
                    for (String record : new String(data, StandardCharsets.UTF_8).split("\n")) {
                        int space = record.indexOf(' '), eq = record.indexOf('=');
                        if (space < 0 || eq < 0) continue;
                        String key = record.substring(space + 1, eq), value = record.substring(eq + 1);
                        if (key.equals("path")) longName = value;
                        if (key.equals("linkpath")) linkName = value;
                    }
                }
                continue;
            }
            String path = longName != null ? longName : prefix.isEmpty() ? name : prefix + "/" + name;
            if (linkName != null) link = linkName;
            longName = null;
            linkName = null;
            Path out = root.resolve(path).normalize();
            if (!out.startsWith(root)) throw new IOException("unsafe path in archive: " + path);
            switch (type) {
                case '5' -> Files.createDirectories(out);
                case '2', '1' -> links.add(new String[]{out.toString(), link, String.valueOf(type)});
                case '0', '7' -> {
                    Files.createDirectories(out.getParent());
                    try (OutputStream o = Files.newOutputStream(out)) {
                        long left = size;
                        byte[] buf = new byte[1 << 16];
                        while (left > 0) {
                            int n = in.read(buf, 0, (int) Math.min(buf.length, left));
                            if (n < 0) throw new EOFException(path);
                            o.write(buf, 0, n);
                            left -= n;
                        }
                    }
                    if ((mode & 0100) != 0 && !isWindows()) {
                        try {
                            Set<PosixFilePermission> p = new HashSet<>(Files.getPosixFilePermissions(out));
                            p.addAll(List.of(PosixFilePermission.OWNER_EXECUTE, PosixFilePermission.GROUP_EXECUTE,
                                PosixFilePermission.OTHERS_EXECUTE));
                            Files.setPosixFilePermissions(out, p);
                        } catch (UnsupportedOperationException ignored) { }
                    }
                    skip(in, pad(size));
                    continue;
                }
                default -> { }
            }
            skip(in, size + pad(size));
        }
        // links last, so their targets exist; on Windows (no symlink rights) a copy is made
        for (String[] l : links) {
            Path out = Path.of(l[0]);
            Path dest = l[2].equals("1") ? root.resolve(l[1]).normalize() : out.getParent().resolve(l[1]).normalize();
            if (!dest.startsWith(root)) continue;
            Files.createDirectories(out.getParent());
            try {
                if (l[2].equals("2")) Files.createSymbolicLink(out, Path.of(l[1]));
                else Files.createLink(out, dest);
            } catch (IOException | UnsupportedOperationException e) {
                if (Files.isRegularFile(dest)) Files.copy(dest, out, StandardCopyOption.REPLACE_EXISTING);
            }
        }
    }

    private static long pad(long size) { return (512 - size % 512) % 512; }

    private static void skip(InputStream in, long n) throws IOException {
        while (n > 0) {
            long s = in.skip(n);
            if (s <= 0) { if (in.read() < 0) return; s = 1; }
            n -= s;
        }
    }

    private static String str(byte[] b, int off, int len) {
        int end = off;
        while (end < off + len && b[end] != 0) end++;
        return new String(b, off, end - off, StandardCharsets.UTF_8);
    }

    private static long octal(byte[] b, int off, int len) {
        if ((b[off] & 0x80) != 0) {  // GNU base-256 for big sizes
            long v = 0;
            for (int i = off + 1; i < off + len; i++) v = (v << 8) | (b[i] & 0xFF);
            return v;
        }
        String s = str(b, off, len).strip();
        return s.isEmpty() ? 0 : Long.parseLong(s, 8);
    }

    static void deleteTree(Path dir) throws IOException {
        if (!Files.exists(dir, LinkOption.NOFOLLOW_LINKS)) return;
        try (Stream<Path> walk = Files.walk(dir)) {
            for (Path p : walk.sorted(Comparator.reverseOrder()).toList()) Files.deleteIfExists(p);
        }
    }
}
