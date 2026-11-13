import Foundation

// MARK: Storage

/// One download or runtime the backend lists. Swift only displays it; the backend deletes.

struct HubStorageEntry: Decodable, Equatable {
    /// Display name: a download's label or a runtime's version.
    let name: String
    let path: String
    /// Nil when the backend could not measure it.
    let bytes: Int64?

    init(name: String, path: String, bytes: Int64? = nil) {
        self.name = name
        self.path = path
        self.bytes = bytes
    }

    private enum CodingKeys: String, CodingKey { case label, version, path, bytes }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let label = (try? c.decodeIfPresent(String.self, forKey: .label)) ?? nil
        let version = (try? c.decodeIfPresent(String.self, forKey: .version)) ?? nil
        guard let name = label ?? version else {
            throw DecodingError.keyNotFound(
                CodingKeys.label, .init(codingPath: c.codingPath, debugDescription: "no label or version"))
        }
        self.name = name
        path = (try? c.decodeIfPresent(String.self, forKey: .path)) ?? ""
        bytes = (try? c.decodeIfPresent(Int64.self, forKey: .bytes)) ?? nil
    }
}

struct HubStorageDownloads: Decodable, Equatable {
    let bytes: Int64?
    let items: [HubStorageEntry]

    init(bytes: Int64? = nil, items: [HubStorageEntry] = []) {
        self.bytes = bytes
        self.items = items
    }

    private enum CodingKeys: String, CodingKey { case bytes, items }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        bytes = (try? c.decodeIfPresent(Int64.self, forKey: .bytes)) ?? nil
        items = c.lossyArray(HubStorageEntry.self, forKey: .items)
    }
}

struct HubStorageRuntimes: Decodable, Equatable {
    let current: String
    /// Only the runtimes that can be removed.
    let items: [HubStorageEntry]
    let inUse: [String]
    /// Why nothing can be removed right now.
    let blocked: String?

    init(current: String = "", items: [HubStorageEntry] = [], inUse: [String] = [], blocked: String? = nil) {
        self.current = current
        self.items = items
        self.inUse = inUse
        self.blocked = blocked
    }

    private enum CodingKeys: String, CodingKey { case current, items, inUse, blocked }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        current = (try? c.decodeIfPresent(String.self, forKey: .current)) ?? ""
        items = c.lossyArray(HubStorageEntry.self, forKey: .items)
        inUse = ((try? c.decodeIfPresent([String].self, forKey: .inUse)) ?? nil) ?? []
        blocked = (try? c.decodeIfPresent(String.self, forKey: .blocked)) ?? nil
    }

    /// Removing is offered only when something can go and nothing blocks it.
    var canRemove: Bool { !items.isEmpty && blocked == nil }

    /// Nil when any removable runtime could not be measured.
    var removableBytes: Int64? {
        let sizes = items.map(\.bytes)
        return sizes.contains(where: { $0 == nil }) ? nil : sizes.reduce(0) { $0 + ($1 ?? 0) }
    }
}

struct HubStorageLogs: Decodable, Equatable {
    let bytes: Int64?
    let path: String

    init(bytes: Int64? = nil, path: String = "") {
        self.bytes = bytes
        self.path = path
    }

    private enum CodingKeys: String, CodingKey { case bytes, path }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        bytes = (try? c.decodeIfPresent(Int64.self, forKey: .bytes)) ?? nil
        path = (try? c.decodeIfPresent(String.self, forKey: .path)) ?? ""
    }
}

/// Disk use the backend reports for the Storage settings. Every field is optional so a bad
/// one costs only itself.
struct HubStorage: Decodable, Equatable {
    let downloads: HubStorageDownloads?
    let runtimes: HubStorageRuntimes?
    let logs: HubStorageLogs?
    let error: String?

    init(downloads: HubStorageDownloads? = nil, runtimes: HubStorageRuntimes? = nil, logs: HubStorageLogs? = nil, error: String? = nil) {
        self.downloads = downloads
        self.runtimes = runtimes
        self.logs = logs
        self.error = error
    }

    private enum CodingKeys: String, CodingKey { case downloads, runtimes, logs, error }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        downloads = (try? c.decodeIfPresent(HubStorageDownloads.self, forKey: .downloads)) ?? nil
        runtimes = (try? c.decodeIfPresent(HubStorageRuntimes.self, forKey: .runtimes)) ?? nil
        logs = (try? c.decodeIfPresent(HubStorageLogs.self, forKey: .logs)) ?? nil
        error = (try? c.decodeIfPresent(String.self, forKey: .error)) ?? nil
    }

    /// "Size unknown" for a size the backend could not measure.
    static func sizeText(_ bytes: Int64?) -> String {
        guard let bytes else { return String(localized: "Size unknown") }
        return ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)
    }
}

/// What the game starts with, as the backend reports it. Changed only through
/// `hub run TITLE set-defaults`.
struct HubLaunchOptions: Decodable, Equatable {
    static let fullscreen = "fullscreen"

    /// "fullscreen" or "<W>x<H>".
    let windowSize: String
    let audio: Bool
    let x87: Bool
    let renderer: String
    let editable: Bool
    let reason: String?

    init(windowSize: String = HubLaunchOptions.fullscreen, audio: Bool = true, x87: Bool = true,
         renderer: String = "d9vk", editable: Bool = true, reason: String? = nil) {
        self.windowSize = windowSize
        self.audio = audio
        self.x87 = x87
        self.renderer = renderer
        self.editable = editable
        self.reason = reason
    }

    private enum CodingKeys: String, CodingKey { case windowSize, audio, x87, renderer, editable, reason }

    /// Throws (so the title reports no options) when a value the controls need is unreadable;
    /// the rest of the snapshot is unaffected.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        windowSize = try c.decode(String.self, forKey: .windowSize)
        audio = try Self.onOff(c, .audio)
        x87 = try Self.onOff(c, .x87)
        renderer = (try? c.decodeIfPresent(String.self, forKey: .renderer)) ?? ""
        editable = (try? c.decodeIfPresent(Bool.self, forKey: .editable)) ?? false
        reason = (try? c.decodeIfPresent(String.self, forKey: .reason)) ?? nil
    }

    private static func onOff(_ c: KeyedDecodingContainer<CodingKeys>, _ key: CodingKeys) throws -> Bool {
        switch try c.decode(String.self, forKey: key) {
        case "on": return true
        case "off": return false
        default:
            throw DecodingError.dataCorruptedError(forKey: key, in: c, debugDescription: "expected on or off")
        }
    }

    var isFullscreen: Bool { windowSize == Self.fullscreen }

    static func parseSize(_ text: String) -> (width: Int, height: Int)? {
        let parts = text.split(separator: "x", omittingEmptySubsequences: false)
        guard parts.count == 2, let width = Int(parts[0]), let height = Int(parts[1]),
              width > 0, height > 0 else { return nil }
        return (width, height)
    }
}

/// The edit state of the Game defaults controls, kept apart from the view so the rules are testable.
struct GameDefaultsDraft: Equatable {
    var fullscreen: Bool
    var width: String
    var height: String
    var audio: Bool
    var x87: Bool

    init(_ options: HubLaunchOptions) {
        fullscreen = options.isFullscreen
        let size = HubLaunchOptions.parseSize(options.windowSize)
        width = size.map { String($0.width) } ?? ""
        height = size.map { String($0.height) } ?? ""
        audio = options.audio
        x87 = options.x87
    }

    /// The `--window-size` value, or nil while Window is chosen and the fields are not positive integers.
    var windowSize: String? {
        if fullscreen { return HubLaunchOptions.fullscreen }
        guard let size = HubLaunchOptions.parseSize("\(width.trimmed)x\(height.trimmed)") else { return nil }
        return "\(size.width)x\(size.height)"
    }

    var isValid: Bool { windowSize != nil }

    /// Only the values that differ from `options`; empty when nothing changed or the size is invalid.
    func changes(from options: HubLaunchOptions) -> GameDefaultsChanges {
        guard let windowSize else { return GameDefaultsChanges() }
        return GameDefaultsChanges(windowSize: windowSize == options.windowSize ? nil : windowSize,
                                   audio: audio == options.audio ? nil : audio,
                                   x87: x87 == options.x87 ? nil : x87)
    }
}

private extension String {
    var trimmed: String { trimmingCharacters(in: .whitespaces) }
}

struct GameDefaultsChanges: Equatable {
    var windowSize: String?
    var audio: Bool?
    var x87: Bool?

    var isEmpty: Bool { windowSize == nil && audio == nil && x87 == nil }
}

/// When the Settings controls must be disabled; the view only reads these.
enum SettingsGate {
    /// Why the Game defaults of `title` cannot be changed now, or nil when they can.
    static func gameDefaultsLock(_ options: HubLaunchOptions, sessionActive: Bool) -> String? {
        if sessionActive { return String(localized: "Quit the game to change its defaults.") }
        if !options.editable {
            return options.reason ?? String(localized: "These defaults cannot be changed now.")
        }
        return nil
    }

    static func canApply(_ draft: GameDefaultsDraft, options: HubLaunchOptions, sessionActive: Bool,
                         busy: Bool) -> Bool {
        !busy && gameDefaultsLock(options, sessionActive: sessionActive) == nil
            && !draft.changes(from: options).isEmpty
    }

    static func canRemoveRuntimes(_ runtimes: HubStorageRuntimes?, busy: Bool) -> Bool {
        !busy && runtimes?.canRemove == true
    }

    static func canClearDownloads(_ downloads: HubStorageDownloads?, busy: Bool) -> Bool {
        !busy && downloads != nil
    }

    static func canClearLogs(_ logs: HubStorageLogs?, busy: Bool) -> Bool {
        !busy && logs != nil
    }
}

extension HubSnapshot {
    /// Installed titles that report launch options, for the Game defaults picker.
    var gameDefaultTitles: [HubTitle] {
        titles.sidebarOrder.filter { $0.isInstalled && $0.launchOptions != nil }
    }

    /// Titles that can write a diagnostics report.
    var diagnosticsTitles: [HubTitle] {
        titles.sidebarOrder.filter { $0.diagnosticsAction != nil }
    }
}

extension HubTitle {
    var diagnosticsAction: HubAction? { actions.first { $0.id == "diagnostics" } }
}
