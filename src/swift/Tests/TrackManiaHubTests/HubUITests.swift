import SwiftUI
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

    func testSidebarKeepsBackendOrderAndDefaultsToInstalledThenSupported() {
        let original = HubTitle(id: "tm-original", name: "Original", status: .unsupported)
        let tmnf = HubTitle(id: "tmnf", name: "Nations", status: .supported)
        let tmu = HubTitle(id: "tmu", name: "United", status: .provisional)
        let canyon = HubTitle(id: "tm-canyon", name: "Canyon", status: .unsupported, state: HubInstallationState(game: true))
        XCTAssertEqual([original, tmnf, tmu, canyon].sidebarOrder.map(\.id), ["tm-canyon", "tm-original", "tmnf", "tmu"])
        XCTAssertEqual([original, tmnf, tmu, canyon].defaultSelection?.id, "tm-canyon")
        XCTAssertEqual([original, tmnf, tmu].defaultSelection?.id, "tmnf")
        XCTAssertEqual([original, tmu].defaultSelection?.id, "tm-original")
        XCTAssertNil([HubTitle]().defaultSelection)
    }

    func testSupportLabelAndColourPerStatus() {
        let titles = [TitleStatus.supported, .provisional, .unsupported, .unknown("beta")].map {
            HubTitle(id: "x", name: "X", status: $0)
        }
        XCTAssertEqual(titles.map(\.supportLabel), ["Supported", "Provisional", "Unsupported", "Unsupported"])
        XCTAssertEqual(titles.map(\.supportColor), [.green, .orange, .secondary, .secondary])
    }

    func testDecodesNewFields() throws {
        let json = #"""
        {"schema":3,"hubVersion":"0.4.1","titles":[{"id":"tmnf","name":"N","sourceSize":1500000000,
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
        let snapshot = try HubBackend.decodeSnapshot(Data(#"{"schema":3,"titles":[{"id":"x","name":"X"}]}"#.utf8))
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

    func testSettingsOfTheEarlierBundleIdentifierAreCopiedOnceAndOnlyRead() throws {
        let suite = "trackmania-mac.tests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let legacy: [String: Any] = [
            HubDefaults.didShowWelcome: true, HubDefaults.checkForUpdates: false, "unrelated": 1
        ]
        HubDefaults.adoptLegacySettings(into: defaults, from: legacy)
        XCTAssertEqual(defaults.object(forKey: HubDefaults.didShowWelcome) as? Bool, true)
        XCTAssertEqual(defaults.object(forKey: HubDefaults.checkForUpdates) as? Bool, false)
        XCTAssertNil(defaults.object(forKey: HubDefaults.dismissedUpdate))
        XCTAssertNil(defaults.object(forKey: "unrelated"))
        // Once anything is set under the current identifier, nothing is copied again.
        defaults.set(true, forKey: HubDefaults.checkForUpdates)
        HubDefaults.adoptLegacySettings(into: defaults, from: [HubDefaults.checkForUpdates: false])
        XCTAssertEqual(defaults.object(forKey: HubDefaults.checkForUpdates) as? Bool, true)
        // No earlier domain: nothing changes.
        let fresh = try XCTUnwrap(UserDefaults(suiteName: suite + ".fresh"))
        defer { fresh.removePersistentDomain(forName: suite + ".fresh") }
        HubDefaults.adoptLegacySettings(into: fresh, from: nil)
        XCTAssertTrue(HubDefaults.keys.allSatisfy { fresh.object(forKey: $0) == nil })
    }

    func testMigrationStartsByItselfOnceAndNeverDuringARun() {
        let pending = HubSnapshot(schema: 3, titles: [], error: nil, migration: HubMigration(state: .pending))
        XCTAssertTrue(HubViewModel.shouldMigrate(pending, attempted: false, busy: false))
        XCTAssertFalse(HubViewModel.shouldMigrate(pending, attempted: true, busy: false))
        XCTAssertFalse(HubViewModel.shouldMigrate(pending, attempted: false, busy: true))
        for state in [HubMigration.State.none, .blocked, .unknown("later")] {
            let snapshot = HubSnapshot(schema: 3, titles: [], error: nil, migration: HubMigration(state: state))
            XCTAssertFalse(HubViewModel.shouldMigrate(snapshot, attempted: false, busy: false))
        }
        XCTAssertFalse(HubViewModel.shouldMigrate(HubSnapshot(schema: 3, titles: [], error: nil),
                                                  attempted: false, busy: false))
    }

    func testReportPathAndFailureReason() {
        let out = "Doing\nDiagnostics report: /Users/me/Library/TrackManiaMacOS/logs/d.zip\n"
        XCTAssertEqual(HubViewModel.reportPath(in: out), "/Users/me/Library/TrackManiaMacOS/logs/d.zip")
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

    func testRegisterKeyShownForSerialTitleWithSettingsReady() {
        let state = HubInstallationState(game: true, gameApp: true, settingsApp: true)
        let title = HubTitle(id: "t", name: "T", requiresSerial: true, state: state,
                             apps: HubApps(game: "/g.app", settings: "/s.app"))
        XCTAssertTrue(title.showsRegisterKey)
        XCTAssertEqual(title.registerKeyTarget, "/s.app")
        XCTAssertEqual(HubUIText.registerKeyButton, "Register player key")
        XCTAssertEqual(HubUIText.registerKeyNote,
                       "First time? Register your player key, then press Play. Play can't ask for the key itself.")
    }

    func testRegisterKeyHiddenWithoutSettingsAppSerialOrGame() {
        let apps = HubApps(game: "/g.app", settings: "/s.app")
        let noSettings = HubTitle(id: "t", name: "T", requiresSerial: true,
                                  state: HubInstallationState(game: true, gameApp: true), apps: apps)
        XCTAssertFalse(noSettings.showsRegisterKey)
        XCTAssertNil(noSettings.registerKeyTarget)
        let state = HubInstallationState(game: true, gameApp: true, settingsApp: true)
        XCTAssertFalse(HubTitle(id: "t", name: "T", requiresSerial: false, state: state, apps: apps).showsRegisterKey)
        XCTAssertFalse(HubTitle(id: "t", name: "T", state: state, apps: apps).showsRegisterKey)
        let noGame = HubInstallationState(gameApp: true, settingsApp: true)
        XCTAssertFalse(HubTitle(id: "t", name: "T", requiresSerial: true, state: noGame, apps: apps).showsRegisterKey)
    }

    func testPlayDisabledUntilCDKeyPresent() {
        var state = HubInstallationState(game: true, gameApp: true, settingsApp: true)
        state.cdKey = HubProbe(ready: false, note: "CD key required")
        let locked = HubTitle(id: "tmu", name: "United", requiresSerial: true, state: state)
        XCTAssertEqual(locked.primaryControl, .play)
        XCTAssertTrue(locked.needsCDKey)
        XCTAssertFalse(locked.playEnabled)
        XCTAssertEqual(locked.availability, "CD key required")
        XCTAssertEqual(locked.summary.last?.label, "CD key")
        XCTAssertEqual(HubUIText.cdKeyRequired, "CD key required")
        XCTAssertEqual(HubLinks.cdKeyHelp.lastPathComponent, "tmuf-cd-key.md")

        state.cdKey = HubProbe(ready: true)
        let unlocked = HubTitle(id: "tmu", name: "United", requiresSerial: true, state: state)
        XCTAssertFalse(unlocked.needsCDKey)
        XCTAssertTrue(unlocked.playEnabled)
        XCTAssertEqual(unlocked.availability, "Ready to play")
        // Registered: the register button and its note give way to Settings.
        XCTAssertTrue(locked.showsRegisterKey)
        XCTAssertFalse(unlocked.showsRegisterKey)
        XCTAssertNil(unlocked.registerKeyTarget)
    }

    func testTitlesWithoutCDKeyProbeAreNeverGated() {
        let nations = HubTitle(id: "tmnf", name: "Nations", state: playReady)
        XCTAssertFalse(nations.needsCDKey)
        XCTAssertTrue(nations.playEnabled)
        XCTAssertFalse(nations.summary.contains { $0.label == "CD key" })
    }

    func testDecodesCDKeyProbe() throws {
        let json = #"""
        {"schema":3,"titles":[{"id":"tmu","name":"U","state":{"game":{"ready":true},
         "gameApp":{"ready":true},"cdKey":{"ready":false,"note":"CD key required"}}},
         {"id":"tmnf","name":"N","state":{"game":{"ready":true},"gameApp":{"ready":true}}}]}
        """#
        let titles = try HubBackend.decodeSnapshot(Data(json.utf8)).titles
        XCTAssertEqual(titles[0].state.cdKey, HubProbe(ready: false, note: "CD key required"))
        XCTAssertFalse(titles[0].playEnabled)
        XCTAssertNil(titles[1].state.cdKey)
        XCTAssertTrue(titles[1].playEnabled)
    }
}

extension HubInstallationState {
    init(game: Bool = false, gameApp: Bool = false, settingsApp: Bool = false) {
        self.init()
        if game { self.game = HubProbe(ready: true) }
        if gameApp { self.gameApp = HubProbe(ready: true) }
        if settingsApp { self.settingsApp = HubProbe(ready: true) }
    }
}
