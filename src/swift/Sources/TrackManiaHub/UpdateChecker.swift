import Foundation

struct UpdateInfo: Equatable, Sendable {
    let version: SemanticVersion
    let tag: String
    let url: URL
}

/// Looks for a newer GitHub release once per launch. Every failure is silent.
@MainActor
final class UpdateChecker: ObservableObject {
    typealias Fetch = @Sendable (URLRequest) async throws -> (Data, URLResponse)

    static let endpoint = URL(string: "https://api.github.com/repos/vakesz/TrackMania-MacOS/releases/latest")!

    @Published private(set) var available: UpdateInfo?
    private var didCheck = false
    private let fetch: Fetch

    init(fetch: @escaping Fetch = { try await URLSession.shared.data(for: $0) }) {
        self.fetch = fetch
    }

    static var bundleVersion: String? {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
    }

    static func request(currentVersion: String) -> URLRequest {
        var request = URLRequest(url: endpoint, timeoutInterval: 10)
        request.setValue("TrackMania-Mac-Hub/\(currentVersion)", forHTTPHeaderField: "User-Agent")
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        return request
    }

    /// Runs the check once, and only when enabled and the running version is known.
    func checkIfNeeded(currentVersion: String?, enabled: Bool) {
        guard enabled, !didCheck, let currentVersion, let current = SemanticVersion(currentVersion) else { return }
        didCheck = true
        let request = Self.request(currentVersion: currentVersion)
        let fetch = fetch
        Task { [weak self] in
            guard let (data, response) = try? await fetch(request),
                  let status = (response as? HTTPURLResponse)?.statusCode,
                  let info = Self.parse(data, statusCode: status, current: current) else { return }
            self?.available = info
        }
    }

    /// The release described by `data`, only if it is newer than `current`.
    nonisolated static func parse(_ data: Data, statusCode: Int, current: SemanticVersion) -> UpdateInfo? {
        struct Release: Decodable {
            let tag_name: String
            let html_url: String
        }
        guard (200..<300).contains(statusCode),
              let release = try? JSONDecoder().decode(Release.self, from: data),
              let version = SemanticVersion(release.tag_name), version > current,
              let url = URL(string: release.html_url), url.scheme == "https", url.host == "github.com"
        else { return nil }
        return UpdateInfo(version: version, tag: release.tag_name, url: url)
    }
}
