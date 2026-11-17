import XCTest
@testable import TrackManiaHub

@MainActor
final class ReportProblemTests: XCTestCase {
    private let base = "https://github.com/vakesz/TrackMania-MacOS/issues/new"
    private let diagnostics = HubAction(id: "diagnostics", label: "Diagnostics", kind: .maintenance)

    private func formLine(_ url: String) -> String { "Report form: \(url)\n" }

    func testAcceptsValidFormURL() {
        let url = "\(base)?template=bug_report.yml&version=0.6.1"
        XCTAssertEqual(HubViewModel.reportFormURL(in: "x\n" + formLine(url))?.absoluteString, url)
    }

    func testTakesLastFormLine() {
        let first = "\(base)?template=bug_report.yml&version=1"
        let last = "\(base)?template=bug_report.yml&version=2"
        XCTAssertEqual(HubViewModel.reportFormURL(in: formLine(first) + formLine(last))?.absoluteString, last)
    }

    func testRejectsUnexpectedURLs() {
        let rejected = [
            "http://github.com/vakesz/TrackMania-MacOS/issues/new?template=a",
            "https://example.com/vakesz/TrackMania-MacOS/issues/new?template=a",
            "https://github.com/other/repo/issues/new?template=a",
            "https://github.com/vakesz/TrackMania-MacOS/issues/new/extra?template=a",
            base,
            "not a url at all"
        ]
        for url in rejected {
            XCTAssertNil(HubViewModel.reportFormURL(in: formLine(url)), url)
        }
        XCTAssertNil(HubViewModel.reportFormURL(in: "nothing"))
    }

    func testReportPathWithBothLinesInEitherOrder() {
        let form = formLine("\(base)?template=bug_report.yml")
        let zip = "Diagnostics report: /tmp/d.zip\n"
        XCTAssertEqual(HubViewModel.reportPath(in: form + zip), "/tmp/d.zip")
        XCTAssertEqual(HubViewModel.reportPath(in: zip + form), "/tmp/d.zip")
        XCTAssertNotNil(HubViewModel.reportFormURL(in: zip + form))
    }

    func testReportableTitle() {
        let withAction = HubTitle(id: "a", name: "A", actions: [diagnostics])
        let without = HubTitle(id: "b", name: "B")
        let snapshot = HubSnapshot(schema: 3, titles: [withAction, without], error: nil)
        func record(_ id: String, _ outcome: RunOutcome?) -> RunRecord {
            RunRecord(titleID: id, actionID: "install", actionLabel: "Install", outcome: outcome)
        }
        let failed = RunOutcome.failed("boom")
        XCTAssertEqual(HubViewModel.reportableTitle(for: record("a", failed), in: snapshot, busy: false)?.id, "a")
        XCTAssertNil(HubViewModel.reportableTitle(for: record("b", failed), in: snapshot, busy: false))
        XCTAssertNil(HubViewModel.reportableTitle(for: record("a", failed), in: snapshot, busy: true))
        XCTAssertNil(HubViewModel.reportableTitle(for: record("a", nil), in: snapshot, busy: false))
        XCTAssertNil(HubViewModel.reportableTitle(for: record("a", .succeeded), in: snapshot, busy: false))
        XCTAssertNil(HubViewModel.reportableTitle(for: record("a", .cancelled), in: snapshot, busy: false))
        XCTAssertNil(HubViewModel.reportableTitle(for: record("zz", failed), in: snapshot, busy: false))
        XCTAssertNil(HubViewModel.reportableTitle(for: record("a", failed), in: nil, busy: false))
    }

    func testNewIssuePointsAtTemplate() {
        let parts = URLComponents(url: HubLinks.newIssue, resolvingAgainstBaseURL: false)
        XCTAssertEqual(parts?.path, HubLinks.repository.path + "/issues/new")
        XCTAssertEqual(parts?.queryItems?.first { $0.name == "template" }?.value, "bug_report.yml")
    }
}
