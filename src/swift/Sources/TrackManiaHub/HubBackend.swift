import Foundation

struct HubCommandResult: Sendable {
    let status: Int32
    let output: String
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

enum HubBackendError: LocalizedError {
    case launcherMissing(URL)
    case commandFailed(HubCommandResult)
    case malformedSnapshot(Error)

    var errorDescription: String? {
        switch self {
        case let .launcherMissing(url):
            "The bundled launcher is missing: \(url.path)"
        case let .commandFailed(result):
            result.output.isEmpty ? "The hub command failed (status \(result.status))." : result.output
        case let .malformedSnapshot(error):
            "The launcher returned invalid hub data: \(error.localizedDescription)"
        }
    }
}

final class HubBackend: Sendable {
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
        let result = try await run(arguments: ["hub", "snapshot"], onOutput: { _ in })
        guard result.status == 0 else { throw HubBackendError.commandFailed(result) }
        do {
            let snapshot = try JSONDecoder().decode(HubSnapshot.self, from: Data(result.output.utf8))
            guard snapshot.schema == 2 else {
                throw DecodingError.dataCorrupted(.init(codingPath: [], debugDescription: "Unknown snapshot schema"))
            }
            return snapshot
        } catch {
            throw HubBackendError.malformedSnapshot(error)
        }
    }

    func run(titleID: String, actionID: String, source: URL? = nil,
             onOutput: @escaping @Sendable (String) -> Void = { _ in }) async throws -> HubCommandResult {
        let result = try await run(
            arguments: Self.runArguments(titleID: titleID, actionID: actionID, source: source),
            onOutput: onOutput)
        guard result.status == 0 else { throw HubBackendError.commandFailed(result) }
        return result
    }

    private func run(arguments: [String], onOutput: @escaping @Sendable (String) -> Void) async throws -> HubCommandResult {
        let launcher = launcherURL
        return try await Task.detached(priority: .userInitiated) {
            try Self.execute(launcher: launcher, arguments: arguments, onOutput: onOutput)
        }.value
    }

    static func execute(launcher: URL, arguments: [String],
                        onOutput: @escaping @Sendable (String) -> Void = { _ in }) throws -> HubCommandResult {
        let process = Process()
        let output = Pipe()
        let collector = HubOutputCollector()
        process.executableURL = launcher
        process.arguments = arguments
        process.currentDirectoryURL = launcher.deletingLastPathComponent()
        process.standardOutput = output
        process.standardError = output
        output.fileHandleForReading.readabilityHandler = { handle in
            let chunk = handle.availableData
            guard !chunk.isEmpty else { return }
            collector.append(chunk)
            onOutput(String(decoding: chunk, as: UTF8.self))
        }
        try process.run()
        process.waitUntilExit()
        output.fileHandleForReading.readabilityHandler = nil
        let remaining = output.fileHandleForReading.readDataToEndOfFile()
        if !remaining.isEmpty {
            collector.append(remaining)
            onOutput(String(decoding: remaining, as: UTF8.self))
        }
        return HubCommandResult(status: process.terminationStatus,
                                output: String(decoding: collector.value(), as: UTF8.self))
    }
}
