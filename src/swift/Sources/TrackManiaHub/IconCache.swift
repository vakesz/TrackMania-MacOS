import AppKit
import SwiftUI

/// Loads title icons (.icns, .png, .svg) and keeps decoded images keyed by path and mtime,
/// so view refreshes do not hit the disk and a replaced file is picked up.
@MainActor
final class IconCache {
    static let shared = IconCache()

    private let cache = NSCache<NSString, NSImage>()
    private(set) var loadCount = 0

    func image(atPath path: String) -> NSImage? {
        let modified = (try? URL(fileURLWithPath: path).resourceValues(forKeys: [.contentModificationDateKey]))?
            .contentModificationDate?.timeIntervalSince1970
        guard let modified else { return nil }
        let key = "\(path)|\(modified)" as NSString
        if let cached = cache.object(forKey: key) { return cached }
        loadCount += 1
        // NSImage decodes SVG natively on recent macOS; a nil or zero-sized result falls back to the tile.
        guard let image = NSImage(contentsOfFile: path), image.size.width > 0, image.size.height > 0 else {
            return nil
        }
        cache.setObject(image, forKey: key)
        return image
    }
}

enum FallbackIcon {
    /// Stable across launches (unlike `hashValue`), so a title keeps its color.
    static func hue(for id: String) -> Double {
        var hash: UInt32 = 2_166_136_261
        for byte in id.utf8 {
            hash = (hash ^ UInt32(byte)) &* 16_777_619
        }
        return Double(hash % 360) / 360
    }

    static func label(for title: HubTitle) -> String {
        if let short = title.shortName, !short.isEmpty { return short }
        return String(title.id.prefix(3)).uppercased()
    }

    static func color(for title: HubTitle) -> Color {
        Color(hue: hue(for: title.id), saturation: 0.65, brightness: 0.55)
    }
}
