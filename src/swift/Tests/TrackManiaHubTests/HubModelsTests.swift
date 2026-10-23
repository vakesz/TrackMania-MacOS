import AppKit
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
        XCTAssertEqual(snapshot.titles.single?.summary.allSatisfy(\.ready), true)
        XCTAssertEqual(snapshot.titles.single?.kind, .online)
        XCTAssertEqual(snapshot.titles.single?.actions.single?.kind, .verify)
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

@MainActor
final class HubTolerantDecodingTests: XCTestCase {
    private func decode(_ json: String) throws -> HubSnapshot {
        try JSONDecoder().decode(HubSnapshot.self, from: Data(json.utf8))
    }

    func testMalformedTitleIsSkippedAndReported() throws {
        let snapshot = try decode("""
        {"schema":2,"titles":[{"name":"No id"},{"id":"ok","name":"Fine"},{"id":"bad","actions":"nope","state":7}]}
        """)
        XCTAssertEqual(snapshot.titles.map(\.id), ["ok", "bad"])
        XCTAssertEqual(snapshot.skippedTitles.count, 1)
        XCTAssertEqual(snapshot.titles.last?.actions, [])
        XCTAssertEqual(snapshot.titles.last?.state, .empty)
    }

    func testMissingFieldsGetDefaults() throws {
        let title = try XCTUnwrap(decode(#"{"schema":2,"titles":[{"id":"x"}]}"#).titles.first)
        XCTAssertEqual(title.name, "x")
        XCTAssertEqual(title.notes, "")
        XCTAssertEqual(title.status, .unknown(""))
        XCTAssertNil(title.kind)
        XCTAssertNil(title.iconSource)
    }

    func testUnknownValuesRoundTripAndExtraKeysAreIgnored() throws {
        let title = try XCTUnwrap(decode("""
        {"schema":2,"future":1,"titles":[{"id":"x","status":"beta","kind":"cloud","iconSource":"web","newField":[1],
         "actions":[{"id":"a","label":"A","primary":true,"kind":"teleport","extra":1},{"id":"b","kind":"online-trial"}]}]}
        """).titles.first)
        XCTAssertEqual(title.status, .unknown("beta"))
        XCTAssertEqual(title.status.rawValue, "beta")
        XCTAssertEqual(title.kind, .unknown("cloud"))
        XCTAssertEqual(title.iconSource, .unknown("web"))
        XCTAssertEqual(title.actions.map(\.kind), [.unknown("teleport"), .unknown("online-trial")])
    }

    func testKnownEnumValues() throws {
        XCTAssertEqual(InstallerKind.parse("steam-copy"), .steamCopy)
        XCTAssertEqual(InstallerKind.parse("disk-image"), .diskImage)
        XCTAssertEqual(InstallerKind.parse("online-retail"), .onlineRetail)
        XCTAssertEqual(ActionKind.parse("build"), .build)
        XCTAssertEqual(TitleStatus.parse("provisional"), .provisional)
        let source = try XCTUnwrap(decode(#"{"schema":2,"titles":[{"id":"x","iconSource":"bundled"}]}"#).titles.first)
        XCTAssertEqual(source.iconSource, .bundled)
        let none = try XCTUnwrap(decode(#"{"schema":2,"titles":[{"id":"x","iconSource":null}]}"#).titles.first)
        XCTAssertNil(none.iconSource)
    }

    func testSchemaMismatchIsReported() {
        XCTAssertThrowsError(try HubBackend.decodeSnapshot(Data(#"{"schema":3,"titles":[]}"#.utf8))) { error in
            guard case HubBackendError.schemaMismatch(3) = error else { return XCTFail("\(error)") }
        }
    }

    func testFallbackHueIsDeterministicAndVaried() {
        XCTAssertEqual(FallbackIcon.hue(for: "tmnf"), FallbackIcon.hue(for: "tmnf"))
        XCTAssertNotEqual(FallbackIcon.hue(for: "tmnf"), FallbackIcon.hue(for: "tmu"))
        XCTAssertTrue((0..<1).contains(FallbackIcon.hue(for: "anything")))
        XCTAssertEqual(FallbackIcon.label(for: HubTitle(id: "tmx", name: "N")), "TMX")
    }

    private func tempDirectory() throws -> URL {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("trackmania-hub-icons-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: directory) }
        return directory
    }

    func testIconCacheReusesImageAndReloadsOnChange() throws {
        let file = try tempDirectory().appendingPathComponent("icon.png")
        let image = NSImage(size: NSSize(width: 8, height: 8), flipped: false) { rect in
            NSColor.red.setFill(); rect.fill(); return true
        }
        let rep = try XCTUnwrap(NSBitmapImageRep(data: try XCTUnwrap(image.tiffRepresentation)))
        try XCTUnwrap(rep.representation(using: .png, properties: [:])).write(to: file)

        let cache = IconCache()
        let first = try XCTUnwrap(cache.image(atPath: file.path))
        XCTAssertTrue(first === cache.image(atPath: file.path))
        XCTAssertEqual(cache.loadCount, 1)

        try FileManager.default.setAttributes([.modificationDate: Date().addingTimeInterval(60)], ofItemAtPath: file.path)
        XCTAssertNotNil(cache.image(atPath: file.path))
        XCTAssertEqual(cache.loadCount, 2)
        XCTAssertNil(cache.image(atPath: file.path + ".missing"))
    }

    func testSVGLoadsOrFallsBackWithoutCrashing() throws {
        let file = try tempDirectory().appendingPathComponent("icon.svg")
        try #"<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" fill="blue"/></svg>"#
            .write(to: file, atomically: true, encoding: .utf8)
        let image = IconCache().image(atPath: file.path)
        print("SVG_RENDER_RESULT: \(image.map { "loaded size=\($0.size)" } ?? "nil (fallback tile)")")
    }
}

final class HubProcessTests: XCTestCase {
    private func script(_ body: String) throws -> URL {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("trackmania-hub-proc-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("run.sh")
        try ("#!/bin/sh\n" + body).write(to: url, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: url.path)
        return url
    }

    private static func killLeftover(pidFile: URL) {
        if let text = try? String(contentsOf: pidFile, encoding: .utf8), let pid = Int32(text.trimmingCharacters(in: .whitespacesAndNewlines)) {
            kill(pid, SIGKILL)
        }
    }

    func testReturnsWhenGrandchildKeepsPipeOpen() throws {
        let pidFile = FileManager.default.temporaryDirectory.appendingPathComponent("hub-gc-\(UUID().uuidString).pid")
        addTeardownBlock { HubProcessTests.killLeftover(pidFile: pidFile); try? FileManager.default.removeItem(at: pidFile) }
        let launcher = try script("echo started\nsleep 30 &\necho $! > '\(pidFile.path)'\nexit 0\n")
        let start = Date()
        let result = try HubBackend.execute(launcher: launcher, arguments: [])
        XCTAssertEqual(result.status, 0)
        XCTAssertTrue(result.output.contains("started"))
        XCTAssertLessThan(Date().timeIntervalSince(start), 5)
    }

    func testCancelTerminatesProcessGroup() throws {
        let launcher = try script("echo begin\nsleep 60\n")
        let handle = HubCommandHandle()
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.5) { handle.cancel() }
        let start = Date()
        let result = try HubBackend.execute(launcher: launcher, arguments: [], handle: handle)
        XCTAssertTrue(result.cancelled)
        XCTAssertNotEqual(result.status, 0)
        XCTAssertLessThan(Date().timeIntervalSince(start), 6)
    }

    func testCancelBeforeStartStillStopsCommand() throws {
        let launcher = try script("sleep 60\n")
        let handle = HubCommandHandle()
        handle.cancel()
        let start = Date()
        let result = try HubBackend.execute(launcher: launcher, arguments: [], handle: handle)
        XCTAssertTrue(result.cancelled)
        XCTAssertLessThan(Date().timeIntervalSince(start), 6)
    }

    func testInheritsEnvironmentAndWorkingDirectory() throws {
        setenv("HUB_TEST_VAR", "present", 1)
        let launcher = try script("echo \"$HUB_TEST_VAR\"; pwd; echo err >&2\n")
        let result = try HubBackend.execute(launcher: launcher, arguments: [])
        XCTAssertTrue(result.output.contains("present"))
        XCTAssertTrue(result.output.contains("err"))
        XCTAssertTrue(result.output.contains(launcher.deletingLastPathComponent().resolvingSymlinksInPath().path))
    }

    func testReportsExitStatus() throws {
        let result = try HubBackend.execute(launcher: try script("exit 7\n"), arguments: [])
        XCTAssertEqual(result.status, 7)
        XCTAssertFalse(result.cancelled)
    }
}

private extension Collection {
    var single: Element? { count == 1 ? first : nil }
}
