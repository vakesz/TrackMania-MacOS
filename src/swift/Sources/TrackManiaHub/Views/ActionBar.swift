import SwiftUI

struct ActionBar: View {
    let title: HubTitle
    @ObservedObject var model: HubViewModel
    let perform: (HubAction) -> Void

    var body: some View {
        let busy = model.runningAction != nil
        HStack(spacing: 10) {
            switch title.primaryControl {
            case .play:
                Button {
                    model.openApp(at: title.apps.game, for: title)
                } label: {
                    Label("Play", systemImage: "play.fill")
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .keyboardShortcut(.return, modifiers: .command)
                .disabled(busy)
            case let .action(action):
                Button {
                    perform(action)
                } label: {
                    Label(action.label, systemImage: Self.icon(for: action))
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(busy || title.error != nil)
            case nil:
                EmptyView()
            }

            if title.state.settingsApp?.ready == true {
                Button("Settings") { model.openApp(at: title.apps.settings, for: title) }
                    .controlSize(.large)
                    .disabled(busy)
            }

            moreMenu(busy: busy)
            Spacer()
        }
    }

    private func moreMenu(busy: Bool) -> some View {
        let groups = title.moreActionGroups
        let location = title.installLocationPath.flatMap {
            FileManager.default.fileExists(atPath: $0) ? $0 : nil
        }
        return Menu {
            ForEach(groups.setup) { action in
                Button(action.label) { perform(action) }.disabled(busy)
            }
            if !groups.setup.isEmpty { Divider() }
            ForEach(groups.maintenance) { action in
                Button(action.label) { perform(action) }.disabled(busy)
            }
            if let location {
                Button("Show in Finder") { model.reveal(path: location) }
            }
            Button("Open Logs Folder") { model.openLogsFolder() }
            if !groups.destructive.isEmpty { Divider() }
            ForEach(groups.destructive) { action in
                Button(action.label, role: .destructive) { perform(action) }.disabled(busy)
            }
        } label: {
            Image(systemName: "ellipsis.circle")
                .imageScale(.large)
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .accessibilityLabel("More actions")
        .help("More actions")
    }

    static func icon(for action: HubAction) -> String {
        switch action.kind {
        case .install: "arrow.down.to.line"
        case .import: "tray.and.arrow.down"
        case .apps: "macwindow.badge.plus"
        case .verify: "checkmark.shield"
        case .maintenance: "wrench.and.screwdriver"
        case .destructive: "trash"
        case .build, .unknown: "wrench.and.screwdriver"
        }
    }
}
