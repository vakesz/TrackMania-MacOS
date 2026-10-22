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
                            HStack(spacing: 11) {
                                GameIcon(title: title, size: 38)
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(title.name)
                                        .font(.system(.body, weight: .medium))
                                        .lineLimit(1)
                                    Text(title.shortName ?? title.status.capitalized)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                    Text(supportLabel(title))
                                        .font(.caption2.weight(.medium))
                                        .foregroundStyle(supportColor(title))
                                }
                                Spacer(minLength: 2)
                                Circle()
                                    .fill(title.error == nil ? supportColor(title) : .orange)
                                    .frame(width: 7, height: 7)
                            }
                            .padding(.vertical, 3)
                            .tag(title.id)
                        }
                        .listStyle(.sidebar)
                        .navigationTitle("TrackMania")
                    } detail: {
                        if let title = selectedTitle(in: snapshot) {
                            TitleDetail(title: title, model: model)
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

private struct TitleDetail: View {
    let title: HubTitle
    @ObservedObject var model: HubViewModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                HStack(alignment: .center, spacing: 18) {
                    GameIcon(title: title, size: 84)
                    VStack(alignment: .leading, spacing: 6) {
                        Text(title.name)
                            .font(.system(size: 26, weight: .semibold, design: .rounded))
                        HStack(spacing: 8) {
                            Circle()
                                .fill(supportColor(title))
                                .frame(width: 7, height: 7)
                            Text(supportLabel(title))
                                .font(.callout.weight(.medium))
                                .foregroundStyle(supportColor(title))
                            Text(availability(title))
                                .font(.callout)
                                .foregroundStyle(.secondary)
                            if title.requiresSerial == true {
                                Text("Serial key required")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                    Spacer()
                }

                HStack(spacing: 10) {
                    if let primaryAction {
                        Button {
                            perform(primaryAction)
                        } label: {
                            Label(primaryAction.label, systemImage: actionIcon(primaryAction))
                        }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)
                        .disabled(model.runningAction != nil || title.error != nil)
                    }

                    if title.state.gameApp?.ready == true {
                        Button {
                            model.openApp(at: title.apps.game, for: title)
                        } label: {
                            Label("Play", systemImage: "play.fill")
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.large)
                        .disabled(model.runningAction != nil)
                    }

                    if let secondaryActions = nonPrimaryActions, !secondaryActions.isEmpty {
                        Menu("More") {
                            ForEach(secondaryActions) { action in
                                Button(action.label) { perform(action) }
                                    .disabled(model.runningAction != nil)
                            }
                        }
                        .menuStyle(.borderlessButton)
                    }

                    if title.state.settingsApp?.ready == true {
                        Button("Settings") { model.openApp(at: title.apps.settings, for: title) }
                            .disabled(model.runningAction != nil)
                    }

                    Spacer()
                    Button { model.reload() } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .buttonStyle(.plain)
                    .help("Refresh installation status")
                    .disabled(model.runningAction != nil)
                }

                Divider()

                VStack(alignment: .leading, spacing: 13) {
                    Text("Installation")
                        .font(.headline)
                    ForEach(Array(title.summary.enumerated()), id: \.offset) { _, item in
                        HStack(spacing: 10) {
                            Image(systemName: item.1 == "ready" ? "checkmark.circle.fill" : "circle")
                                .foregroundStyle(item.1 == "ready" ? Color.green : Color.secondary)
                                .frame(width: 16)
                            Text(item.0)
                                .foregroundStyle(.secondary)
                            Spacer()
                            Text(item.1 == "ready" ? "Ready" : "Not installed")
                                .foregroundStyle(item.1 == "ready" ? .primary : .secondary)
                        }
                        .font(.callout)
                    }
                }

                if !title.notes.isEmpty {
                    Text(title.notes)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                if let error = title.error {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                }

                if model.isRunning(title) || (model.hasActivity(for: title) && !model.log.isEmpty) {
                    VStack(alignment: .leading, spacing: 10) {
                        if model.isRunning(title) {
                            ProgressView("Working on \(title.name)…")
                                .frame(maxWidth: .infinity, alignment: .leading)
                            if let progress = model.progressDescription(for: title) {
                                Text(progress)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                            }
                        }
                        if !model.log.isEmpty {
                            DisclosureGroup("Installer details") {
                                ScrollView {
                                    Text(model.log)
                                        .font(.system(.caption, design: .monospaced))
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                        .textSelection(.enabled)
                                }
                                .frame(minHeight: 100, maxHeight: 220)
                            }
                        }
                    }
                }

                if let message = model.message(for: title) {
                    Text(message)
                        .font(.callout)
                        .foregroundStyle(model.runningAction == nil ? Color.secondary : Color.blue)
                }
            }
            .padding(32)
            .frame(maxWidth: 760, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .topLeading)
        }
    }

    private var primaryAction: HubAction? {
        title.actions.first(where: \.primary) ?? title.actions.first
    }

    private var nonPrimaryActions: [HubAction]? {
        guard let primaryAction else { return nil }
        return title.actions.filter { $0.id != primaryAction.id }
    }

    private func perform(_ action: HubAction) {
        if action.id == "import" {
            model.chooseImportSource(for: title, action: action)
        } else {
            model.run(action, for: title)
        }
    }

    private func actionIcon(_ action: HubAction) -> String {
        switch action.kind {
        case "install": "arrow.down.to.line"
        case "import": "tray.and.arrow.down"
        case "apps": "macwindow.badge.plus"
        case "verify": "arrow.triangle.2.circlepath"
        default: "wrench.and.screwdriver"
        }
    }
}

private struct GameIcon: View {
    let title: HubTitle
    let size: CGFloat

    var body: some View {
        Group {
            if let path = title.icon, let image = NSImage(contentsOfFile: path) {
                Image(nsImage: image)
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .padding(size * 0.08)
            } else {
                Text(title.shortName ?? "TM")
                    .font(.system(size: max(9, size * 0.24), weight: .heavy, design: .rounded))
                    .minimumScaleFactor(0.6)
                    .foregroundStyle(.white)
                    .padding(size * 0.13)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(iconColor(for: title))
            }
        }
        .frame(width: size, height: size)
        .background(.quaternary)
        .clipShape(RoundedRectangle(cornerRadius: size * 0.22, style: .continuous))
    }
}

private func availability(_ title: HubTitle) -> String {
    if title.error != nil { return "Needs attention" }
    if title.state.gameApp?.ready == true { return "Ready to play" }
    if title.state.game?.ready == true { return "Game files installed" }
    if title.state.prefix?.ready == true { return "Platform ready" }
    return "Not installed"
}

private func supportLabel(_ title: HubTitle) -> String {
    title.status == "supported" ? "Supported" : "Provisional"
}

private func supportColor(_ title: HubTitle) -> Color {
    title.status == "supported" ? .green : .orange
}

private func iconColor(for title: HubTitle) -> Color {
    switch title.id {
    case "tmnf": Color(red: 0.08, green: 0.28, blue: 0.65)
    case "tmu": Color(red: 0.31, green: 0.17, blue: 0.57)
    case "tm-original": Color(red: 0.10, green: 0.40, blue: 0.32)
    case "tm-canyon": Color(red: 0.66, green: 0.27, blue: 0.06)
    case "tm-turbo": Color(red: 0.66, green: 0.10, blue: 0.15)
    default: Color.accentColor
    }
}
