import SwiftUI

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
                NavigationSplitView {
                    List(snapshot.titles, selection: $model.selectedTitleID) { title in
                        VStack(alignment: .leading, spacing: 3) {
                            Text(title.name)
                            Text(title.status.capitalized)
                                .font(.caption)
                                .foregroundStyle(title.status == "supported" ? .green : .secondary)
                        }
                        .tag(title.id)
                    }
                    .navigationTitle("TrackMania")
                } detail: {
                    if let title = selectedTitle(in: snapshot) {
                        TitleDetail(title: title, model: model)
                    } else {
                        ContentUnavailableView("Choose a title", systemImage: "gamecontroller")
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

private struct TitleDetail: View {
    let title: HubTitle
    @ObservedObject var model: HubViewModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(title.name).font(.largeTitle.weight(.semibold))
                        Text(title.status == "supported" ? "Supported" : "Provisional")
                            .foregroundStyle(title.status == "supported" ? .green : .orange)
                        if !title.notes.isEmpty {
                            Text(title.notes).foregroundStyle(.secondary)
                        }
                    }
                    Spacer()
                    Button { model.reload() } label: {
                        Label("Refresh", systemImage: "arrow.clockwise")
                    }
                    .disabled(model.runningAction != nil)
                }

                GroupBox("Installation state") {
                    Grid(alignment: .leading, horizontalSpacing: 28, verticalSpacing: 8) {
                        ForEach(Array(title.state.summary.enumerated()), id: \.offset) { _, item in
                            GridRow {
                                Text(item.0).foregroundStyle(.secondary)
                                Text(item.1 == "ready" ? "Ready" : "Missing")
                                    .foregroundStyle(item.1 == "ready" ? .green : .secondary)
                            }
                        }
                    }
                    .padding(.vertical, 4)
                }

                GroupBox("Actions") {
                    FlowLayout(spacing: 10) {
                        ForEach(title.actions) { action in
                            Button(action.label) {
                                if action.id == "import" {
                                    model.chooseImportSource(for: title, action: action)
                                } else {
                                    model.run(action, for: title)
                                }
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled(model.runningAction != nil)
                        }
                        Button("Open Game") { model.openApp(at: title.apps.game) }
                            .disabled(model.runningAction != nil || title.state.gameApp != "ready")
                        Button("Open Settings") { model.openApp(at: title.apps.settings) }
                            .disabled(model.runningAction != nil || title.state.settingsApp != "ready")
                    }
                    .padding(.vertical, 4)
                }

                if let message = model.message {
                    Text(message).foregroundStyle(.secondary)
                }

                if !model.log.isEmpty {
                    GroupBox("Activity log") {
                        ScrollView {
                            Text(model.log)
                                .font(.system(.body, design: .monospaced))
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .textSelection(.enabled)
                        }
                        .frame(minHeight: 150, maxHeight: 260)
                    }
                }
            }
            .padding(24)
        }
    }
}

private struct FlowLayout: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? subviews.reduce(0) { $0 + $1.sizeThatFits(.unspecified).width + spacing }
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x > 0, x + size.width > width {
                x = 0
                y += rowHeight + spacing
                rowHeight = 0
            }
            x += size.width + (x > 0 ? spacing : 0)
            rowHeight = max(rowHeight, size.height)
        }
        return CGSize(width: width, height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var point = bounds.origin
        var rowHeight: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if point.x > bounds.minX, point.x + size.width > bounds.maxX {
                point.x = bounds.minX
                point.y += rowHeight + spacing
                rowHeight = 0
            }
            subview.place(at: point, proposal: ProposedViewSize(size))
            point.x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}
