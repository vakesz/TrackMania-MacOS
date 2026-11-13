import SwiftUI

struct ActionBar: View {
    let title: HubTitle
    @ObservedObject var model: HubViewModel
    let perform: (HubAction) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            controls
            if title.needsCDKey {
                cdKeyNotice
            } else if title.showsRegisterKey {
                Text(HubUIText.registerKeyNote).font(.callout).foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private var controls: some View {
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
                .disabled(busy || !title.playEnabled)
                .help(title.needsCDKey ? HubUIText.cdKeyRequired : "")
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

            if let target = title.registerKeyTarget {
                // Same app as Settings; for these titles the label says what to do with it.
                Button(HubUIText.registerKeyButton) { model.openApp(at: target, for: title) }
                    .controlSize(.large)
                    .disabled(busy)
            } else if title.state.settingsApp?.ready == true {
                Button("Settings") { model.openApp(at: title.apps.settings, for: title) }
                    .controlSize(.large)
                    .disabled(busy)
            }

            moreMenu(busy: busy)
            Spacer()
        }
    }

    private var cdKeyNotice: some View {
        HStack(spacing: 6) {
            Label(HubUIText.cdKeyRequired, systemImage: "key.fill")
                .foregroundStyle(.orange)
            Link(HubUIText.cdKeyHelpLink, destination: HubLinks.cdKeyHelp)
        }
        .font(.callout)
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
            // Uninstall waits until the game has stopped; the backend refuses it too.
            ForEach(groups.destructive) { action in
                Button(action.label, role: .destructive) { perform(action) }
                    .disabled(busy || title.sessionActive)
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
