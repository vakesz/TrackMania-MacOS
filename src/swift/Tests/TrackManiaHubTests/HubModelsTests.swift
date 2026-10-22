import XCTest
@testable import TrackManiaHub

private final class OutputProbe: @unchecked Sendable {
    private let lock = NSLock()
    private var firstOutputDelay: TimeInterval?

    func record(_ text: String, since start: Date) {
        guard text.contains("first output") else { return }
        lock.lock()
        firstOutputDelay = Date().timeIntervalSince(start)
        lock.unlock()
    }

    var delay: TimeInterval? {
        lock.lock()
        defer { lock.unlock() }
        return firstOutputDelay
    }
}

final class HubModelsTests: XCTestCase {
    func testDecodesVersionedSnapshot() throws {
        let data = Data("""
        {"schema":2,"titles":[{"id":"tmnf","name":"TrackMania Nations Forever","shortName":"TMNF","status":"supported","notes":"Ready","engine":"tmf-classic","kind":"online","icon":"/tmp/tmnf.icns","error":null,"requiresSerial":false,"state":{"runtime":{"ready":true,"note":"Wine present"},"prefix":{"ready":true},"directx":{"ready":true},"renderer":{"ready":true},"game":{"ready":true},"gameApp":{"ready":true},"settingsApp":{"ready":true}},"apps":{"game":"/Applications/Game.app","settings":"/Applications/Settings.app"},"actions":[{"id":"update","label":"Check for updates","primary":false,"kind":"verify"}]}]}
        """.utf8)
        let snapshot = try JSONDecoder().decode(HubSnapshot.self, from: data)
        XCTAssertEqual(snapshot.schema, 2)
        XCTAssertEqual(snapshot.titles.single?.summary.count, 7)
        XCTAssertEqual(snapshot.titles.single?.actions.single?.id, "update")
        XCTAssertTrue(snapshot.titles.single?.state.gameApp?.ready == true)
        XCTAssertNil(snapshot.error)
    }

    func testDecodesEmptySnapshotError() throws {
        let snapshot = try JSONDecoder().decode(
            HubSnapshot.self,
            from: Data("""
            {"schema":2,"titles":[],"error":"No valid title definitions found."}
            """.utf8)
        )

        XCTAssertTrue(snapshot.titles.isEmpty)
        XCTAssertEqual(snapshot.error, "No valid title definitions found.")
    }

    func testRunArgumentsKeepSourceAsOneArgument() {
        let source = URL(fileURLWithPath: "/Users/example/My Game/setup.exe")
        XCTAssertEqual(HubBackend.runArguments(titleID: "tmu", actionID: "import", source: source),
                       ["hub", "run", "tmu", "import", "--source", source.path])
    }

    func testBackendDrainsLargeCommandOutputBeforeWaiting() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("trackmania-hub-tests-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let script = directory.appendingPathComponent("large-output.sh")
        try """
        #!/bin/sh
        i=0
        while test "$i" -lt 4096; do
          printf '0123456789abcdef0123456789abcdef\\n'
          i=$((i + 1))
        done
        """.write(to: script, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: script.path)

        let result = try HubBackend.execute(launcher: script, arguments: [])
        XCTAssertEqual(result.status, 0)
        XCTAssertGreaterThan(result.output.utf8.count, 100_000)
    }

    func testBackendStreamsOutputBeforeCommandFinishes() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("trackmania-hub-stream-tests-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let script = directory.appendingPathComponent("stream-output.sh")
        try """
        #!/bin/sh
        printf 'first output\\n'
        sleep 2
        printf 'last output\\n'
        """.write(to: script, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: script.path)

        let probe = OutputProbe()
        let start = Date()
        let result = try HubBackend.execute(launcher: script, arguments: []) { text in
            probe.record(text, since: start)
        }
        XCTAssertEqual(result.status, 0)
        XCTAssertTrue(result.output.contains("first output"))
        XCTAssertNotNil(probe.delay)
        XCTAssertLessThan(probe.delay ?? .infinity, 1.5)
    }
}

private extension Collection {
    var single: Element? { count == 1 ? first : nil }
}
