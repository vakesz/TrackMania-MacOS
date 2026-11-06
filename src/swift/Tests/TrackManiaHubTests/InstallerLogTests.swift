import XCTest
@testable import TrackManiaHub

final class InstallerLogTests: XCTestCase {
    func testBareReturnRewindsCurrentLine() {
        var log = InstallerLog()
        log.append("Downloading\n#  10.0%\r##  20.0%\r###  30.0%")
        XCTAssertEqual(log.text, "Downloading\n###  30.0%")
        XCTAssertEqual(log.lastLine, "###  30.0%")
    }

    func testReturnNewlineIsOneNewline() {
        var log = InstallerLog()
        log.append("one\r\ntwo\r\n")
        XCTAssertEqual(log.text, "one\ntwo\n")
        XCTAssertEqual(log.lastLine, "two")
    }

    func testReturnNewlineSplitAcrossChunks() {
        var log = InstallerLog()
        log.append("one\r")
        log.append("\ntwo")
        XCTAssertEqual(log.text, "one\ntwo")
    }

    func testManyProgressUpdatesStayOneLine() {
        var log = InstallerLog()
        for step in 0...100 { log.append("\r" + String(repeating: "#", count: step) + " \(step).0%") }
        XCTAssertEqual(log.text.filter { $0 == "\n" }.count, 0)
        XCTAssertEqual(log.lastLine.hasSuffix("100.0%"), true)
    }

    func testCapKeepsTailAndWholeLines() {
        var log = InstallerLog()
        for index in 0..<10_000 { log.append("line \(index)\n") }
        XCTAssertLessThanOrEqual(log.text.unicodeScalars.count, InstallerLog.limit)
        XCTAssertEqual(log.lastLine, "line 9999")
        XCTAssertTrue(log.text.hasPrefix("line "))
    }

    func testCapAppliesToOneHugeLine() {
        var log = InstallerLog()
        log.append(String(repeating: "x", count: 200_000))
        XCTAssertEqual(log.text.unicodeScalars.count, InstallerLog.limit)
    }

    func testEmptyLog() {
        let log = InstallerLog()
        XCTAssertTrue(log.isEmpty)
        XCTAssertEqual(log.lastLine, "")
    }
}

final class InstallerProgressTests: XCTestCase {
    private func parse(_ text: String) -> InstallerProgress {
        var log = InstallerLog()
        log.append(text)
        return InstallerProgress.parse(log)
    }

    func testFractionFromTrailingPercent() {
        XCTAssertEqual(parse("Downloading x\n######  45.5%").fraction ?? 0, 0.455, accuracy: 0.0001)
        XCTAssertEqual(parse("Downloading x\r 12.0%").fraction ?? 0, 0.12, accuracy: 0.0001)
    }

    func testNoFractionWithoutPercent() {
        XCTAssertNil(parse("Installing TrackMania United Forever").fraction)
        XCTAssertNil(parse("").fraction)
    }

    func testFractionIsClamped() {
        XCTAssertEqual(parse("250.0%").fraction, 1)
    }

    func testStageSkipsProgressBarsAndEchoedCommand() {
        let progress = parse("$ tmnf hub run tmnf install\nDownloading runtime.tar.xz\n#####   30.0%\r######  60.0%")
        XCTAssertEqual(progress.stage, "Downloading runtime.tar.xz")
    }

    func testStageSkipsWineAndMoltenVKChatter() {
        // Shape taken from a real TMU install log.
        var log = InstallerLog()
        log.append("Installing TrackMania United Forever\n"
            + "[mvk-info] Created VkInstance for Vulkan version 1.0.357, with 3 extensions:\n"
            + "\tVK_KHR_external_fence_capabilities v1\n"
            + "0024:fixme:ole:CoInitializeSecurity stub\n")
        XCTAssertEqual(InstallerProgress.parse(log).stage, "Installing TrackMania United Forever")
    }

    func testConfirmLabelDropsMenuEllipsis() {
        XCTAssertEqual(TitleDetailView.confirmLabel("Uninstall…"), "Uninstall")
        XCTAssertEqual(TitleDetailView.confirmLabel("Stop game"), "Stop game")
    }

    func testStageIsLatestPlainLine() {
        let progress = parse("Downloading a\n100.0%\nInstalling TrackMania United Forever\n")
        XCTAssertEqual(progress.stage, "Installing TrackMania United Forever")
    }

    func testErrorLineIsSurfaced() {
        let progress = parse("Downloading a\nerror: checksum mismatch\n")
        XCTAssertEqual(progress.error, "checksum mismatch")
        XCTAssertNil(parse("fine\n").error)
    }

    func testPlainTextEndingInPercentIsAStage() {
        XCTAssertEqual(parse("Disk is 50%").stage, "Disk is 50%")
    }
}
