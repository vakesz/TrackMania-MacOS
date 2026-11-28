import XCTest
@testable import TrackManiaHub

@MainActor
final class TrackPackTests: XCTestCase {
    private func title(_ json: String) throws -> HubTitle {
        let data = Data(#"{"schema":3,"titles":[\#(json)]}"#.utf8)
        return try XCTUnwrap(HubBackend.decodeSnapshot(data).titles.first)
    }

    private let pack = HubTrackPack(label: "about 9,000 community tracks", size: 70_062_938)

    func testDecodesTrackPackOrNil() throws {
        let packed = try title(#"{"id":"x","trackPack":{"label":"about 9,000 community tracks","size":70062938}}"#)
        XCTAssertEqual(packed.trackPack, pack)
        XCTAssertNil(try title(#"{"id":"x","trackPack":null}"#).trackPack)
        XCTAssertNil(try title(#"{"id":"x"}"#).trackPack)
        XCTAssertNil(try title(#"{"id":"x","trackPack":{"label":"tracks"}}"#).trackPack)
        XCTAssertNil(try title(#"{"id":"x","trackPack":{"label":"tracks","size":"big"}}"#).trackPack)
    }

    func testCheckboxNamesTheTracksAndTheDownloadSize() {
        let size = ByteCountFormatter.string(fromByteCount: 70_062_938, countStyle: .file)
        XCTAssertEqual(pack.checkboxTitle, "Also install about 9,000 community tracks (\(size) download)")
    }

    func testRunArgumentsAppendTrackPackOnlyWhenChosen() {
        let source = URL(fileURLWithPath: "/Users/example/Sunrise.iso")
        XCTAssertEqual(
            HubBackend.runArguments(titleID: "tm-sunrise-extreme", actionID: "import", source: source, trackPack: true),
            ["hub", "run", "tm-sunrise-extreme", "import", "--source", source.path, "--track-pack", "on"])
        XCTAssertEqual(
            HubBackend.runArguments(titleID: "tm-sunrise-extreme", actionID: "import", source: source, trackPack: false),
            ["hub", "run", "tm-sunrise-extreme", "import", "--source", source.path, "--track-pack", "off"])
        XCTAssertEqual(HubBackend.runArguments(titleID: "tm-original", actionID: "import", source: source),
                       ["hub", "run", "tm-original", "import", "--source", source.path])
    }

    func testImportAsksOnlyForATitleWithTrackPack() {
        let model = HubViewModel(backend: nil)
        var asked: [String] = []
        var answer: Bool?
        model.confirmTrackPack = { title, _ in
            asked.append(title.id)
            return answer
        }
        XCTAssertEqual(model.importChoice(for: HubTitle(id: "tm-original", name: "O")), .run(trackPack: nil))
        XCTAssertEqual(asked, [])
        let packed = HubTitle(id: "tm-sunrise-extreme", name: "X", trackPack: pack)
        answer = true
        XCTAssertEqual(model.importChoice(for: packed), .run(trackPack: true))
        answer = false
        XCTAssertEqual(model.importChoice(for: packed), .run(trackPack: false))
        answer = nil
        XCTAssertEqual(model.importChoice(for: packed), .cancel)
        XCTAssertEqual(asked, ["tm-sunrise-extreme", "tm-sunrise-extreme", "tm-sunrise-extreme"])
    }
}
