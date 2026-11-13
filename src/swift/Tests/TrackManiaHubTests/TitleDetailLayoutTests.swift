import XCTest
@testable import TrackManiaHub

final class TitleDetailLayoutTests: XCTestCase {
    func testUnsupportedTitlesLeadWithTheirNotes() {
        XCTAssertFalse(HubTitle(id: "x", name: "X", status: .supported).showsNotesFirst)
        XCTAssertFalse(HubTitle(id: "x", name: "X", status: .provisional).showsNotesFirst)
        XCTAssertTrue(HubTitle(id: "x", name: "X", status: .unsupported).showsNotesFirst)
        XCTAssertTrue(HubTitle(id: "x", name: "X", status: .unknown("beta")).showsNotesFirst)
    }

    func testSetupStartsCollapsedOnlyWhenInstalledAndComplete() {
        XCTAssertFalse(HubTitle(id: "x", name: "X").setupStartsCollapsed)
        // Installed, but other steps are missing: the checklist stays open.
        var state = HubInstallationState(game: true, gameApp: true, settingsApp: true)
        XCTAssertFalse(HubTitle(id: "x", name: "X", state: state).setupStartsCollapsed)
        state.runtime = HubProbe(ready: true)
        state.prefix = HubProbe(ready: true)
        state.directx = HubProbe(ready: true)
        state.renderer = HubProbe(ready: true)
        XCTAssertTrue(HubTitle(id: "x", name: "X", state: state).setupStartsCollapsed)
        state.cdKey = HubProbe(ready: false, note: "CD key required")
        XCTAssertFalse(HubTitle(id: "x", name: "X", state: state).setupStartsCollapsed)
    }
}
