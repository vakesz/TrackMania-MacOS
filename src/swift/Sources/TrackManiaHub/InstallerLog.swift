import Foundation

/// Console text of an installer run. Handles carriage returns the way a terminal does,
/// so curl's `--progress-bar` updates overwrite one line instead of piling up.
struct InstallerLog: Equatable, Sendable {
    static let limit = 60_000

    /// Finished lines, each ending in "\n".
    private(set) var committed = ""
    /// The line being written. A bare "\r" rewinds it.
    private(set) var current = ""
    private var committedCount = 0
    private var currentCount = 0
    private var pendingReturn = false

    var text: String { committed + current }
    var isEmpty: Bool { committed.isEmpty && current.isEmpty }

    /// The line being written, or the last finished line when none is in progress.
    var lastLine: String {
        if !current.isEmpty { return current }
        let body = committed.dropLast()
        guard let newline = body.lastIndex(of: "\n") else { return String(body) }
        return String(body[body.index(after: newline)...])
    }

    mutating func append(_ chunk: String) {
        for scalar in chunk.unicodeScalars {
            if pendingReturn {
                pendingReturn = false
                if scalar == "\n" {
                    commitLine()
                    continue
                }
                current = ""
                currentCount = 0
            }
            switch scalar {
            case "\r": pendingReturn = true
            case "\n": commitLine()
            default:
                current.unicodeScalars.append(scalar)
                currentCount += 1
            }
        }
        trim()
    }

    mutating func reset() { self = InstallerLog() }

    private mutating func commitLine() {
        committed += current
        committed += "\n"
        committedCount += currentCount + 1
        current = ""
        currentCount = 0
    }

    private mutating func trim() {
        while committedCount + currentCount > Self.limit, let newline = committed.firstIndex(of: "\n") {
            let removed = committed[...newline]
            committedCount -= removed.unicodeScalars.count
            committed.removeSubrange(...newline)
        }
        if committedCount + currentCount > Self.limit {
            let excess = committedCount + currentCount - Self.limit
            current = String(String.UnicodeScalarView(current.unicodeScalars.dropFirst(excess)))
            currentCount -= excess
        }
    }
}

/// What the user needs to see about a running or finished installer, derived from its log.
struct InstallerProgress: Equatable, Sendable {
    var fraction: Double?
    var stage: String?
    var error: String?

    static func parse(_ log: InstallerLog) -> InstallerProgress {
        var result = InstallerProgress()
        result.fraction = percent(in: log.lastLine)
        for line in log.text.split(whereSeparator: \.isNewline).reversed() {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if result.error == nil, trimmed.hasPrefix("error: ") {
                result.error = String(trimmed.dropFirst("error: ".count))
            }
            if result.stage == nil, !isNoise(trimmed), !isRuntimeChatter(line) {
                result.stage = trimmed
            }
            if result.error != nil, result.stage != nil { break }
        }
        return result
    }

    /// A trailing `NN.N%`, as a fraction in 0...1.
    static func percent(in line: String) -> Double? {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard trimmed.hasSuffix("%") else { return nil }
        let digits = trimmed.dropLast().reversed().prefix { $0.isNumber || $0 == "." }
        guard !digits.isEmpty, let value = Double(String(digits.reversed())), value.isFinite else { return nil }
        return min(max(value / 100, 0), 1)
    }

    /// Wine and MoltenVK write diagnostics into the same stream: indented
    /// continuation lines, `[mvk-info]` blocks and `0024:fixme:ole:...` channels.
    /// The installer's own steps are unindented sentences.
    static func isRuntimeChatter(_ line: Substring) -> Bool {
        if let first = line.first, first == " " || first == "\t" { return true }
        if line.hasPrefix("[mvk-") { return true }
        let parts = line.split(separator: ":", maxSplits: 2, omittingEmptySubsequences: false)
        if parts.count == 3, parts[0].count == 4, parts[0].allSatisfy(\.isHexDigit),
           ["fixme", "err", "warn", "trace"].contains(parts[1]) {
            return true
        }
        return false
    }

    /// Blank lines, the echoed command and curl's bar are not a stage.
    private static func isNoise(_ line: String) -> Bool {
        if line.isEmpty || line.hasPrefix("$ ") { return true }
        let bar = Set("#=>-.O_ \t")
        var body = Substring(line)
        if percent(in: line) != nil {
            body = body.dropLast()
            while let last = body.last, last.isNumber || last == "." { body = body.dropLast() }
        }
        return body.allSatisfy { bar.contains($0) }
    }
}
