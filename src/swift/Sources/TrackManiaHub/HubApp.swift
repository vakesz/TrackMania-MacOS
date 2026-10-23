import SwiftUI
import AppKit

@main
struct TrackManiaHubApp: App {
    var body: some Scene {
        WindowGroup("TrackMania Mac") {
            HubRootView()
                .frame(minWidth: 900, minHeight: 600)
        }
    }
}

private struct HubRootView: View {
    @StateObject private var model = HubViewModel()

    var body: some View {
        Group {
            if let snapshot = model.snapshot {
                if snapshot.titles.isEmpty {
                    ContentUnavailableView {
                        Label("No titles available", systemImage: "exclamationmark.triangle")
                    } description: {
                        Text(snapshot.error ?? "The title registry did not provide any games.")
                    } actions: {
                        Button("Reload titles", systemImage: "arrow.clockwise") {
                            model.reload()
                        }
                    }
                } else {
                    NavigationSplitView {
                        List(snapshot.titles, selection: $model.selectedTitleID) { title in
                            TitleListRow(title: title)
                        }
                        .listStyle(.sidebar)
                        .navigationTitle("TrackMania")
                    } detail: {
                        if let title = selectedTitle(in: snapshot) {
                            TitleDetailView(title: title, model: model)
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
        .task { model.reload() }
    }

    private func selectedTitle(in snapshot: HubSnapshot) -> HubTitle? {
        snapshot.titles.first(where: { $0.id == model.selectedTitleID }) ?? snapshot.titles.first
    }
}
