import Foundation

/// Names of earlier releases the hub still recognises. The Python and shell sides keep
/// theirs in trackmania_mac/legacy.py and src/shell/lib/legacy.sh.
enum HubLegacy {
    /// The hub's bundle identifier before this release, and so its old settings domain.
    static let bundleIdentifier = "local.tmnf.hub"
}

extension HubDefaults {
    static let keys = [checkForUpdates, didShowWelcome, dismissedUpdate]

    /// Copies the hub's own settings from its earlier bundle identifier's domain, once:
    /// only while none of them is set under the current identifier. The old domain is
    /// only read. The app is not sandboxed, so `persistentDomain(forName:)` sees it.
    static func adoptLegacySettings(into defaults: UserDefaults, from legacy: [String: Any]?) {
        guard let legacy, keys.allSatisfy({ defaults.object(forKey: $0) == nil }) else { return }
        for key in keys {
            if let value = legacy[key] {
                defaults.set(value, forKey: key)
            }
        }
    }
}
