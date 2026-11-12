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
        let progress = parse("$ trackmania-mac hub run trackmania-mac install\nDownloading runtime.tar.xz\n#####   30.0%\r######  60.0%")
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

    func testEarlyErrorSurvivesLaterOutput() {
        var log = InstallerLog()
        log.append("Downloading a\nerror: checksum mismatch\n")
        // Enough later output that the text window has dropped the error line.
        for index in 0..<10_000 { log.append("step \(index)\n") }
        XCTAssertFalse(log.text.contains("error: "))
        let progress = InstallerProgress.parse(log)
        XCTAssertEqual(progress.error, "checksum mismatch")
        XCTAssertEqual(progress.stage, "step 9999")
    }

    func testLaterErrorReplacesEarlierOne() {
        XCTAssertEqual(parse("error: first\nstep\nerror: second\nstep\n").error, "second")
    }

    func testErrorAndStageInTheLineBeingWritten() {
        XCTAssertEqual(parse("Installing\nerror: half a li").error, "half a li")
        XCTAssertEqual(parse("Installing\nerror: half a li").stage, "error: half a li")
        XCTAssertEqual(parse("Installing\n[mvk-info] chatter").stage, "Installing")
    }

    func testResetForgetsErrorAndStage() {
        var log = InstallerLog()
        log.append("error: boom\n")
        log.reset()
        XCTAssertEqual(InstallerProgress.parse(log), InstallerProgress())
    }

    /// The previous implementation, which re-split the whole log on every call.
    private func reference(_ log: InstallerLog) -> InstallerProgress {
        var result = InstallerProgress()
        result.fraction = InstallerProgress.percent(in: log.lastLine)
        for line in log.text.split(whereSeparator: \.isNewline).reversed() {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if result.error == nil, trimmed.hasPrefix("error: ") {
                result.error = String(trimmed.dropFirst("error: ".count))
            }
            if result.stage == nil, !InstallerProgress.isNoise(trimmed), !InstallerProgress.isRuntimeChatter(line) {
                result.stage = trimmed
            }
            if result.error != nil, result.stage != nil { break }
        }
        return result
    }

    func testMatchesFullRescanForChunkedInput() {
        let transcript = "$ trackmania-mac hub run trackmania-mac install\nDownloading runtime.tar.xz\n"
            + "#####   30.0%\r######  60.0%\r#######  100.0%\n"
            + "[mvk-info] Created VkInstance\n\tVK_KHR_x v1\n0024:fixme:ole:Co stub\n"
            + "Installing TrackMania United Forever\n   \nDisk is 50%\nerror: nope\nafter error\r\nlast"
        for chunk in [1, 2, 3, 7, 50, transcript.count] {
            var log = InstallerLog()
            var rest = Substring(transcript)
            while !rest.isEmpty {
                log.append(String(rest.prefix(chunk)))
                rest = rest.dropFirst(chunk)
                XCTAssertEqual(InstallerProgress.parse(log), reference(log), "chunk \(chunk), after \(log.text.debugDescription)")
            }
        }
    }
}
