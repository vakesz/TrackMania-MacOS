import XCTest
@testable import TrackManiaHub

@MainActor
final class HubUITests: XCTestCase {
    private let playReady = HubInstallationState(game: true, gameApp: true)
    private let install = HubAction(id: "install", label: "Install", primary: true, kind: .install)
    private let verify = HubAction(id: "verify", label: "Verify", kind: .verify)
    private let doctor = HubAction(id: "doctor", label: "Run checks", kind: .maintenance)
    private let uninstall = HubAction(id: "uninstall", label: "Uninstall…", kind: .destructive)

    func testPlayIsPrimaryWhenGameAppReady() {
        let title = HubTitle(id: "t", name: "T", state: playReady, actions: [install, verify])
        XCTAssertEqual(title.primaryControl, .play)
        XCTAssertEqual(title.moreActionGroups.setup, [install])
        XCTAssertEqual(title.moreActionGroups.maintenance, [verify])
    }

    func testPlayNeedsGameFilesNotJustWrappers() {
        let wrappersOnly = HubTitle(id: "t", name: "T", state: HubInstallationState(gameApp: true), actions: [install, verify])
        XCTAssertEqual(wrappersOnly.primaryControl, .action(install))
        XCTAssertNotEqual(wrappersOnly.availability, "Ready to play")
        let both = HubTitle(id: "t", name: "T", state: HubInstallationState(game: true, gameApp: true),
                            actions: [install])
        XCTAssertEqual(both.primaryControl, .play)
        XCTAssertEqual(both.availability, "Ready to play")
    }

    func testBackendPrimaryWhenGameAppMissing() {
        let title = HubTitle(id: "t", name: "T", actions: [verify, install])
        XCTAssertEqual(title.primaryControl, .action(install))
        XCTAssertEqual(title.moreActionGroups.setup, [])
        XCTAssertEqual(title.moreActionGroups.maintenance, [verify])
    }

    func testFirstActionIsPrimaryFallbackAndEmptyHasNone() {
        XCTAssertEqual(HubTitle(id: "t", name: "T", actions: [verify]).primaryControl, .action(verify))
        XCTAssertNil(HubTitle(id: "t", name: "T").primaryControl)
    }

    func testActionsGroupedByKind() {
        let title = HubTitle(id: "t", name: "T", state: playReady, actions: [uninstall, doctor, install])
        let groups = title.moreActionGroups
        XCTAssertEqual(groups.setup, [install])
        XCTAssertEqual(groups.maintenance, [doctor])
        XCTAssertEqual(groups.destructive, [uninstall])
    }

    func testSidebarSections() {
        let installed = HubTitle(id: "a", name: "A", state: HubInstallationState(game: true))
        let fresh = HubTitle(id: "b", name: "B")
        let sections = [fresh, installed].sidebarSections
        XCTAssertEqual(sections.installed.map(\.id), ["a"])
        XCTAssertEqual(sections.available.map(\.id), ["b"])
        XCTAssertTrue([fresh].sidebarSections.installed.isEmpty)
        XCTAssertTrue([installed].sidebarSections.available.isEmpty)
    }

    func testSidebarOrderPutsInstalledThenSupportedFirst() {
        let canyon = HubTitle(id: "tm-canyon", name: "Canyon", status: .provisional)
        let tmnf = HubTitle(id: "tmnf", name: "Nations", status: .supported)
        let tmu = HubTitle(id: "tmu", name: "United", status: .provisional,
                           state: HubInstallationState(game: true))
        XCTAssertEqual([canyon, tmnf, tmu].sidebarOrder.map(\.id), ["tmu", "tmnf", "tm-canyon"])
    }

    func testDecodesNewFields() throws {
        let json = #"""
        {"schema":2,"hubVersion":"0.4.1","titles":[{"id":"tmnf","name":"N","sourceSize":1500000000,
         "sessionActive":true,"locations":{"root":"/r","prefix":"/p","gameDir":"/g","userdata":"/u","logs":"/l"},
         "actions":[{"id":"doctor","label":"Run checks","kind":"maintenance"},
                    {"id":"uninstall","label":"Uninstall…","kind":"destructive"}]}]}
        """#
        let snapshot = try HubBackend.decodeSnapshot(Data(json.utf8))
        let title = try XCTUnwrap(snapshot.titles.first)
        XCTAssertEqual(snapshot.hubVersion, "0.4.1")
        XCTAssertEqual(title.sourceSize, 1_500_000_000)
        XCTAssertTrue(title.sessionActive)
        XCTAssertEqual(title.locations, HubLocations(root: "/r", prefix: "/p", gameDir: "/g", userdata: "/u", logs: "/l"))
        XCTAssertEqual(title.actions.map(\.kind), [.maintenance, .destructive])
        XCTAssertEqual(title.installLocationPath, "/g")
    }

    func testOldBackendLeavesNewFieldsAbsent() throws {
        let snapshot = try HubBackend.decodeSnapshot(Data(#"{"schema":2,"titles":[{"id":"x","name":"X"}]}"#.utf8))
        let title = try XCTUnwrap(snapshot.titles.first)
        XCTAssertNil(snapshot.hubVersion)
        XCTAssertNil(title.sourceSize)
        XCTAssertNil(title.locations)
        XCTAssertFalse(title.sessionActive)
        XCTAssertNil(title.installLocationPath)
    }

    func testInstallerLabelsAndChecklistMetadata() {
        XCTAssertEqual(HubTitle(id: "x", name: "X", kind: .steamCopy).installerLabel, "Steam copy")
        XCTAssertNil(HubTitle(id: "x", name: "X", kind: .unknown("?")).installerLabel)
        let items = HubTitle(id: "x", name: "X").summary
        XCTAssertEqual(items.count, 7)
        XCTAssertEqual(items.map(\.symbol), ["shippingbox", "folder", "cube", "display", "gamecontroller",
                                             "play.rectangle", "gearshape"])
    }

    func testProbeNoteSurfaces() {
        var state = HubInstallationState()
        state.runtime = HubProbe(ready: false, note: "missing")
        XCTAssertEqual(HubTitle(id: "x", name: "X", state: state).summary.first?.note, "missing")
    }

    func testReportPathAndFailureReason() {
        let out = "Doing\nDiagnostics report: /Users/me/Library/Logs/TMNF/d.zip\n"
        XCTAssertEqual(HubViewModel.reportPath(in: out), "/Users/me/Library/Logs/TMNF/d.zip")
        XCTAssertNil(HubViewModel.reportPath(in: "nothing"))
        XCTAssertEqual(HubViewModel.failureReason(in: "a\nerror: no disk space\n"), "no disk space")
    }

    func testNotificationsNeedARealAppBundle() {
        XCTAssertFalse(Notifier.canUseNotifications(bundleIdentifier: nil, bundleURL: URL(fileURLWithPath: "/x/.build/debug")))
        XCTAssertFalse(Notifier.canUseNotifications(bundleIdentifier: "a.b", bundleURL: URL(fileURLWithPath: "/x/.build/debug")))
        XCTAssertTrue(Notifier.canUseNotifications(bundleIdentifier: "a.b", bundleURL: URL(fileURLWithPath: "/Apps/T.app")))
    }

    func testUninstallMessageWithoutPreviewNamesPrefixAndFallsBack() {
        let located = HubTitle(id: "x", name: "X", locations: HubLocations(prefix: "/p", userdata: "/u"))
        XCTAssertTrue(HubUIText.uninstall(located).contains("/p"))
        XCTAssertTrue(HubUIText.uninstall(located).contains("/u"))
        XCTAssertTrue(HubUIText.uninstall(HubTitle(id: "x", name: "X")).contains("Wine prefix"))
        XCTAssertEqual(UninstallChoice.choices(for: located), [.gameOnly])
    }

    private let removalItems = [
        HubRemovalItem(kind: "prefix", label: "Wine prefix and game files", path: "/g/prefix"),
        HubRemovalItem(kind: "game-app", label: "Game app", path: "/Apps/Game.app")
    ]

    func testUninstallMessageListsEveryReportedPathAndTheSavesFolder() {
        let title = HubTitle(id: "x", name: "X", removal: HubRemoval(items: removalItems, saves: "/g/UserData"))
        let message = HubUIText.uninstall(title)
        XCTAssertTrue(message.contains("Wine prefix and game files: /g/prefix"))
        XCTAssertTrue(message.contains("Game app: /Apps/Game.app"))
        XCTAssertTrue(message.contains("/g/UserData"))
        XCTAssertTrue(message.contains("shared with other games are kept"))
        XCTAssertEqual(UninstallChoice.choices(for: title), [.keepSaves, .everything])
        XCTAssertEqual(UninstallChoice.keepSaves.label, "Remove game, keep saves")
        XCTAssertEqual(UninstallChoice.everything.label, "Remove everything including saves")
        XCTAssertFalse(UninstallChoice.keepSaves.includesSaves)
        XCTAssertTrue(UninstallChoice.everything.includesSaves)
    }

    func testUninstallWithoutSavesOrWithOnlySavesLeft() {
        let noSaves = HubTitle(id: "x", name: "X", removal: HubRemoval(items: removalItems))
        XCTAssertTrue(HubUIText.uninstall(noSaves).contains("No saved data was found"))
        XCTAssertEqual(UninstallChoice.choices(for: noSaves), [.gameOnly])
        XCTAssertFalse(UninstallChoice.gameOnly.includesSaves)
        let savesOnly = HubTitle(id: "x", name: "X", removal: HubRemoval(saves: "/g/UserData"))
        XCTAssertFalse(HubUIText.uninstall(savesOnly).contains("This removes:"))
        XCTAssertTrue(HubUIText.uninstall(savesOnly).contains("Only your saved data is left, in /g/UserData"))
        XCTAssertEqual(UninstallChoice.choices(for: savesOnly), [.savesOnly])
        XCTAssertTrue(UninstallChoice.savesOnly.includesSaves)
    }
}

extension HubInstallationState {
    init(game: Bool = false, gameApp: Bool = false) {
        self.init()
        if game { self.game = HubProbe(ready: true) }
        if gameApp { self.gameApp = HubProbe(ready: true) }
    }
}
