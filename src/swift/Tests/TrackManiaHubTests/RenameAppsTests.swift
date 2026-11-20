import XCTest
@testable import TrackManiaHub

final class RenameAppsTests: XCTestCase {
    private func names(_ name: String = "TMU", custom: Bool = false) -> HubAppNames {
        HubAppNames(name: name, defaultName: "TrackMania United Forever", custom: custom)
    }

    func testApplyNeedsANonEmptyChangedName() {
        var draft = RenameAppsDraft(names())
        XCTAssertFalse(draft.canApply)
        draft.text = "  TMU \n"
        XCTAssertFalse(draft.canApply)
        draft.text = "   "
        XCTAssertFalse(draft.canApply)
        draft.text = " My Game "
        XCTAssertTrue(draft.canApply)
        XCTAssertEqual(draft.trimmed, "My Game")
    }

    func testResetNeedsACustomName() {
        XCTAssertFalse(RenameAppsDraft(names()).canReset)
        XCTAssertTrue(RenameAppsDraft(names(custom: true)).canReset)
    }

    func testPreviewShowsBothAppNames() {
        var draft = RenameAppsDraft(names())
        draft.text = " Forever "
        XCTAssertEqual(draft.preview, "Applications will show “Forever” and “Forever Settings”.")
    }

    func testRenameAppsLock() {
        let free = HubTitle(id: "a", name: "A", appNames: names())
        XCTAssertNil(free.renameAppsLock)
        XCTAssertNil(HubTitle(id: "a", name: "A").renameAppsLock)
        XCTAssertNotNil(HubTitle(id: "a", name: "A", sessionActive: true, appNames: names()).renameAppsLock)
        let locked = HubAppNames(name: "TMU", defaultName: "D", editable: false, reason: "Launcher is not ours")
        XCTAssertEqual(HubTitle(id: "a", name: "A", appNames: locked).renameAppsLock, "Launcher is not ours")
        let noReason = HubAppNames(name: "TMU", defaultName: "D", editable: false)
        XCTAssertNotNil(HubTitle(id: "a", name: "A", appNames: noReason).renameAppsLock)
    }

    func testRunArgumentsSendAppNameAsOneArgumentOrReset() {
        XCTAssertEqual(HubBackend.runArguments(titleID: "tmu", actionID: "rename-apps", appName: "-My Game"),
                       ["hub", "run", "tmu", "rename-apps", "--app-name=-My Game"])
        XCTAssertEqual(HubBackend.runArguments(titleID: "tmu", actionID: "rename-apps", resetAppName: true),
                       ["hub", "run", "tmu", "rename-apps", "--reset-app-name"])
    }

    func testAppNamesDecodesPresentAbsentAndNull() throws {
        let snapshot = try HubBackend.decodeSnapshot(Data("""
            {"schema":3,"titles":[
              {"id":"a","name":"A","appNames":{"name":"TMU","default":"TrackMania United Forever","custom":true,
               "game":"TMU.app","settings":"TMU Settings.app","editable":true,"reason":null}},
              {"id":"b","name":"B"},
              {"id":"c","name":"C","appNames":null},
              {"id":"d","name":"D","appNames":{"name":5}}]}
            """.utf8))
        XCTAssertEqual(snapshot.titles[0].appNames, HubAppNames(
            name: "TMU", defaultName: "TrackMania United Forever", custom: true,
            game: "TMU.app", settings: "TMU Settings.app"))
        XCTAssertNil(snapshot.titles[1].appNames)
        XCTAssertNil(snapshot.titles[2].appNames)
        XCTAssertNil(snapshot.titles[3].appNames)
        XCTAssertEqual(snapshot.titles.count, 4)
    }
}
