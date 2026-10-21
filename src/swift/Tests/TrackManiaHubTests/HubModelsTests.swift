import XCTest
@testable import TrackManiaHub

final class HubModelsTests: XCTestCase {
    func testDecodesVersionedSnapshot() throws {
        let data = Data("""
        {"schema":1,"titles":[{"id":"tmnf","name":"TrackMania Nations Forever","status":"supported","notes":"Ready","state":{"runtime":"ready","prefix":"ready","directx":"ready","renderer":"ready","game":"ready","gameApp":"ready","settingsApp":"ready"},"apps":{"game":"/Applications/Game.app","settings":"/Applications/Settings.app"},"actions":[{"id":"update","label":"Check for updates"}]}]}
        """.utf8)
        let snapshot = try JSONDecoder().decode(HubSnapshot.self, from: data)
        XCTAssertEqual(snapshot.schema, 1)
        XCTAssertEqual(snapshot.titles.single?.state.summary.count, 7)
        XCTAssertEqual(snapshot.titles.single?.actions.single?.id, "update")
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
}

private extension Collection {
    var single: Element? { count == 1 ? first : nil }
}
