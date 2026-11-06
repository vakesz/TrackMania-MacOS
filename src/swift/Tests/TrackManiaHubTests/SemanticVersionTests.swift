import XCTest
@testable import TrackManiaHub

final class SemanticVersionTests: XCTestCase {
    func testParsesWithAndWithoutPrefix() {
        XCTAssertEqual(SemanticVersion("v0.4.1"), SemanticVersion(0, 4, 1))
        XCTAssertEqual(SemanticVersion("0.4.1"), SemanticVersion(0, 4, 1))
        XCTAssertEqual(SemanticVersion("1.2"), SemanticVersion(1, 2, 0))
        XCTAssertEqual(SemanticVersion(" v2.0.0\n"), SemanticVersion(2, 0, 0))
    }

    func testRejectsUnparseableTags() {
        for tag in ["", "v", "1", "1.2.3.4", "1.2.3-rc1", "v1.0.0+build", "abc", "1.x.0", "-1.0.0", "١.٢.٣"] {
            XCTAssertNil(SemanticVersion(tag), tag)
        }
    }

    func testOrdersNumerically() {
        XCTAssertLessThan(SemanticVersion(0, 4, 1), SemanticVersion(0, 4, 10))
        XCTAssertLessThan(SemanticVersion(0, 4, 10), SemanticVersion(0, 5, 0))
        XCTAssertLessThan(SemanticVersion(0, 9, 9), SemanticVersion(1, 0, 0))
        XCTAssertFalse(SemanticVersion(1, 0, 0) < SemanticVersion(1, 0, 0))
    }
}

@MainActor
final class UpdateCheckerTests: XCTestCase {
    private func release(_ tag: String, url: String = "https://github.com/vakesz/TrackMania-MacOS/releases/tag/x") -> Data {
        Data(#"{"tag_name":"\#(tag)","html_url":"\#(url)","extra":1}"#.utf8)
    }

    func testNewerReleaseIsReported() {
        let info = UpdateChecker.parse(release("v0.5.0"), statusCode: 200, current: SemanticVersion(0, 4, 1))
        XCTAssertEqual(info?.version, SemanticVersion(0, 5, 0))
        XCTAssertEqual(info?.tag, "v0.5.0")
    }

    func testSameOlderOrUnparseableGivesNoBanner() {
        let current = SemanticVersion(0, 4, 1)
        XCTAssertNil(UpdateChecker.parse(release("v0.4.1"), statusCode: 200, current: current))
        XCTAssertNil(UpdateChecker.parse(release("v0.3.0"), statusCode: 200, current: current))
        XCTAssertNil(UpdateChecker.parse(release("v0.9.0-rc1"), statusCode: 200, current: current))
        XCTAssertNil(UpdateChecker.parse(release("nightly"), statusCode: 200, current: current))
    }

    func testBadResponsesGiveNoBanner() {
        let current = SemanticVersion(0, 4, 1)
        XCTAssertNil(UpdateChecker.parse(release("v1.0.0"), statusCode: 404, current: current))
        XCTAssertNil(UpdateChecker.parse(Data("nope".utf8), statusCode: 200, current: current))
        XCTAssertNil(UpdateChecker.parse(release("v1.0.0", url: "https://evil.example/x"), statusCode: 200, current: current))
        XCTAssertNil(UpdateChecker.parse(release("v1.0.0", url: "http://github.com/x"), statusCode: 200, current: current))
    }

    func testRequestHeadersAndTimeout() {
        let request = UpdateChecker.request(currentVersion: "0.4.1")
        XCTAssertEqual(request.value(forHTTPHeaderField: "User-Agent"), "TrackMania-Mac-Hub/0.4.1")
        XCTAssertEqual(request.timeoutInterval, 10)
        XCTAssertEqual(request.url, UpdateChecker.endpoint)
    }

    func testDisabledOrUnknownVersionNeverFetches() async {
        let fetched = Counter()
        let checker = UpdateChecker { _ in fetched.bump(); throw URLError(.notConnectedToInternet) }
        checker.checkIfNeeded(currentVersion: "0.4.1", enabled: false)
        checker.checkIfNeeded(currentVersion: nil, enabled: true)
        checker.checkIfNeeded(currentVersion: "garbage", enabled: true)
        try? await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(fetched.value, 0)
    }

    func testFailureIsSilentAndCheckRunsOnce() async {
        let fetched = Counter()
        let checker = UpdateChecker { _ in fetched.bump(); throw URLError(.notConnectedToInternet) }
        checker.checkIfNeeded(currentVersion: "0.4.1", enabled: true)
        checker.checkIfNeeded(currentVersion: "0.4.1", enabled: true)
        try? await Task.sleep(for: .milliseconds(100))
        XCTAssertEqual(fetched.value, 1)
        XCTAssertNil(checker.available)
    }
}

private final class Counter: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0
    func bump() { lock.lock(); count += 1; lock.unlock() }
    var value: Int { lock.lock(); defer { lock.unlock() }; return count }
}
