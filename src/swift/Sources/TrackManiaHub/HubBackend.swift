import Foundation

struct HubCommandResult: Sendable {
    let status: Int32
    let output: String
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
        let result = try await run(arguments: ["hub", "snapshot"])
        guard result.status == 0 else { throw HubBackendError.commandFailed(result) }
        do {
            let snapshot = try JSONDecoder().decode(HubSnapshot.self, from: Data(result.output.utf8))
            guard snapshot.schema == 1 else {
                throw DecodingError.dataCorrupted(.init(codingPath: [], debugDescription: "Unknown snapshot schema"))
            }
            return snapshot
        } catch {
            throw HubBackendError.malformedSnapshot(error)
        }
    }

    func run(titleID: String, actionID: String, source: URL? = nil) async throws -> HubCommandResult {
        let result = try await run(arguments: Self.runArguments(titleID: titleID, actionID: actionID, source: source))
        guard result.status == 0 else { throw HubBackendError.commandFailed(result) }
        return result
    }

    private func run(arguments: [String]) async throws -> HubCommandResult {
        let launcher = launcherURL
        return try await Task.detached(priority: .userInitiated) {
            try Self.execute(launcher: launcher, arguments: arguments)
        }.value
    }

    static func execute(launcher: URL, arguments: [String]) throws -> HubCommandResult {
        let process = Process()
        let output = Pipe()
        process.executableURL = launcher
        process.arguments = arguments
        process.currentDirectoryURL = launcher.deletingLastPathComponent()
        process.standardOutput = output
        process.standardError = output
        try process.run()
        // Drain before waiting. Waiting first can deadlock once an installer
        // fills the pipe buffer with build/download diagnostics.
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        return HubCommandResult(status: process.terminationStatus,
                                output: String(decoding: data, as: UTF8.self))
    }
}
