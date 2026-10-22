import AppKit
import Combine
import Foundation
import UniformTypeIdentifiers

@MainActor
final class HubViewModel: ObservableObject {
    @Published private(set) var snapshot: HubSnapshot?
    @Published private(set) var message: String?
    @Published private(set) var log = ""
    @Published private(set) var runningAction: String?
    @Published private(set) var runningTitleID: String?
    @Published private(set) var activityTitleID: String?
    private var activityToken: UUID?
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

    func reload(clearMessage: Bool = true) {
        guard let backend else { return }
        if clearMessage {
            message = nil
        }
        Task {
            do {
                let snapshot = try await backend.snapshot()
                self.snapshot = snapshot
                if let selectedTitleID,
                   snapshot.titles.contains(where: { $0.id == selectedTitleID }) {
                    // Keep a valid current selection across refreshes.
                } else {
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
        runningTitleID = title.id
        activityTitleID = title.id
        let token = UUID()
        activityToken = token
        message = nil
        log = ""
        appendLog("$ tmnf hub run \(title.id) \(action.id)\n")
        Task { [weak self] in
            guard let self else { return }
            defer {
                runningAction = nil
                runningTitleID = nil
            }
            do {
                _ = try await backend.run(titleID: title.id, actionID: action.id, source: source) { [weak self] chunk in
                    Task { @MainActor [weak self] in
                        guard self?.activityToken == token else { return }
                        self?.appendLog(chunk)
                    }
                }
                message = "\(action.label) completed."
                reload(clearMessage: false)
            } catch {
                if let hubError = error as? HubBackendError,
                   case .commandFailed = hubError {
                    message = "\(action.label) failed. See Installer details."
                } else {
                    appendLog(error.localizedDescription + "\n")
                    message = error.localizedDescription
                }
                reload(clearMessage: false)
            }
        }
    }

    func chooseImportSource(for title: HubTitle, action: HubAction) {
        let panel = NSOpenPanel()
        let isFolderImport = title.kind == "steam-copy"
        panel.canChooseDirectories = isFolderImport
        panel.canChooseFiles = !isFolderImport
        if !isFolderImport, let executable = UTType(filenameExtension: "exe") {
            panel.allowedContentTypes = [executable]
        }
        panel.allowsMultipleSelection = false
        panel.message = isFolderImport
            ? "Choose the TrackMania game folder inside steamapps/common"
            : "Choose the retail Setup.exe. Copy it out of the disk image first."
        panel.prompt = isFolderImport ? "Copy Game" : "Run Installer"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        run(action, for: title, source: url)
    }

    func openApp(at path: String, for title: HubTitle) {
        activityTitleID = title.id
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

    func isRunning(_ title: HubTitle) -> Bool {
        runningTitleID == title.id
    }

    func hasActivity(for title: HubTitle) -> Bool {
        activityTitleID == title.id
    }

    func message(for title: HubTitle) -> String? {
        hasActivity(for: title) ? message : nil
    }

    func progressDescription(for title: HubTitle) -> String? {
        guard isRunning(title) else { return nil }
        return log.split(whereSeparator: \.isNewline).last.map(String.init)
    }
}
