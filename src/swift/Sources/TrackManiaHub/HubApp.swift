import SwiftUI
import AppKit

@main
struct TrackManiaHubApp: App {
    @NSApplicationDelegateAdaptor(HubAppDelegate.self) private var appDelegate
    @StateObject private var model = HubViewModel()
    @StateObject private var updates = UpdateChecker()
    @State private var showWelcome = false

    init() {
        let defaults = UserDefaults.standard
        if Bundle.main.bundleIdentifier != HubLegacy.bundleIdentifier {
            HubDefaults.adoptLegacySettings(
                into: defaults, from: defaults.persistentDomain(forName: HubLegacy.bundleIdentifier))
        }
    }

    var body: some Scene {
        Window("TrackMania Mac", id: "hub") {
            HubRootView(model: model, updates: updates, showWelcome: $showWelcome)
                .frame(minWidth: 900, minHeight: 600)
                .onAppear { appDelegate.model = model }
        }
        .commands {
            HubCommands(model: model) {
                showWelcome = true
                appDelegate.showMainWindow()
            }
        }
        Settings {
            SettingsView()
        }
    }
}

struct HubCommands: Commands {
    @ObservedObject var model: HubViewModel
    let showWelcome: () -> Void

    var body: some Commands {
        CommandGroup(after: .toolbar) {
            Button("Refresh") { model.reload() }
                .keyboardShortcut("r")
                .disabled(model.runningAction != nil)
            Button("Open Logs Folder") { model.openLogsFolder() }
                .keyboardShortcut("l", modifiers: [.option, .command])
        }
        CommandGroup(replacing: .help) {
            Button("Welcome Guide", action: showWelcome)
            Link("TrackMania Mac on GitHub", destination: HubLinks.repository)
        }
    }
}

enum HubLinks {
    static let repository = URL(string: "https://github.com/vakesz/TrackMania-MacOS")!
}

enum HubDefaults {
    static let checkForUpdates = "checkForUpdatesAutomatically"
    static let didShowWelcome = "didShowWelcome"
    static let dismissedUpdate = "dismissedUpdateVersion"
}
