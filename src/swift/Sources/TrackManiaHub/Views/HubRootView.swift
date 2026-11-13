import SwiftUI

struct HubRootView: View {
    @ObservedObject var model: HubViewModel
    @ObservedObject var updates: UpdateChecker
    @Binding var showWelcome: Bool
    @AppStorage(HubDefaults.checkForUpdates) private var checkForUpdates = true
    @AppStorage(HubDefaults.didShowWelcome) private var didShowWelcome = false

    var body: some View {
        Group {
            if let snapshot = model.snapshot {
                if let migration = snapshot.migration, migration.waiting || model.migrating {
                    MigrationView(model: model, migration: migration)
                } else if snapshot.titles.isEmpty {
                    ContentUnavailableView {
                        Label("No titles available", systemImage: "exclamationmark.triangle")
                    } description: {
                        Text(snapshot.error ?? String(localized: "The title registry did not provide any games."))
                    } actions: {
                        Button("Reload titles", systemImage: "arrow.clockwise") {
                            model.reload()
                        }
                    }
                } else {
                    NavigationSplitView {
                        HubSidebar(model: model, titles: snapshot.titles)
                    } detail: {
                        if let title = selectedTitle(in: snapshot) {
                            TitleDetailView(title: title, model: model, updates: updates)
                        } else {
                            ContentUnavailableView("Choose a title", systemImage: "gamecontroller")
                        }
                    }
                }
            } else if let message = model.message {
                ContentUnavailableView("TrackMania Mac needs attention", systemImage: "exclamationmark.triangle",
                                       description: Text(message))
            } else {
                ProgressView("Loading installed-title state…")
            }
        }
        .task {
            model.reload()
            if !didShowWelcome { showWelcome = true }
        }
        .task(id: model.snapshot?.hubVersion) {
            updates.checkIfNeeded(currentVersion: UpdateChecker.bundleVersion ?? model.snapshot?.hubVersion,
                                  enabled: checkForUpdates)
        }
        .sheet(isPresented: $showWelcome, onDismiss: { didShowWelcome = true }, content: {
            WelcomeView { showWelcome = false }
        })
    }

    private func selectedTitle(in snapshot: HubSnapshot) -> HubTitle? {
        snapshot.titles.first(where: { $0.id == model.selectedTitleID }) ?? snapshot.titles.defaultSelection
    }
}

struct HubSidebar: View {
    static let minimumWidth: CGFloat = 220

    @ObservedObject var model: HubViewModel
    let titles: [HubTitle]

    var body: some View {
        let sections = titles.sidebarSections
        List(selection: $model.selectedTitleID) {
            if !sections.installed.isEmpty {
                Section("Installed") { rows(sections.installed) }
            }
            if !sections.available.isEmpty {
                Section("Available") { rows(sections.available) }
            }
        }
        .listStyle(.sidebar)
        .safeAreaInset(edge: .bottom, spacing: 0) { footer }
        .navigationTitle("TrackMania")
        .navigationSplitViewColumnWidth(min: HubSidebar.minimumWidth, ideal: 300, max: 380)
    }

    private var footer: some View {
        HubSidebarFooter(version: UpdateChecker.bundleVersion ?? model.snapshot?.hubVersion)
    }

    private func rows(_ titles: [HubTitle]) -> some View {
        ForEach(titles) { title in
            if model.isRunning(title) {
                RunningTitleRow(title: title, activity: model.activity)
            } else {
                TitleListRow(title: title)
            }
        }
    }
}

/// The row under the sidebar list: the settings button, then the app icon, name and version.
struct HubSidebarFooter: View {
    static let settingsSymbol = "gearshape"
    static let settingsLabel = "Settings"

    let version: String?

    var body: some View {
        HStack(spacing: 8) {
            // Opens the same window as the app menu item and Command-comma.
            SettingsLink { Image(systemName: Self.settingsSymbol) }
                .buttonStyle(.borderless)
                .help(Self.settingsLabel)
                .accessibilityLabel(Self.settingsLabel)
            HStack(spacing: 8) {
                HubIconView(size: 22)
                Text("TrackMania Mac").font(.callout.weight(.medium)).lineLimit(1)
                if let version {
                    Text(version).font(.caption).foregroundStyle(.secondary).monospacedDigit().lineLimit(1)
                }
            }
            .accessibilityElement(children: .combine)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }
}

/// Only the running title's row follows the run's progress, so other rows and the sidebar
/// itself are not invalidated by output batches.
private struct RunningTitleRow: View {
    let title: HubTitle
    @ObservedObject var activity: InstallerActivity

    var body: some View {
        TitleListRow(title: title, isRunning: true, fraction: activity.progress.fraction)
    }
}
