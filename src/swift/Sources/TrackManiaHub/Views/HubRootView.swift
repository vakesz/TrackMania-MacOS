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
        snapshot.titles.first(where: { $0.id == model.selectedTitleID }) ?? snapshot.titles.sidebarOrder.first
    }
}

struct HubSidebar: View {
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
        .navigationTitle("TrackMania")
        .navigationSplitViewColumnWidth(min: 220, ideal: 300, max: 380)
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

/// Only the running title's row follows the run's progress, so other rows and the sidebar
/// itself are not invalidated by output batches.
private struct RunningTitleRow: View {
    let title: HubTitle
    @ObservedObject var activity: InstallerActivity

    var body: some View {
        TitleListRow(title: title, isRunning: true, fraction: activity.progress.fraction)
    }
}
