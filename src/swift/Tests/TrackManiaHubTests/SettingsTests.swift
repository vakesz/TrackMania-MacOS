import AppKit
import SwiftUI
import XCTest
@testable import TrackManiaHub

@MainActor
final class SettingsTests: XCTestCase {
    private func decode(_ json: String) throws -> HubSnapshot {
        try JSONDecoder().decode(HubSnapshot.self, from: Data(json.utf8))
    }

    private let fullStorage = """
    "storage":{"downloads":{"bytes":2048,"items":[{"label":"Wine","path":"/c/wine.tar","bytes":2048}]},
    "runtimes":{"current":"2","items":[{"version":"1","path":"/r/1","bytes":100},{"version":"0","path":"/r/0","bytes":null}],
    "inUse":["2"],"blocked":null},"logs":{"bytes":5,"path":"/l"},"error":null}
    """

    func testDecodesStorageAndLaunchOptions() throws {
        let snapshot = try decode("""
        {"schema":3,"titles":[{"id":"tmnf","name":"N","launchOptions":{"windowSize":"1280x720","audio":"off","x87":"on",
        "renderer":"d9vk","editable":true,"reason":null}}],\(fullStorage)}
        """)
        let storage = try XCTUnwrap(snapshot.storage)
        XCTAssertEqual(storage.downloads?.bytes, 2048)
        XCTAssertEqual(storage.downloads?.items.first?.name, "Wine")
        XCTAssertEqual(storage.runtimes?.items.map(\.name), ["1", "0"])
        XCTAssertEqual(storage.runtimes?.inUse, ["2"])
        XCTAssertNil(storage.runtimes?.removableBytes)
        XCTAssertEqual(storage.logs, HubStorageLogs(bytes: 5, path: "/l"))
        let options = try XCTUnwrap(snapshot.titles.first?.launchOptions)
        XCTAssertEqual(options.windowSize, "1280x720")
        XCTAssertFalse(options.audio)
        XCTAssertTrue(options.x87)
        XCTAssertTrue(options.editable)
    }

    func testMissingAndNullStorageAndOptions() throws {
        let missing = try decode(#"{"schema":3,"titles":[{"id":"a","name":"A"}]}"#)
        XCTAssertNil(missing.storage)
        XCTAssertNil(missing.titles.first?.launchOptions)
        let null = try decode(#"{"schema":3,"titles":[{"id":"a","name":"A","launchOptions":null}],"storage":null}"#)
        XCTAssertNil(null.storage)
        XCTAssertNil(null.titles.first?.launchOptions)
    }

    func testMalformedStorageDoesNotBreakTheSnapshot() throws {
        let wholeBad = try decode(#"{"schema":3,"titles":[{"id":"a","name":"A"}],"storage":"oops"}"#)
        XCTAssertNil(wholeBad.storage)
        XCTAssertEqual(wholeBad.titles.count, 1)
        let partial = try decode("""
        {"schema":3,"titles":[{"id":"a","name":"A"}],"storage":{"downloads":{"bytes":"big","items":[{"label":"x"},5]},
        "runtimes":7,"logs":{"bytes":3,"path":"/l"}}}
        """)
        XCTAssertNil(partial.storage?.downloads?.bytes)
        XCTAssertEqual(partial.storage?.downloads?.items.map(\.name), ["x"])
        XCTAssertNil(partial.storage?.runtimes)
        XCTAssertEqual(partial.storage?.logs?.bytes, 3)
        XCTAssertEqual(partial.titles.count, 1)
    }

    func testMalformedLaunchOptionsDoNotBreakTheTitleOrSnapshot() throws {
        let snapshot = try decode("""
        {"schema":3,"titles":[{"id":"a","name":"A","launchOptions":{"windowSize":"fullscreen","audio":"maybe","x87":"on"}},
        {"id":"b","name":"B","launchOptions":[1]},
        {"id":"c","name":"C","launchOptions":{"windowSize":"fullscreen","audio":"on","x87":"on","editable":"yes"}}]}
        """)
        XCTAssertEqual(snapshot.titles.map(\.id), ["a", "b", "c"])
        XCTAssertNil(snapshot.titles[0].launchOptions)
        XCTAssertNil(snapshot.titles[1].launchOptions)
        XCTAssertEqual(snapshot.titles[2].launchOptions?.editable, false)
    }

    func testSizeText() {
        XCTAssertEqual(HubStorage.sizeText(nil), "Size unknown")
        XCTAssertNotEqual(HubStorage.sizeText(0), "Size unknown")
    }

    func testCleanAndSetDefaultsArguments() {
        XCTAssertEqual(HubBackend.cleanArguments(.downloads), ["hub", "clean", "downloads"])
        XCTAssertEqual(HubBackend.cleanArguments(.runtimes), ["hub", "clean", "runtimes"])
        XCTAssertEqual(HubBackend.cleanArguments(.logs), ["hub", "clean", "logs"])
        XCTAssertEqual(HubBackend.runArguments(titleID: "tmnf", actionID: "set-defaults", windowSize: "1280x720",
                                               audio: false, x87: true),
                       ["hub", "run", "tmnf", "set-defaults", "--window-size", "1280x720", "--audio", "off",
                        "--x87", "on"])
        XCTAssertEqual(HubBackend.runArguments(titleID: "tmnf", actionID: "set-defaults", audio: true),
                       ["hub", "run", "tmnf", "set-defaults", "--audio", "on"])
        XCTAssertEqual(HubBackend.runArguments(titleID: "tmnf", actionID: "install"),
                       ["hub", "run", "tmnf", "install"])
    }

    func testBusyStatusReadsAsBusy() {
        let busy = HubCommandResult(status: 75, output: "")
        XCTAssertTrue(HubViewModel.settingsFailure(busy).contains("running"))
        let refused = HubCommandResult(status: 1, output: "error: nope\n")
        XCTAssertFalse(HubViewModel.settingsFailure(refused).contains("running"))
    }

    func testDraftValidationAndChanges() {
        let options = HubLaunchOptions(windowSize: "fullscreen", audio: true, x87: true)
        var draft = GameDefaultsDraft(options)
        XCTAssertTrue(draft.changes(from: options).isEmpty)
        draft.fullscreen = false
        XCTAssertFalse(draft.isValid)
        XCTAssertTrue(draft.changes(from: options).isEmpty)
        draft.width = "0"
        draft.height = "720"
        XCTAssertFalse(draft.isValid)
        draft.width = "-5"
        XCTAssertFalse(draft.isValid)
        draft.width = "12a"
        XCTAssertFalse(draft.isValid)
        draft.width = " 1280 "
        XCTAssertEqual(draft.windowSize, "1280x720")
        draft.audio = false
        XCTAssertEqual(draft.changes(from: options), GameDefaultsChanges(windowSize: "1280x720", audio: false, x87: nil))
        let windowed = HubLaunchOptions(windowSize: "800x600", audio: true, x87: false)
        let same = GameDefaultsDraft(windowed)
        XCTAssertEqual(same.width, "800")
        XCTAssertTrue(same.changes(from: windowed).isEmpty)
    }

    func testControlDisabledStates() {
        let editable = HubLaunchOptions()
        let locked = HubLaunchOptions(editable: false, reason: "Using a custom renderer.")
        var draft = GameDefaultsDraft(editable)
        draft.audio = false
        XCTAssertNil(SettingsGate.gameDefaultsLock(editable, sessionActive: false))
        XCTAssertNotNil(SettingsGate.gameDefaultsLock(editable, sessionActive: true))
        XCTAssertEqual(SettingsGate.gameDefaultsLock(locked, sessionActive: false), "Using a custom renderer.")
        XCTAssertTrue(SettingsGate.canApply(draft, options: editable, sessionActive: false, busy: false))
        XCTAssertFalse(SettingsGate.canApply(draft, options: editable, sessionActive: true, busy: false))
        XCTAssertFalse(SettingsGate.canApply(draft, options: editable, sessionActive: false, busy: true))
        XCTAssertFalse(SettingsGate.canApply(draft, options: locked, sessionActive: false, busy: false))
        XCTAssertFalse(SettingsGate.canApply(GameDefaultsDraft(editable), options: editable, sessionActive: false,
                                             busy: false))

        let item = HubStorageEntry(name: "1", path: "/r/1", bytes: 1)
        XCTAssertTrue(SettingsGate.canRemoveRuntimes(.init(items: [item]), busy: false))
        XCTAssertFalse(SettingsGate.canRemoveRuntimes(.init(items: []), busy: false))
        XCTAssertFalse(SettingsGate.canRemoveRuntimes(.init(items: [item], blocked: "Game running"), busy: false))
        XCTAssertFalse(SettingsGate.canRemoveRuntimes(.init(items: [item]), busy: true))
        XCTAssertFalse(SettingsGate.canRemoveRuntimes(nil, busy: false))
        XCTAssertFalse(SettingsGate.canClearDownloads(nil, busy: false))
        XCTAssertFalse(SettingsGate.canClearLogs(.init(), busy: true))
    }

    func testGameDefaultTitlesAndDiagnosticsTitles() {
        let diagnostics = HubAction(id: "diagnostics", label: "Diagnostics", kind: .maintenance)
        let installed = HubInstallationState(game: true)
        let snapshot = HubSnapshot(schema: 3, titles: [
            HubTitle(id: "a", name: "A", state: installed, actions: [diagnostics], launchOptions: HubLaunchOptions()),
            HubTitle(id: "b", name: "B", state: installed),
            HubTitle(id: "c", name: "C", launchOptions: HubLaunchOptions())
        ], error: nil)
        XCTAssertEqual(snapshot.gameDefaultTitles.map(\.id), ["a"])
        XCTAssertEqual(snapshot.diagnosticsTitles.map(\.id), ["a"])
    }

    func testSidebarFooterOpensSettingsAndFitsTheSidebar() {
        XCTAssertEqual(HubSidebarFooter.settingsSymbol, "gearshape")
        XCTAssertEqual(HubSidebarFooter.settingsLabel, "Settings")
        // The gear, icon, name and a long version fit the narrowest sidebar.
        let footer = NSHostingView(rootView: HubSidebarFooter(version: "10.10.10"))
        XCTAssertLessThanOrEqual(footer.fittingSize.width, HubSidebar.minimumWidth)
    }
}
