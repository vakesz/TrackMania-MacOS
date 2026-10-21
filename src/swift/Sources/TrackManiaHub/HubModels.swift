import Foundation

struct HubSnapshot: Codable, Equatable {
    let schema: Int
    let titles: [HubTitle]
}

struct HubTitle: Codable, Equatable, Identifiable {
    let id: String
    let name: String
    let status: String
    let notes: String
    let state: HubState
    let apps: HubApps
    let actions: [HubAction]
}

struct HubState: Codable, Equatable {
    let runtime: String
    let prefix: String
    let directx: String
    let renderer: String
    let game: String
    let gameApp: String
    let settingsApp: String

    var summary: [(String, String)] {
        [
            ("Runtime", runtime),
            ("Wine prefix", prefix),
            ("DirectX", directx),
            ("Renderer", renderer),
            ("Game", game),
            ("Game app", gameApp),
            ("Settings app", settingsApp),
        ]
    }
}

struct HubApps: Codable, Equatable {
    let game: String
    let settings: String
}

struct HubAction: Codable, Equatable, Identifiable {
    let id: String
    let label: String
}
