import Darwin
import Foundation

struct HubCommandResult: Sendable {
    let status: Int32
    let output: String
    var cancelled = false
}

private final class HubOutputCollector: @unchecked Sendable {
    private let lock = NSLock()
    private var data = Data()

    func append(_ chunk: Data) {
        lock.lock()
        data.append(chunk)
        lock.unlock()
    }

    func value() -> Data {
        lock.lock()
        defer { lock.unlock() }
        return data
    }
}

/// Lets callers cancel a running command from any thread. Commands run in their own session,
/// so cancelling signals the whole process group: SIGTERM first, SIGKILL after a grace period.
final class HubCommandHandle: @unchecked Sendable {
    static let killGrace: TimeInterval = 3

    private let lock = NSLock()
    private var pid: pid_t?
    private var cancelRequested = false
    private var finished = false

    var isCancelled: Bool {
        lock.lock()
        defer { lock.unlock() }
        return cancelRequested
    }

    func cancel() {
        lock.lock()
        cancelRequested = true
        let target = finished ? nil : pid
        lock.unlock()
        if let target { terminate(group: target) }
    }

    fileprivate func attach(pid: pid_t) {
        lock.lock()
        self.pid = pid
        let alreadyCancelled = cancelRequested
        lock.unlock()
        if alreadyCancelled { terminate(group: pid) }
    }

    fileprivate func markFinished() {
        lock.lock()
        finished = true
        lock.unlock()
    }

    private var isFinished: Bool {
        lock.lock()
        defer { lock.unlock() }
        return finished
    }

    private func terminate(group pid: pid_t) {
        killpg(pid, SIGTERM)
        DispatchQueue.global().asyncAfter(deadline: .now() + Self.killGrace) { [self] in
            if !isFinished { killpg(pid, SIGKILL) }
        }
    }
}

/// Exit status of the spawned child, filled in by a waiter thread.
private final class HubChildExit: @unchecked Sendable {
    private let lock = NSLock()
    private var raw: Int32?
    private var exitedAt: Date?

    func set(_ status: Int32) {
        lock.lock()
        raw = status
        exitedAt = Date()
        lock.unlock()
    }

    var snapshot: (status: Int32, at: Date)? {
        lock.lock()
        defer { lock.unlock() }
        guard let raw, let exitedAt else { return nil }
        return (raw, exitedAt)
    }
}

enum HubBackendError: LocalizedError {
    case launcherMissing(URL)
    case commandFailed(HubCommandResult)
    case malformedSnapshot(Error)
    case schemaMismatch(Int)
    case cancelled
    case spawnFailed(Int32)

    var errorDescription: String? {
        switch self {
        case let .launcherMissing(url):
            "The bundled launcher is missing: \(url.path)"
        case let .commandFailed(result):
            result.output.isEmpty ? "The hub command failed (status \(result.status))." : result.output
        case let .malformedSnapshot(error):
            "The launcher returned invalid hub data: \(error.localizedDescription)"
        case let .schemaMismatch(found):
            "Hub/backend version mismatch: the launcher reported snapshot schema \(found), "
                + "but this app expects \(HubBackend.supportedSchema). Reinstall TrackMania Mac."
        case .cancelled:
            "Cancelled."
        case let .spawnFailed(code):
            "Could not start the launcher: \(String(cString: strerror(code)))."
        }
    }
}

final class HubBackend: Sendable {
    static let supportedSchema = 2
    /// How long to keep reading after the child exits before giving up on a pipe
    /// that a daemonized grandchild (e.g. wineserver) still holds open.
    static let postExitDrainGrace: TimeInterval = 0.5

    private let launcherURL: URL

    init(launcherURL: URL) {
        self.launcherURL = launcherURL
    }

    static func bundled() throws -> HubBackend {
        guard let resources = Bundle.main.resourceURL else {
            throw HubBackendError.launcherMissing(URL(fileURLWithPath: "launcher"))
        }
        let launcher = resources.appendingPathComponent("launcher/src/shell/cli/tmnf")
        guard FileManager.default.isExecutableFile(atPath: launcher.path) else {
            throw HubBackendError.launcherMissing(launcher)
        }
        return HubBackend(launcherURL: launcher)
    }

    static func runArguments(titleID: String, actionID: String, source: URL? = nil) -> [String] {
        var arguments = ["hub", "run", titleID, actionID]
        if let source {
            arguments += ["--source", source.path]
        }
        return arguments
    }

    func snapshot() async throws -> HubSnapshot {
        let result = try await run(arguments: ["hub", "snapshot"], handle: HubCommandHandle(), onOutput: { _ in })
        guard result.status == 0 else { throw HubBackendError.commandFailed(result) }
        return try Self.decodeSnapshot(Data(result.output.utf8))
    }

    static func decodeSnapshot(_ data: Data) throws -> HubSnapshot {
        let snapshot: HubSnapshot
        do {
            snapshot = try JSONDecoder().decode(HubSnapshot.self, from: data)
        } catch {
            throw HubBackendError.malformedSnapshot(error)
        }
        guard snapshot.schema == supportedSchema else {
            throw HubBackendError.schemaMismatch(snapshot.schema)
        }
        return snapshot
    }

    func run(titleID: String, actionID: String, source: URL? = nil,
             handle: HubCommandHandle = HubCommandHandle(),
             onOutput: @escaping @Sendable (String) -> Void = { _ in }) async throws -> HubCommandResult {
        let result = try await run(
            arguments: Self.runArguments(titleID: titleID, actionID: actionID, source: source),
            handle: handle, onOutput: onOutput)
        guard result.status == 0 else { throw HubBackendError.commandFailed(result) }
        return result
    }

    private func run(arguments: [String], handle: HubCommandHandle,
                     onOutput: @escaping @Sendable (String) -> Void) async throws -> HubCommandResult {
        let launcher = launcherURL
        let result = try await withTaskCancellationHandler {
            try await Task.detached(priority: .userInitiated) {
                try Self.execute(launcher: launcher, arguments: arguments, handle: handle, onOutput: onOutput)
            }.value
        } onCancel: {
            handle.cancel()
        }
        if result.cancelled { throw HubBackendError.cancelled }
        return result
    }

    /// Runs `launcher` in a new session with stdout and stderr merged into one pipe.
    /// Returns once the child has exited and the pipe hit EOF, or shortly after exit if
    /// some other process still holds the pipe's write end.
    static func execute(launcher: URL, arguments: [String],
                        handle: HubCommandHandle = HubCommandHandle(),
                        onOutput: @escaping @Sendable (String) -> Void = { _ in }) throws -> HubCommandResult {
        var fds: [Int32] = [0, 0]
        guard pipe(&fds) == 0 else { throw HubBackendError.spawnFailed(errno) }
        let readFD = fds[0], writeFD = fds[1]
        // Keep the pipe out of unrelated children spawned concurrently; dup2 below clears the flag.
        _ = fcntl(readFD, F_SETFD, FD_CLOEXEC)
        _ = fcntl(writeFD, F_SETFD, FD_CLOEXEC)

        var actions: posix_spawn_file_actions_t?
        posix_spawn_file_actions_init(&actions)
        defer { posix_spawn_file_actions_destroy(&actions) }
        posix_spawn_file_actions_adddup2(&actions, writeFD, STDOUT_FILENO)
        posix_spawn_file_actions_adddup2(&actions, writeFD, STDERR_FILENO)
        posix_spawn_file_actions_addchdir_np(&actions, launcher.deletingLastPathComponent().path)

        var attributes: posix_spawnattr_t?
        posix_spawnattr_init(&attributes)
        defer { posix_spawnattr_destroy(&attributes) }
        posix_spawnattr_setflags(&attributes, Int16(POSIX_SPAWN_SETSID))

        let argv = ([launcher.path] + arguments).map { strdup($0) } + [nil]
        let envp = ProcessInfo.processInfo.environment.map { strdup("\($0.key)=\($0.value)") } + [nil]
        defer {
            argv.forEach { free($0) }
            envp.forEach { free($0) }
        }

        var pid: pid_t = 0
        let spawnStatus = posix_spawn(&pid, launcher.path, &actions, &attributes, argv, envp)
        close(writeFD)
        guard spawnStatus == 0 else {
            close(readFD)
            throw HubBackendError.spawnFailed(spawnStatus)
        }
        handle.attach(pid: pid)
        let childPID = pid

        let exit = HubChildExit()
        DispatchQueue.global(qos: .userInitiated).async {
            var raw: Int32 = 0
            while waitpid(childPID, &raw, 0) < 0 && errno == EINTR {}
            handle.markFinished()
            exit.set(raw)
        }

        let collector = HubOutputCollector()
        var buffer = [UInt8](repeating: 0, count: 16 * 1024)
        readLoop: while true {
            let exited = exit.snapshot
            if let exited, Date().timeIntervalSince(exited.at) > postExitDrainGrace { break }
            var descriptor = pollfd(fd: readFD, events: Int16(POLLIN), revents: 0)
            let ready = poll(&descriptor, 1, 50)
            if ready < 0 {
                if errno == EINTR { continue }
                break
            }
            guard ready > 0 else { continue }
            let count = read(readFD, &buffer, buffer.count)
            if count < 0 {
                if errno == EINTR || errno == EAGAIN { continue }
                break readLoop
            }
            if count == 0 { break readLoop }
            let chunk = Data(buffer[0..<count])
            collector.append(chunk)
            onOutput(String(decoding: chunk, as: UTF8.self))
        }
        close(readFD)

        // The loop can end on EOF before the waiter thread has recorded the status.
        while exit.snapshot == nil { usleep(2_000) }
        let raw = exit.snapshot?.status ?? 0
        let signal = raw & 0x7f
        let status: Int32 = signal == 0 ? (raw >> 8) & 0xff : 128 + signal
        return HubCommandResult(status: status,
                                output: String(decoding: collector.value(), as: UTF8.self),
                                cancelled: handle.isCancelled && status != 0)
    }
}
