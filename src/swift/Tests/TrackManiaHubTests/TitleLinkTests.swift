import XCTest
@testable import TrackManiaHub

final class TitleLinkTests: XCTestCase {
    private func title(_ json: String) throws -> HubTitle {
        let data = Data(#"{"schema":3,"titles":[\#(json)]}"#.utf8)
        return try XCTUnwrap(HubBackend.decodeSnapshot(data).titles.first)
    }

    func testDecodesLinkAndOpensOnlyHttps() throws {
        let linked = try title(#"{"id":"x","link":{"label":"Port","url":"https://example.test/port"}}"#)
        XCTAssertEqual(linked.link, HubLink(label: "Port", url: "https://example.test/port"))
        XCTAssertEqual(linked.link?.destination?.absoluteString, "https://example.test/port")
        XCTAssertNil(try title(#"{"id":"x","link":null}"#).link)
        XCTAssertNil(try title(#"{"id":"x"}"#).link)
        XCTAssertNil(try title(#"{"id":"x","link":{"label":"Port"}}"#).link)
        for url in ["http://example.test", "file:///etc/passwd", "javascript:alert(1)", "not a url"] {
            XCTAssertNil(HubLink(label: "x", url: url).destination, url)
        }
    }

    func testContributionNoteFollowsStatusOnly() {
        let statuses: [TitleStatus] = [.supported, .provisional, .unsupported, .unknown("beta"), .unknown("")]
        let notes = statuses.map { HubTitle(id: "tmnf", name: "X", status: $0).contributionNote }
        XCTAssertNil(notes[0])
        XCTAssertNil(notes[1])
        XCTAssertNotNil(notes[2])
        XCTAssertEqual(notes[2], notes[3])
        XCTAssertEqual(notes[2], notes[4])
        XCTAssertTrue(notes[2]?.contains("pull requests") == true)
    }
}
