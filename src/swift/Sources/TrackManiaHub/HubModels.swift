import Foundation

struct HubSnapshot: Codable, Equatable {
    let schema: Int
    let titles: [HubTitle]
    let error: String?
}

struct HubTitle: Codable, Equatable, Identifiable {
    let id: String
    let name: String
    let shortName: String?
    let status: String
    let notes: String
    let engine: String?
    let kind: String?
    let icon: String?
    let error: String?
    let requiresSerial: Bool?
    let state: HubInstallationState
    let apps: HubApps
    let actions: [HubAction]
}

struct HubProbe: Codable, Equatable {
    let ready: Bool
    let note: String?
}

struct HubInstallationState: Codable, Equatable {
    let runtime: HubProbe?
    let prefix: HubProbe?
    let directx: HubProbe?
    let renderer: HubProbe?
    let game: HubProbe?
    let gameApp: HubProbe?
    let settingsApp: HubProbe?
}

extension HubTitle {
    var summary: [(String, String)] {
        [
            ("Runtime", state.runtime),
            ("Wine prefix", state.prefix),
            ("DirectX", state.directx),
            ("Renderer", state.renderer),
            ("Game files", state.game),
            ("Game app", state.gameApp),
            ("Settings app", state.settingsApp),
        ].map { label, probe in
            (label, probe?.ready == true ? "ready" : "missing")
        }
    }
}

struct HubApps: Codable, Equatable {
    let game: String
    let settings: String
}

struct HubAction: Codable, Equatable, Identifiable {
    let id: String
    let label: String
    let primary: Bool
    let kind: String
}
