import AppKit
import Combine
import Foundation
import UniformTypeIdentifiers

enum RunOutcome: Equatable {
    case succeeded
    case failed(String)
    case cancelled
}

/// The most recent action run, shown in the activity card of its title.
struct RunRecord: Equatable {
    let titleID: String
    let actionID: String
    let actionLabel: String
    var outcome: RunOutcome?
    /// Path parsed from a diagnostics run's "Diagnostics report:" line.
    var reportPath: String?
}

@MainActor
final class HubViewModel: ObservableObject {
    /// Output is applied in batches so curl's progress bar cannot make the UI stutter.
    static let flushInterval: Duration = .milliseconds(100)
    static let reportPrefix = "Diagnostics report: "

    @Published private(set) var snapshot: HubSnapshot?
    @Published private(set) var message: String?
    @Published private(set) var log = InstallerLog()
    @Published private(set) var progress = InstallerProgress()
    @Published private(set) var logRevision = 0
    @Published private(set) var runningAction: String?
    @Published private(set) var runningTitleID: String?
    @Published private(set) var activityTitleID: String?
    @Published private(set) var lastRun: RunRecord?
    @Published private(set) var cancelling = false
    @Published var selectedTitleID: String?

    /// Called when a run ends and the UI is idle again (the quit flow waits on it).
    var onIdle: (() -> Void)?

    private var activityToken: UUID?
    private var commandHandle: HubCommandHandle?
    private var reloadTask: Task<Void, Never>?
    private var reloadGeneration = 0
    private var pendingOutput = ""
    private var flushTask: Task<Void, Never>?

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
            selectedTitleID = snapshot.titles.sidebarOrder.first?.id
        }
        if !snapshot.skippedTitles.isEmpty {
            let count = snapshot.skippedTitles.count
            activityTitleID = selectedTitleID
            message = count == 1
                ? String(localized: "1 title could not be read and was skipped.")
                : String(localized: "\(count) titles could not be read and were skipped.")
        }
    }

    func run(_ action: HubAction, for title: HubTitle, source: URL? = nil, runtime: RuntimeSource? = nil) {
        guard let backend, runningAction == nil else { return }
        if action.kind == .install || action.kind == .import { Notifier.requestAuthorizationOnce() }
        runningAction = action.id
        runningTitleID = title.id
        activityTitleID = title.id
        cancelling = false
        lastRun = RunRecord(titleID: title.id, actionID: action.id, actionLabel: action.label)
        let token = UUID()
        activityToken = token
        let handle = HubCommandHandle()
        commandHandle = handle
        message = nil
        resetLog()
        enqueue("$ tmnf hub run \(title.id) \(action.id)\(runtime.map { " --runtime \($0.rawValue)" } ?? "")\n")
        Task { [weak self] in
            guard let self else { return }
            var outcome = RunOutcome.succeeded
            var output = ""
            do {
                output = try await backend.run(titleID: title.id, actionID: action.id, source: source, runtime: runtime,
                                               handle: handle) { [weak self] chunk in
                    Task { @MainActor [weak self] in
                        guard self?.activityToken == token else { return }
                        self?.enqueue(chunk)
                    }
                }.output
            } catch HubBackendError.cancelled {
                outcome = .cancelled
            } catch HubBackendError.commandFailed(let result) {
                output = result.output
                outcome = .failed(Self.failureReason(in: output))
            } catch {
                outcome = .failed(error.localizedDescription)
                enqueue(error.localizedDescription + "\n")
            }
            finish(action: action, title: title, outcome: outcome, output: output)
        }
    }

    private func finish(action: HubAction, title: HubTitle, outcome: RunOutcome, output: String) {
        flushOutput()
        if outcome == .cancelled { enqueue("Cancelled.\n"); flushOutput() }
        lastRun?.outcome = outcome
        if outcome == .succeeded { lastRun?.reportPath = Self.reportPath(in: output) }
        // Every exit path, including cancellation, must leave the UI idle.
        runningAction = nil
        runningTitleID = nil
        commandHandle = nil
        cancelling = false
        switch outcome {
        case .succeeded: Notifier.notify(title: title.name, body: String(localized: "\(action.label) finished."))
        case .failed: Notifier.notify(title: title.name, body: String(localized: "\(action.label) failed."))
        case .cancelled: break
        }
        reload(clearMessage: false)
        onIdle?()
    }

    /// The installer's `error:` line, else the last line of output.
    static func failureReason(in output: String) -> String {
        var log = InstallerLog()
        log.append(output)
        let parsed = InstallerProgress.parse(log)
        return parsed.error ?? parsed.stage ?? String(localized: "The action failed.")
    }

    static func reportPath(in output: String) -> String? {
        output.split(whereSeparator: \.isNewline).reversed()
            .first { $0.hasPrefix(reportPrefix) }
            .map { String($0.dropFirst(reportPrefix.count)).trimmingCharacters(in: .whitespaces) }
    }

    /// Cancels the running action, if any. The run task resets the running state when the process ends.
    func cancelRunning() {
        guard runningAction != nil else { return }
        cancelling = true
        commandHandle?.cancel()
    }

    var runningActionLabel: String? {
        runningAction == nil ? nil : lastRun?.actionLabel
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
            panel.message = String(localized: "Choose the TrackMania game folder inside steamapps/common")
            panel.prompt = String(localized: "Copy Game")
        case .online, .onlineRetail, .onlineTrial, .diskImage, .unknown, nil:
            panel.message = String(localized: "Choose the retail Setup.exe. Copy it out of the disk image first.")
            panel.prompt = String(localized: "Run Installer")
        }
        guard panel.runModal() == .OK, let url = panel.url else { return }
        run(action, for: title, source: url)
    }

    func openApp(at path: String, for title: HubTitle) {
        activityTitleID = title.id
        let url = URL(fileURLWithPath: path)
        guard FileManager.default.fileExists(atPath: url.path) else {
            message = String(localized: "This app has not been installed yet.")
            return
        }
        guard NSWorkspace.shared.open(url) else {
            message = String(localized: "macOS could not open \(url.lastPathComponent).")
            return
        }
    }

    func reveal(path: String) {
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
    }

    func openLogsFolder() {
        let reported = snapshot?.titles.lazy.compactMap { $0.locations?.logs }.first
        let path = reported ?? (NSHomeDirectory() + "/Library/Logs/TMNF")
        guard FileManager.default.fileExists(atPath: path) else {
            message = String(localized: "There are no logs yet.")
            return
        }
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }

    // MARK: Output batching

    private func resetLog() {
        flushTask?.cancel()
        flushTask = nil
        pendingOutput = ""
        log.reset()
        progress = InstallerProgress()
        logRevision += 1
    }

    private func enqueue(_ chunk: String) {
        guard !chunk.isEmpty else { return }
        pendingOutput += chunk
        guard flushTask == nil else { return }
        flushTask = Task { [weak self] in
            try? await Task.sleep(for: Self.flushInterval)
            guard !Task.isCancelled else { return }
            self?.flushOutput()
        }
    }

    func flushOutput() {
        flushTask?.cancel()
        flushTask = nil
        guard !pendingOutput.isEmpty else { return }
        log.append(pendingOutput)
        pendingOutput = ""
        progress = InstallerProgress.parse(log)
        logRevision += 1
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

    func run(for title: HubTitle) -> RunRecord? {
        lastRun?.titleID == title.id ? lastRun : nil
    }
}
