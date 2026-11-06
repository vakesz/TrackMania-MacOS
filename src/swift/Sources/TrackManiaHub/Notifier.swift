import AppKit
import UserNotifications

/// Completion feedback. Notifications need a real .app bundle; an unbundled `swift run`
/// would crash inside UserNotifications, so it only bounces the Dock icon there.
@MainActor
enum Notifier {
    private static var authorizationRequested = false

    nonisolated static func canUseNotifications(bundleIdentifier: String?, bundleURL: URL) -> Bool {
        bundleIdentifier != nil && bundleURL.pathExtension == "app"
    }

    private static var available: Bool {
        canUseNotifications(bundleIdentifier: Bundle.main.bundleIdentifier, bundleURL: Bundle.main.bundleURL)
    }

    static func requestAuthorizationOnce() {
        guard available, !authorizationRequested else { return }
        authorizationRequested = true
        Task { _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) }
    }

    /// Only speaks up when the app is in the background.
    static func notify(title: String, body: String) {
        guard !NSApp.isActive else { return }
        NSApp.requestUserAttention(.informationalRequest)
        guard available else { return }
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        let request = UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil)
        Task { try? await UNUserNotificationCenter.current().add(request) }
    }
}
