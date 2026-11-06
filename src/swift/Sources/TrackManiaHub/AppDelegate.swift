import AppKit

@MainActor
final class HubAppDelegate: NSObject, NSApplicationDelegate {
    static let quitTimeout: Duration = .seconds(5)

    /// Set by the app once the window appears.
    weak var model: HubViewModel?
    private var terminationPending = false

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let model, let label = model.runningActionLabel else { return .terminateNow }
        let alert = NSAlert()
        alert.messageText = String(localized: "Cancel the running \(label)?")
        alert.informativeText = String(localized: "Quitting stops it now. You can run it again later.")
        alert.addButton(withTitle: String(localized: "Quit & Cancel"))
        alert.addButton(withTitle: String(localized: "Keep Running"))
        guard alert.runModal() == .alertFirstButtonReturn else { return .terminateCancel }
        model.cancelRunning()
        guard model.runningAction != nil else { return .terminateNow }
        terminationPending = true
        model.onIdle = { [weak self] in self?.replyToTermination() }
        Task { [weak self] in
            try? await Task.sleep(for: Self.quitTimeout)
            self?.replyToTermination()
        }
        return .terminateLater
    }

    /// Replies exactly once, whether the process ended or the timeout fired.
    private func replyToTermination() {
        guard terminationPending else { return }
        terminationPending = false
        model?.onIdle = nil
        NSApp.reply(toApplicationShouldTerminate: true)
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        guard !flag else { return true }
        return !showMainWindow()
    }

    /// Brings the hub window forward. Returns false when no window exists to show.
    @discardableResult
    func showMainWindow() -> Bool {
        let window = NSApp.windows.first { $0.identifier?.rawValue.contains("hub") == true }
            ?? NSApp.windows.first { $0.canBecomeMain }
        guard let window else { return false }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate()
        return true
    }
}
