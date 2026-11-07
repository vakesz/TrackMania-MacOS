import Foundation

/// A release version such as `0.4.1` or `v0.4.1`. Pre-release and build suffixes do not parse.
struct SemanticVersion: Comparable, Hashable, Sendable, CustomStringConvertible {
    let major: Int
    let minor: Int
    let patch: Int

    init(_ major: Int, _ minor: Int, _ patch: Int) {
        self.major = major; self.minor = minor; self.patch = patch
    }

    init?(_ string: String) {
        var text = string.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.hasPrefix("v") || text.hasPrefix("V") { text.removeFirst() }
        let parts = text.split(separator: ".", omittingEmptySubsequences: false)
        guard (2...3).contains(parts.count) else { return nil }
        let numbers = parts.compactMap { part in
            part.allSatisfy(\.isASCII) && part.allSatisfy(\.isNumber) ? Int(part) : nil
        }
        guard numbers.count == parts.count else { return nil }
        self.init(numbers[0], numbers[1], numbers.count > 2 ? numbers[2] : 0)
    }

    var description: String { "\(major).\(minor).\(patch)" }

    static func < (lhs: SemanticVersion, rhs: SemanticVersion) -> Bool {
        (lhs.major, lhs.minor, lhs.patch) < (rhs.major, rhs.minor, rhs.patch)
    }
}
