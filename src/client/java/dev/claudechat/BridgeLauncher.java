package dev.claudechat;

import com.google.gson.*;
import net.fabricmc.loader.api.FabricLoader;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.TimeUnit;
import java.util.function.BooleanSupplier;
import java.util.stream.Stream;

/**
 * Runs the Python bridge as a child of the Minecraft client.
 *
 * The bridge sources ship inside the JAR (claudecraft/bridge). On start they are
 * copied to config/claude-chat/runtime/bridge, a private venv is created with the
 * system Python 3.11+, requirements are installed once (re-installed when
 * requirements.txt changes) and bridge.py is started with --game-dir. A bridge that
 * the user already runs by hand is detected and left alone. The process tree is
 * stopped when the game closes.
 */
final class BridgeLauncher {
    enum State {
        OFF("выключен"), EXTERNAL("запущен вручную"), INSTALLING("установка…"),
        STARTING("запуск…"), RUNNING("работает"), STOPPED("остановлен"), FAILED("ошибка");
        final String label;
        State(String label) { this.label = label; }
    }

    private final Path gameDir = FabricLoader.getInstance().getGameDir().toAbsolutePath();
    private final Path configDir = FabricLoader.getInstance().getConfigDir().resolve("claude-chat");
    private final Path runtime = configDir.resolve("runtime");
    private final Path settingsFile = configDir.resolve("client.json");
    private final BooleanSupplier bridgeAnswers;
    private volatile State state = State.OFF;
    private volatile String detail = "";
    private Process process;
    private Thread worker;
    private volatile boolean stopping;
    JsonObject settings = new JsonObject();

    BridgeLauncher(BooleanSupplier bridgeAnswers) {
        this.bridgeAnswers = bridgeAnswers;
        loadSettings();
    }

    State state() { return state; }
    String detail() { return detail; }
    Path logFile() { return runtime.resolve("bridge.log"); }
    boolean autoStart() { return !settings.has("autoStart") || settings.get("autoStart").getAsBoolean(); }
    String pythonSetting() { return settings.has("python") ? settings.get("python").getAsString() : ""; }

    private void set(State state, String detail) { this.state = state; this.detail = detail == null ? "" : detail; }

    void loadSettings() {
        try {
            if (Files.isRegularFile(settingsFile))
                settings = JsonParser.parseString(Files.readString(settingsFile)).getAsJsonObject();
        } catch (Exception e) { settings = new JsonObject(); }
    }

    void saveSettings(boolean autoStart, String python) {
        settings.addProperty("autoStart", autoStart);
        settings.addProperty("python", python.strip());
        try {
            Files.createDirectories(configDir);
            Files.writeString(settingsFile, new GsonBuilder().setPrettyPrinting().create().toJson(settings));
        } catch (IOException e) { set(state, "не удалось сохранить настройки: " + e.getMessage()); }
    }

    synchronized void start() {
        if (worker != null && worker.isAlive()) return;
        stopping = false;
        worker = new Thread(this::run, "claude-bridge-launcher");
        worker.setDaemon(true);
        worker.start();
    }

    /** Not synchronized: the worker may need the lock to notice it was stopped. */
    void restart() {
        stop();
        Thread old = worker;
        if (old != null && old != Thread.currentThread()) {
            try { old.join(10000); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
        }
        loadSettings();
        start();
    }

    synchronized void stop() {
        stopping = true;
        Process p = process;
        if (p != null) {
            // The bridge spawns Claude CLI processes: stop the whole tree.
            p.descendants().forEach(ProcessHandle::destroy);
            p.destroy();
            try {
                if (!p.waitFor(5, TimeUnit.SECONDS)) {
                    p.descendants().forEach(ProcessHandle::destroyForcibly);
                    p.destroyForcibly();
                }
            } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
        }
        process = null;
        if (state != State.EXTERNAL) set(State.STOPPED, "");
    }

    private void run() {
        try {
            if (bridgeAnswers.getAsBoolean()) { set(State.EXTERNAL, ""); return; }
            set(State.INSTALLING, "распаковка моста");
            Path bridgeDir = extract();
            set(State.INSTALLING, "поиск Python 3.11+");
            List<String> python = findPython();
            Path venvPython = venv(python);
            installRequirements(venvPython, bridgeDir.resolve("requirements.txt"));
            if (stopping) return;
            set(State.STARTING, "");
            Files.createDirectories(runtime);
            rotateLog();
            ProcessBuilder builder = new ProcessBuilder(venvPython.toString(),
                bridgeDir.resolve("bridge.py").toString(), "--game-dir", gameDir.toString())
                .directory(bridgeDir.toFile()).redirectErrorStream(true)
                .redirectOutput(ProcessBuilder.Redirect.appendTo(logFile().toFile()));
            builder.environment().put("PYTHONUTF8", "1");
            builder.environment().put("PYTHONUNBUFFERED", "1");
            synchronized (this) {
                if (stopping) return;
                process = builder.start();
            }
            Process started = process;
            long deadline = System.currentTimeMillis() + 60000;
            while (System.currentTimeMillis() < deadline && started.isAlive() && !bridgeAnswers.getAsBoolean())
                Thread.sleep(300);
            if (!started.isAlive()) throw new IllegalStateException("мост завершился: " + lastLogLine());
            if (!bridgeAnswers.getAsBoolean()) throw new IllegalStateException("мост не ответил за 60 с: " + lastLogLine());
            set(State.RUNNING, "");
            int code = started.waitFor();
            if (!stopping) set(State.FAILED, "мост завершился (код " + code + "): " + lastLogLine());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (Exception e) {
            if (!stopping) set(State.FAILED, e.getMessage());
        }
    }

    /** Copies the bundled bridge sources out of the JAR (always fresh, they are small). */
    private Path extract() throws IOException {
        Path source = FabricLoader.getInstance().getModContainer("claude_chat")
            .flatMap(c -> c.findPath("claudecraft/bridge"))
            .orElseThrow(() -> new IOException("в JAR нет claudecraft/bridge"));
        Path target = runtime.resolve("bridge");
        if (Files.isDirectory(target)) {
            try (Stream<Path> old = Files.walk(target)) {
                for (Path p : old.sorted(Comparator.reverseOrder()).toList()) Files.deleteIfExists(p);
            }
        }
        try (Stream<Path> files = Files.walk(source)) {
            for (Path p : files.toList()) {
                Path out = target.resolve(source.relativize(p).toString());
                if (Files.isDirectory(p)) Files.createDirectories(out);
                else Files.copy(p, out, StandardCopyOption.REPLACE_EXISTING);
            }
        }
        return target;
    }

    private List<String> findPython() throws IOException, InterruptedException {
        List<List<String>> candidates = new ArrayList<>();
        if (!pythonSetting().isBlank()) candidates.add(List.of(pythonSetting()));
        boolean windows = System.getProperty("os.name").toLowerCase(Locale.ROOT).contains("win");
        if (windows) {
            candidates.add(List.of("py", "-3"));
            candidates.add(List.of("python"));
        } else {
            candidates.add(List.of("python3"));
            candidates.add(List.of("python"));
        }
        for (List<String> candidate : candidates) {
            List<String> command = new ArrayList<>(candidate);
            command.addAll(List.of("-c", "import sys;print(sys.version_info[:2]>=(3,11))"));
            try {
                Result result = exec(command, 20);
                if (result.code == 0 && result.output.strip().endsWith("True")) return candidate;
            } catch (IOException ignored) { }
        }
        throw new IOException("не найден Python 3.11+. Установи его с python.org (галочка «Add to PATH») "
            + "или укажи путь к python.exe в /c settings");
    }

    private Path venv(List<String> python) throws IOException, InterruptedException {
        Path venv = runtime.resolve("venv");
        boolean windows = System.getProperty("os.name").toLowerCase(Locale.ROOT).contains("win");
        Path exe = windows ? venv.resolve("Scripts/python.exe") : venv.resolve("bin/python3");
        if (Files.isRegularFile(exe)) return exe;
        set(State.INSTALLING, "создание окружения Python");
        List<String> command = new ArrayList<>(python);
        command.addAll(List.of("-m", "venv", venv.toString()));
        Result result = exec(command, 300);
        if (result.code != 0 || !Files.isRegularFile(exe))
            throw new IOException("не удалось создать venv: " + tail(result.output));
        return exe;
    }

    private void installRequirements(Path venvPython, Path requirements) throws Exception {
        String hash = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
            .digest(Files.readAllBytes(requirements)));
        Path marker = runtime.resolve("venv/.claudecraft-requirements");
        if (Files.isRegularFile(marker) && Files.readString(marker).strip().equals(hash)) return;
        set(State.INSTALLING, "установка зависимостей (первый раз до пары минут)");
        Result result = exec(List.of(venvPython.toString(), "-m", "pip", "install",
            "--disable-pip-version-check", "-r", requirements.toString()), 900);
        if (result.code != 0) throw new IOException("pip install не удался: " + tail(result.output));
        Files.writeString(marker, hash);
    }

    private record Result(int code, String output) { }

    private Result exec(List<String> command, int timeoutSeconds) throws IOException, InterruptedException {
        Process p = new ProcessBuilder(command).redirectErrorStream(true).start();
        p.getOutputStream().close();
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        Thread reader = new Thread(() -> {
            try (InputStream in = p.getInputStream()) { in.transferTo(out); } catch (IOException ignored) { }
        });
        reader.setDaemon(true);
        reader.start();
        if (!p.waitFor(timeoutSeconds, TimeUnit.SECONDS)) {
            p.destroyForcibly();
            throw new IOException("команда не завершилась за " + timeoutSeconds + " с: " + command.get(0));
        }
        reader.join(2000);
        return new Result(p.exitValue(), out.toString(StandardCharsets.UTF_8));
    }

    private void rotateLog() throws IOException {
        Path log = logFile();
        if (Files.isRegularFile(log) && Files.size(log) > 5_000_000)
            Files.move(log, runtime.resolve("bridge.old.log"), StandardCopyOption.REPLACE_EXISTING);
    }

    private String lastLogLine() {
        try {
            List<String> lines = Files.readAllLines(logFile(), StandardCharsets.UTF_8);
            for (int i = lines.size() - 1; i >= 0; i--)
                if (!lines.get(i).isBlank()) return lines.get(i).strip();
        } catch (IOException ignored) { }
        return "см. " + logFile();
    }

    private static String tail(String text) {
        String[] lines = text.strip().split("\\R");
        return lines.length == 0 ? "" : lines[lines.length - 1];
    }
}
