import SwiftUI

struct TitleDetailView: View {
    let title: HubTitle
    @ObservedObject var model: HubViewModel
    @State private var runtimeChoiceAction: HubAction?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                HStack(alignment: .center, spacing: 18) {
                    GameIconView(title: title, size: 84)
                    VStack(alignment: .leading, spacing: 6) {
                        Text(title.name)
                            .font(.system(size: 26, weight: .semibold, design: .rounded))
                        HStack(spacing: 8) {
                            Circle()
                                .fill(title.supportColor)
                                .frame(width: 7, height: 7)
                            Text(title.supportLabel)
                                .font(.callout.weight(.medium))
                                .foregroundStyle(title.supportColor)
                            Text(title.availability)
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
                    ForEach(title.summary) { item in
                        HStack(spacing: 10) {
                            Image(systemName: item.ready ? "checkmark.circle.fill" : "circle")
                                .foregroundStyle(item.ready ? Color.green : Color.secondary)
                                .frame(width: 16)
                            Text(item.label)
                                .foregroundStyle(.secondary)
                            Spacer()
                            Text(item.ready ? "Ready" : "Not installed")
                                .foregroundStyle(item.ready ? .primary : .secondary)
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
                            HStack {
                                ProgressView("Working on \(title.name)…")
                                Spacer()
                                Button("Cancel") { model.cancelRunning() }
                            }
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
        .confirmationDialog(
            "Install the Wine runtime",
            isPresented: Binding(get: { runtimeChoiceAction != nil },
                                 set: { if !$0 { runtimeChoiceAction = nil } }),
            titleVisibility: .visible,
            presenting: runtimeChoiceAction
        ) { action in
            Button("Download prebuilt (recommended)") {
                model.run(action, for: title, runtime: .download)
            }
            Button("Build from source…") {
                model.run(action, for: title, runtime: .build)
            }
            Button("Cancel", role: .cancel) {}
        } message: { _ in
            Text("Downloading fetches the verified runtime for this release and takes a few minutes. "
                + "Building from source compiles the runtime from this release's source archive "
                + "and needs Xcode, Rosetta 2 and Homebrew; it can take hours.")
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
        if action.kind == .import {
            model.chooseImportSource(for: title, action: action)
        } else if title.needsRuntimeChoice(for: action) {
            runtimeChoiceAction = action
        } else {
            model.run(action, for: title)
        }
    }

    private func actionIcon(_ action: HubAction) -> String {
        switch action.kind {
        case .install: "arrow.down.to.line"
        case .import: "tray.and.arrow.down"
        case .apps: "macwindow.badge.plus"
        case .verify: "arrow.triangle.2.circlepath"
        case .build, .unknown: "wrench.and.screwdriver"
        }
    }
}
