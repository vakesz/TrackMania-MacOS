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
    private var commandHandle: HubCommandHandle?
    private var reloadTask: Task<Void, Never>?
    private var reloadGeneration = 0
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
        // Only the latest reload may publish; an older one finishing late must not overwrite it.
        reloadTask?.cancel()
        reloadGeneration += 1
        let generation = reloadGeneration
        reloadTask = Task { [weak self] in
            do {
                let snapshot = try await backend.snapshot()
                guard let self, !Task.isCancelled, generation == reloadGeneration else { return }
                apply(snapshot)
            } catch HubBackendError.cancelled {
                return
            } catch {
                guard let self, !Task.isCancelled, generation == reloadGeneration else { return }
                // Keep any previous snapshot on screen; just report the problem.
                if snapshot != nil { activityTitleID = selectedTitleID }
                message = error.localizedDescription
            }
        }
    }

    private func apply(_ snapshot: HubSnapshot) {
        self.snapshot = snapshot
        if selectedTitleID == nil || !snapshot.titles.contains(where: { $0.id == selectedTitleID }) {
            selectedTitleID = snapshot.titles.first?.id
        }
        if !snapshot.skippedTitles.isEmpty {
            let count = snapshot.skippedTitles.count
            activityTitleID = selectedTitleID
            message = "\(count) title\(count == 1 ? "" : "s") could not be read and \(count == 1 ? "was" : "were") skipped."
        }
    }

    func run(_ action: HubAction, for title: HubTitle, source: URL? = nil, runtime: RuntimeSource? = nil) {
        guard let backend, runningAction == nil else { return }
        runningAction = action.id
        runningTitleID = title.id
        activityTitleID = title.id
        let token = UUID()
        activityToken = token
        let handle = HubCommandHandle()
        commandHandle = handle
        message = nil
        log = ""
        appendLog("$ tmnf hub run \(title.id) \(action.id)\(runtime.map { " --runtime \($0.rawValue)" } ?? "")\n")
        Task { [weak self] in
            guard let self else { return }
            // Every exit path, including cancellation, must leave the UI idle.
            defer {
                runningAction = nil
                runningTitleID = nil
                commandHandle = nil
            }
            do {
                _ = try await backend.run(titleID: title.id, actionID: action.id, source: source, runtime: runtime,
                                          handle: handle) { [weak self] chunk in
                    Task { @MainActor [weak self] in
                        guard self?.activityToken == token else { return }
                        self?.appendLog(chunk)
                    }
                }
                message = "\(action.label) completed."
                reload(clearMessage: false)
            } catch HubBackendError.cancelled {
                appendLog("Cancelled.\n")
                message = "\(action.label) cancelled."
                reload(clearMessage: false)
            } catch {
                if case HubBackendError.commandFailed = error {
                    message = "\(action.label) failed. See Installer details."
                } else {
                    appendLog(error.localizedDescription + "\n")
                    message = error.localizedDescription
                }
                reload(clearMessage: false)
            }
        }
    }

    /// Cancels the running action, if any. The run task resets the running state when the process ends.
    func cancelRunning() {
        commandHandle?.cancel()
        if runningAction != nil { message = "Cancelling…" }
    }

    func chooseImportSource(for title: HubTitle, action: HubAction) {
        let panel = NSOpenPanel()
        let isFolderImport = title.kind == .steamCopy
        panel.canChooseDirectories = isFolderImport
        panel.canChooseFiles = !isFolderImport
        if !isFolderImport, let executable = UTType(filenameExtension: "exe") {
            panel.allowedContentTypes = [executable]
        }
        panel.allowsMultipleSelection = false
        switch title.kind {
        case .steamCopy:
            panel.message = "Choose the TrackMania game folder inside steamapps/common"
            panel.prompt = "Copy Game"
        case .online, .onlineRetail, .onlineTrial, .diskImage, .unknown, nil:
            panel.message = "Choose the retail Setup.exe. Copy it out of the disk image first."
            panel.prompt = "Run Installer"
        }
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
