import Foundation

/// A string-backed value that never fails to decode: unrecognised raw values land in `unknown`.
protocol TolerantStringEnum: RawRepresentable, Codable, Equatable, Sendable where RawValue == String {
    static var known: [Self] { get }
    init(unknown raw: String)
}

extension TolerantStringEnum {
    static func parse(_ raw: String) -> Self {
        known.first(where: { $0.rawValue == raw }) ?? Self(unknown: raw)
    }

    /// Never returns nil: unrecognised values become `unknown`.
    init?(rawValue: String) {
        self = Self.parse(rawValue)
    }

    init(from decoder: Decoder) throws {
        self = Self.parse(try decoder.singleValueContainer().decode(String.self))
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(rawValue)
    }
}

enum TitleStatus: TolerantStringEnum {
    case supported, provisional, unknown(String)
    static let known: [TitleStatus] = [.supported, .provisional]
    init(unknown raw: String) { self = .unknown(raw) }
    var rawValue: String {
        switch self {
        case .supported: "supported"
        case .provisional: "provisional"
        case let .unknown(raw): raw
        }
    }
}

enum InstallerKind: TolerantStringEnum {
    case online, onlineRetail, onlineTrial, steamCopy, diskImage, unknown(String)
    static let known: [InstallerKind] = [.online, .onlineRetail, .onlineTrial, .steamCopy, .diskImage]
    init(unknown raw: String) { self = .unknown(raw) }
    var rawValue: String {
        switch self {
        case .online: "online"
        case .onlineRetail: "online-retail"
        case .onlineTrial: "online-trial"
        case .steamCopy: "steam-copy"
        case .diskImage: "disk-image"
        case let .unknown(raw): raw
        }
    }
}

enum ActionKind: TolerantStringEnum {
    case install, `import`, apps, verify, build, maintenance, destructive, unknown(String)
    static let known: [ActionKind] = [.install, .import, .apps, .verify, .build, .maintenance, .destructive]
    init(unknown raw: String) { self = .unknown(raw) }
    var rawValue: String {
        switch self {
        case .install: "install"
        case .import: "import"
        case .apps: "apps"
        case .verify: "verify"
        case .build: "build"
        case .maintenance: "maintenance"
        case .destructive: "destructive"
        case let .unknown(raw): raw
        }
    }
}

enum IconSource: TolerantStringEnum {
    case game, bundled, unknown(String)
    static let known: [IconSource] = [.game, .bundled]
    init(unknown raw: String) { self = .unknown(raw) }
    var rawValue: String {
        switch self {
        case .game: "game"
        case .bundled: "bundled"
        case let .unknown(raw): raw
        }
    }
}

/// Decodes one array element, recording the failure instead of aborting the whole array.
private struct Lossy<Value: Decodable>: Decodable {
    let value: Value?
    let error: String?

    init(from decoder: Decoder) throws {
        do {
            value = try Value(from: decoder)
            error = nil
        } catch {
            value = nil
            self.error = Self.describe(error)
        }
    }

    private static func describe(_ error: Error) -> String {
        if case let DecodingError.keyNotFound(key, _) = error { return "missing \(key.stringValue)" }
        if case let DecodingError.typeMismatch(_, context) = error { return context.debugDescription }
        return error.localizedDescription
    }
}

extension KeyedDecodingContainer {
    fileprivate func lossyArray<Value: Decodable>(_ type: Value.Type, forKey key: Key) -> [Value] {
        ((try? decodeIfPresent([Lossy<Value>].self, forKey: key)) ?? nil)?.compactMap(\.value) ?? []
    }
}

struct HubSnapshot: Decodable, Equatable {
    let schema: Int
    let titles: [HubTitle]
    let error: String?
    /// The project VERSION reported by the backend; absent from older backends.
    let hubVersion: String?
    /// Descriptions of title entries that could not be decoded and were skipped.
    let skippedTitles: [String]

    init(schema: Int, titles: [HubTitle], error: String?, hubVersion: String? = nil, skippedTitles: [String] = []) {
        self.hubVersion = hubVersion
        self.schema = schema
        self.titles = titles
        self.error = error
        self.skippedTitles = skippedTitles
    }

    private enum CodingKeys: String, CodingKey { case schema, titles, error, hubVersion }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schema = try container.decode(Int.self, forKey: .schema)
        error = try? container.decodeIfPresent(String.self, forKey: .error)
        hubVersion = try? container.decodeIfPresent(String.self, forKey: .hubVersion)
        let entries = (try? container.decodeIfPresent([Lossy<HubTitle>].self, forKey: .titles)) ?? nil
        titles = (entries ?? []).compactMap(\.value)
        skippedTitles = (entries ?? []).enumerated().compactMap { index, entry in
            entry.error.map { "entry \(index): \($0)" }
        }
    }
}

struct HubTitle: Decodable, Equatable, Identifiable {
    let id: String
    let name: String
    let shortName: String?
    let status: TitleStatus
    let notes: String
    let engine: String?
    let kind: InstallerKind?
    let icon: String?
    let iconSource: IconSource?
    let error: String?
    let requiresSerial: Bool?
    let sourceSize: Int64?
    let locations: HubLocations?
    let sessionActive: Bool
    let state: HubInstallationState
    let apps: HubApps
    let actions: [HubAction]

    private enum CodingKeys: String, CodingKey {
        case id, name, shortName, status, notes, engine, kind, icon, iconSource, error
        case requiresSerial, sourceSize, locations, sessionActive, state, apps, actions
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = (try? c.decodeIfPresent(String.self, forKey: .name)) ?? id
        shortName = try? c.decodeIfPresent(String.self, forKey: .shortName)
        status = (try? c.decodeIfPresent(TitleStatus.self, forKey: .status)) ?? .unknown("")
        notes = (try? c.decodeIfPresent(String.self, forKey: .notes)) ?? ""
        engine = try? c.decodeIfPresent(String.self, forKey: .engine)
        kind = try? c.decodeIfPresent(InstallerKind.self, forKey: .kind)
        icon = try? c.decodeIfPresent(String.self, forKey: .icon)
        iconSource = try? c.decodeIfPresent(IconSource.self, forKey: .iconSource)
        error = try? c.decodeIfPresent(String.self, forKey: .error)
        requiresSerial = try? c.decodeIfPresent(Bool.self, forKey: .requiresSerial)
        sourceSize = try? c.decodeIfPresent(Int64.self, forKey: .sourceSize)
        locations = try? c.decodeIfPresent(HubLocations.self, forKey: .locations)
        sessionActive = (try? c.decodeIfPresent(Bool.self, forKey: .sessionActive)) ?? false
        state = (try? c.decodeIfPresent(HubInstallationState.self, forKey: .state)) ?? .empty
        apps = (try? c.decodeIfPresent(HubApps.self, forKey: .apps)) ?? HubApps(game: "", settings: "")
        actions = c.lossyArray(HubAction.self, forKey: .actions)
    }

    init(id: String, name: String, shortName: String? = nil, status: TitleStatus = .supported,
         notes: String = "", engine: String? = nil, kind: InstallerKind? = nil, icon: String? = nil,
         iconSource: IconSource? = nil, error: String? = nil, requiresSerial: Bool? = nil,
         sourceSize: Int64? = nil, locations: HubLocations? = nil, sessionActive: Bool = false,
         state: HubInstallationState = .empty, apps: HubApps = HubApps(game: "", settings: ""),
         actions: [HubAction] = []) {
        self.id = id; self.name = name; self.shortName = shortName; self.status = status
        self.notes = notes; self.engine = engine; self.kind = kind; self.icon = icon
        self.iconSource = iconSource; self.error = error; self.requiresSerial = requiresSerial
        self.sourceSize = sourceSize; self.locations = locations; self.sessionActive = sessionActive
        self.state = state; self.apps = apps; self.actions = actions
    }
}

struct HubLocations: Decodable, Equatable {
    let root: String?
    let prefix: String?
    let gameDir: String?
    let userdata: String?
    let logs: String?

    init(root: String? = nil, prefix: String? = nil, gameDir: String? = nil,
         userdata: String? = nil, logs: String? = nil) {
        self.root = root; self.prefix = prefix; self.gameDir = gameDir
        self.userdata = userdata; self.logs = logs
    }

    private enum CodingKeys: String, CodingKey { case root, prefix, gameDir, userdata, logs }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        root = try? c.decodeIfPresent(String.self, forKey: .root)
        prefix = try? c.decodeIfPresent(String.self, forKey: .prefix)
        gameDir = try? c.decodeIfPresent(String.self, forKey: .gameDir)
        userdata = try? c.decodeIfPresent(String.self, forKey: .userdata)
        logs = try? c.decodeIfPresent(String.self, forKey: .logs)
    }
}

struct HubProbe: Decodable, Equatable {
    let ready: Bool
    let note: String?

    init(ready: Bool, note: String? = nil) {
        self.ready = ready
        self.note = note
    }

    private enum CodingKeys: String, CodingKey { case ready, note }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ready = (try? c.decodeIfPresent(Bool.self, forKey: .ready)) ?? false
        note = try? c.decodeIfPresent(String.self, forKey: .note)
    }
}

struct HubInstallationState: Decodable, Equatable {
    var runtime: HubProbe?
    var prefix: HubProbe?
    var directx: HubProbe?
    var renderer: HubProbe?
    var game: HubProbe?
    var gameApp: HubProbe?
    var settingsApp: HubProbe?

    static let empty = HubInstallationState()

    init() {}

    private enum CodingKeys: String, CodingKey {
        case runtime, prefix, directx, renderer, game, gameApp, settingsApp
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        runtime = try? c.decodeIfPresent(HubProbe.self, forKey: .runtime)
        prefix = try? c.decodeIfPresent(HubProbe.self, forKey: .prefix)
        directx = try? c.decodeIfPresent(HubProbe.self, forKey: .directx)
        renderer = try? c.decodeIfPresent(HubProbe.self, forKey: .renderer)
        game = try? c.decodeIfPresent(HubProbe.self, forKey: .game)
        gameApp = try? c.decodeIfPresent(HubProbe.self, forKey: .gameApp)
        settingsApp = try? c.decodeIfPresent(HubProbe.self, forKey: .settingsApp)
    }
}

struct InstallationItem: Equatable, Identifiable {
    let label: String
    let ready: Bool
    var symbol = "circle"
    var note: String?
    var id: String { label }
}

/// Where the installer gets the Wine runtime when it is not installed yet.
enum RuntimeSource: String {
    case download, build
}

extension HubTitle {
    /// Installing the runtime is slow either way, so the user picks download or build first.
    func needsRuntimeChoice(for action: HubAction) -> Bool {
        (action.id == "install" || action.id == "install-platform") && state.runtime?.ready != true
    }

    var summary: [InstallationItem] {
        [
            item("Runtime", "shippingbox", state.runtime),
            item("Wine prefix", "folder", state.prefix),
            item("DirectX", "cube", state.directx),
            item("Renderer", "display", state.renderer),
            item("Game files", "gamecontroller", state.game),
            item("Game app", "play.rectangle", state.gameApp),
            item("Settings app", "gearshape", state.settingsApp),
        ]
    }

    private func item(_ label: String, _ symbol: String, _ probe: HubProbe?) -> InstallationItem {
        let note = probe?.note.flatMap { $0.isEmpty ? nil : $0 }
        return InstallationItem(label: label, ready: probe?.ready == true, symbol: symbol, note: note)
    }

    /// Sidebar fallback when the full name does not fit: "TrackMania X" becomes "TM X".
    var compactName: String {
        let prefix = "TrackMania "
        guard name.hasPrefix(prefix), name.count > prefix.count else { return name }
        return "TM " + name.dropFirst(prefix.count)
    }

    var isSupported: Bool { status == .supported }
    var supportLabel: String { isSupported ? "Supported" : "Provisional" }

    var availability: String {
        if error != nil { return "Needs attention" }
        if state.gameApp?.ready == true { return "Ready to play" }
        if state.game?.ready == true { return "Game files installed" }
        if state.prefix?.ready == true { return "Platform ready" }
        return "Not installed"
    }
}

struct HubApps: Decodable, Equatable {
    let game: String
    let settings: String

    init(game: String, settings: String) {
        self.game = game
        self.settings = settings
    }

    private enum CodingKeys: String, CodingKey { case game, settings }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        game = (try? c.decodeIfPresent(String.self, forKey: .game)) ?? ""
        settings = (try? c.decodeIfPresent(String.self, forKey: .settings)) ?? ""
    }
}

struct HubAction: Decodable, Equatable, Identifiable {
    let id: String
    let label: String
    let primary: Bool
    let kind: ActionKind

    init(id: String, label: String, primary: Bool = false, kind: ActionKind) {
        self.id = id
        self.label = label
        self.primary = primary
        self.kind = kind
    }

    private enum CodingKeys: String, CodingKey { case id, label, primary, kind }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        label = (try? c.decodeIfPresent(String.self, forKey: .label)) ?? id
        primary = (try? c.decodeIfPresent(Bool.self, forKey: .primary)) ?? false
        kind = (try? c.decodeIfPresent(ActionKind.self, forKey: .kind)) ?? .unknown("")
    }
}

enum PrimaryControl: Equatable {
    case play
    case action(HubAction)
}

struct ActionGroups: Equatable {
    var setup: [HubAction] = []
    var maintenance: [HubAction] = []
    var destructive: [HubAction] = []
}

extension HubTitle {
    /// Installed titles are those whose game files are in place.
    var isInstalled: Bool { state.game?.ready == true }

    /// Play when the game app is ready; otherwise the backend's primary (or first) action.
    var primaryControl: PrimaryControl? {
        if state.gameApp?.ready == true { return .play }
        return (actions.first(where: \.primary) ?? actions.first).map(PrimaryControl.action)
    }

    /// Everything that is not the prominent button, grouped for the More menu.
    var moreActionGroups: ActionGroups {
        var groups = ActionGroups()
        for action in actions {
            if case let .action(primary)? = primaryControl, primary.id == action.id { continue }
            switch action.kind {
            case .install, .import, .apps, .build, .unknown: groups.setup.append(action)
            case .verify, .maintenance: groups.maintenance.append(action)
            case .destructive: groups.destructive.append(action)
            }
        }
        return groups
    }

    var installerLabel: String? {
        switch kind {
        case .online: String(localized: "Online download")
        case .onlineRetail: String(localized: "Retail installer")
        case .onlineTrial: String(localized: "Trial")
        case .steamCopy: String(localized: "Steam copy")
        case .diskImage: String(localized: "Retail disk")
        case .unknown, nil: nil
        }
    }

    /// Where the title lives on disk, for display; the first location the backend reported.
    var installLocationPath: String? {
        locations?.gameDir ?? locations?.prefix ?? locations?.root
    }
}

extension Array where Element == HubTitle {
    /// Sidebar sections: installed titles first, then the rest.
    var sidebarSections: (installed: [HubTitle], available: [HubTitle]) {
        (filter(\.isInstalled), filter { !$0.isInstalled })
    }
}
