import AppKit
import Combine
import Foundation

@MainActor
final class HubViewModel: ObservableObject {
    @Published private(set) var snapshot: HubSnapshot?
    @Published private(set) var message: String?
    @Published private(set) var log = ""
    @Published private(set) var runningAction: String?
    @Published var selectedTitleID: String?

    private let backend: HubBackend?

    init(backend: HubBackend? = nil) {
        if let backend {
            self.backend = backend
        } else {
            do {
                self.backend = try HubBackend.bundled()
            } catch {
                self.backend = nil
                message = error.localizedDescription
            }
        }
    }

    func reload() {
        guard let backend else { return }
        message = nil
        Task {
            do {
                let snapshot = try await backend.snapshot()
                self.snapshot = snapshot
                if selectedTitleID == nil {
                    selectedTitleID = snapshot.titles.first?.id
                }
            } catch {
                message = error.localizedDescription
            }
        }
    }

    func run(_ action: HubAction, for title: HubTitle, source: URL? = nil) {
        guard let backend, runningAction == nil else { return }
        runningAction = action.id
        message = nil
        appendLog("$ tmnf hub run \(title.id) \(action.id)\n")
        Task {
            defer { runningAction = nil }
            do {
                let result = try await backend.run(titleID: title.id, actionID: action.id, source: source)
                appendLog(result.output)
                message = "\(action.label) completed."
                reload()
            } catch {
                appendLog(error.localizedDescription + "\n")
                message = error.localizedDescription
                reload()
            }
        }
    }

    func chooseImportSource(for title: HubTitle, action: HubAction) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        panel.message = "Choose a retail TrackMania disk image or setup executable"
        panel.prompt = "Import"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        run(action, for: title, source: url)
    }

    func openApp(at path: String) {
        let url = URL(fileURLWithPath: path)
        guard FileManager.default.fileExists(atPath: url.path) else {
            message = "This app has not been installed yet."
            return
        }
        guard NSWorkspace.shared.open(url) else {
            message = "macOS could not open \(url.lastPathComponent)."
            return
        }
    }

    func appendLog(_ text: String) {
        guard !text.isEmpty else { return }
        log += text
        if log.count > 60_000 {
            log = String(log.suffix(60_000))
        }
    }
}
