import XCTest
@testable import TrackManiaHub

@MainActor
final class SelectionRestoreTests: XCTestCase {
    private func suite() throws -> UserDefaults {
        let name = "SelectionRestoreTests-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        addTeardownBlock { defaults.removePersistentDomain(forName: name) }
        return defaults
    }

    private func snapshot() -> HubSnapshot {
        let installed = HubInstallationState(game: true)
        return HubSnapshot(schema: 3, titles: [
            HubTitle(id: "tmnf", name: "N"),
            HubTitle(id: "tmu", name: "U", state: installed)
        ], error: nil)
    }

    private func model(_ defaults: UserDefaults) -> HubViewModel {
        HubViewModel(backend: HubBackend(launcherURL: URL(fileURLWithPath: "/nonexistent")), defaults: defaults)
    }

    func testRestoresStoredTitle() throws {
        let defaults = try suite()
        defaults.set("tmnf", forKey: HubDefaults.selectedTitle)
        let model = model(defaults)
        model.apply(snapshot())
        XCTAssertEqual(model.selectedTitleID, "tmnf")
    }

    func testStoredTitleMissingFromSnapshotFallsBackToDefault() throws {
        let defaults = try suite()
        defaults.set("gone", forKey: HubDefaults.selectedTitle)
        let model = model(defaults)
        model.apply(snapshot())
        XCTAssertEqual(model.selectedTitleID, "tmu")
    }

    func testNothingStoredUsesDefaultAndStoresChanges() throws {
        let defaults = try suite()
        let model = model(defaults)
        model.apply(snapshot())
        XCTAssertEqual(model.selectedTitleID, "tmu")
        model.selectedTitleID = "tmnf"
        XCTAssertEqual(defaults.string(forKey: HubDefaults.selectedTitle), "tmnf")
    }

    func testSettingsPickerFallback() {
        let titles = [HubTitle(id: "tmnf", name: "N"), HubTitle(id: "tmu", name: "U")]
        XCTAssertEqual(GameDefaultsSection.pickerTitle(in: titles, selectedID: "tmnf", sidebarID: "tmu")?.id, "tmnf")
        XCTAssertEqual(GameDefaultsSection.pickerTitle(in: titles, selectedID: nil, sidebarID: "tmu")?.id, "tmu")
        XCTAssertEqual(GameDefaultsSection.pickerTitle(in: titles, selectedID: nil, sidebarID: "x")?.id, "tmnf")
        XCTAssertNil(GameDefaultsSection.pickerTitle(in: [], selectedID: nil, sidebarID: nil))
    }
}
