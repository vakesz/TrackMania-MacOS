import SwiftUI

struct SettingsView: View {
    @ObservedObject var model: HubViewModel
    @AppStorage(HubDefaults.checkForUpdates) private var checkForUpdates = true
    @State private var pendingClean: HubCleanKind?

    private var busy: Bool { model.runningAction != nil }
    private var storage: HubStorage? { model.snapshot?.storage }

    var body: some View {
        Form {
            Section("Updates") {
                Toggle("Check for updates automatically", isOn: $checkForUpdates)
            }
            storageSection
            logsSection
            GameDefaultsSection(model: model)
            if let status = model.settingsStatus {
                Section {
                    switch status {
                    case let .done(text): Label(text, systemImage: "checkmark.circle").foregroundStyle(.secondary)
                    case let .failed(text): Label(text, systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                    }
                }
            }
        }
        .formStyle(.grouped)
        .frame(width: 480)
        .fixedSize(horizontal: false, vertical: true)
        .confirmationDialog(confirmationTitle, isPresented: confirming, titleVisibility: .visible) {
            if let kind = pendingClean {
                Button(confirmationButton(kind), role: .destructive) { model.clean(kind) }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(confirmationMessage)
        }
    }

    // MARK: Storage

    private var storageSection: some View {
        Section("Storage") {
            if let error = storage?.error {
                Text(error).font(.callout).foregroundStyle(.secondary)
            }
            LabeledContent("Downloads") {
                HStack {
                    Text(HubStorage.sizeText(storage?.downloads?.bytes)).foregroundStyle(.secondary)
                    Button("Clear downloads…") { pendingClean = .downloads }
                        .disabled(!SettingsGate.canClearDownloads(storage?.downloads, busy: busy))
                }
            }
            runtimesRow
        }
    }

    @ViewBuilder
    private var runtimesRow: some View {
        let runtimes = storage?.runtimes
        LabeledContent("Old runtimes") {
            HStack {
                Text(runtimes?.items.isEmpty == false
                     ? HubStorage.sizeText(runtimes?.removableBytes) : String(localized: "None"))
                    .foregroundStyle(.secondary)
                Button("Remove old runtimes…") { pendingClean = .runtimes }
                    .disabled(!SettingsGate.canRemoveRuntimes(runtimes, busy: busy))
            }
        }
        if let runtimes, !runtimes.items.isEmpty {
            Text(runtimes.items.map(\.name).joined(separator: ", ")).font(.callout).foregroundStyle(.secondary)
        }
        if let blocked = runtimes?.blocked {
            Text(blocked).font(.callout).foregroundStyle(.orange)
        }
    }

    // MARK: Logs

    private var logsSection: some View {
        Section("Logs") {
            LabeledContent("Logs folder") {
                HStack {
                    Text(HubStorage.sizeText(storage?.logs?.bytes)).foregroundStyle(.secondary)
                    Button("Open logs folder") { model.openLogsFolder() }
                }
            }
            diagnosticsRow
            Button("Clear logs…") { pendingClean = .logs }
                .disabled(!SettingsGate.canClearLogs(storage?.logs, busy: busy))
        }
    }

    @ViewBuilder
    private var diagnosticsRow: some View {
        let titles = model.snapshot?.diagnosticsTitles ?? []
        HStack {
            if titles.count > 1 {
                Menu("Save diagnostics report…") {
                    ForEach(titles) { title in
                        Button(title.name) { saveReport(for: title) }
                    }
                }
                .disabled(busy)
            } else {
                Button("Save diagnostics report…") { titles.first.map(saveReport) }
                    .disabled(busy || titles.isEmpty)
            }
            if let path = model.lastRun?.reportPath, model.lastRun?.actionID == "diagnostics" {
                Button("Show report in Finder") { model.reveal(path: path) }
            }
        }
    }

    private func saveReport(for title: HubTitle) {
        guard let action = title.diagnosticsAction else { return }
        model.run(action, for: title)
    }

    // MARK: Confirmation

    private var confirming: Binding<Bool> {
        Binding(get: { pendingClean != nil }, set: { if !$0 { pendingClean = nil } })
    }

    private var confirmationTitle: String {
        switch pendingClean {
        case .downloads: String(localized: "Clear downloads?")
        case .runtimes: String(localized: "Remove old runtimes?")
        case .logs: String(localized: "Clear logs?")
        case nil: ""
        }
    }

    private var confirmationMessage: String {
        switch pendingClean {
        case .downloads: String(localized: "The next install downloads these files again.")
        case .runtimes: String(localized: "The runtimes no title uses are deleted. This cannot be undone.")
        case .logs: String(localized: "All log files are deleted. This cannot be undone.")
        case nil: ""
        }
    }

    private func confirmationButton(_ kind: HubCleanKind) -> String {
        switch kind {
        case .downloads: String(localized: "Clear downloads")
        case .runtimes: String(localized: "Remove old runtimes")
        case .logs: String(localized: "Clear logs")
        }
    }
}

/// Window size, sound and x87 acceleration of an installed title. Nothing is sent until Apply.
struct GameDefaultsSection: View {
    @ObservedObject var model: HubViewModel
    @State private var selectedID: String?
    @State private var draft: GameDefaultsDraft?

    private var titles: [HubTitle] { model.snapshot?.gameDefaultTitles ?? [] }

    private var title: HubTitle? {
        titles.first { $0.id == selectedID } ?? titles.first
    }

    var body: some View {
        Section("Game defaults") {
            if let title, let options = title.launchOptions {
                content(title: title, options: options)
            } else {
                Text("Install a game to change its defaults.").foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private func content(title: HubTitle, options: HubLaunchOptions) -> some View {
        let lock = SettingsGate.gameDefaultsLock(options, sessionActive: title.sessionActive)
        let current = Binding(get: { draft ?? GameDefaultsDraft(options) }, set: { draft = $0 })
        Picker("Title", selection: Binding(get: { title.id }, set: { selectedID = $0; draft = nil })) {
            ForEach(titles) { Text($0.name).tag($0.id) }
        }
        Group {
            Picker("Display", selection: current.fullscreen) {
                Text("Full screen").tag(true)
                Text("Window").tag(false)
            }
            if !current.wrappedValue.fullscreen {
                LabeledContent("Size") {
                    HStack {
                        TextField("Width", text: current.width).frame(width: 64).multilineTextAlignment(.trailing)
                        Text("×")
                        TextField("Height", text: current.height).frame(width: 64)
                    }
                    .textFieldStyle(.roundedBorder)
                }
                if !current.wrappedValue.isValid {
                    Text("Enter a width and height as whole numbers above zero.")
                        .font(.callout).foregroundStyle(.orange)
                }
            }
            Toggle("Sound", isOn: current.audio)
            Toggle("x87 acceleration", isOn: current.x87)
        }
        .disabled(lock != nil || model.runningAction != nil)
        if options.renderer != "d9vk" {
            LabeledContent("Renderer") { Text(options.renderer.isEmpty ? "Unknown" : options.renderer).foregroundStyle(.secondary) }
        }
        if let lock {
            Text(lock).font(.callout).foregroundStyle(.secondary)
        }
        HStack {
            Spacer()
            Button("Apply") { apply(title: title, options: options, draft: current.wrappedValue) }
                .disabled(!SettingsGate.canApply(current.wrappedValue, options: options,
                                                 sessionActive: title.sessionActive,
                                                 busy: model.runningAction != nil))
        }
        .onChange(of: options) { _, _ in draft = nil }
    }

    private func apply(title: HubTitle, options: HubLaunchOptions, draft: GameDefaultsDraft) {
        let changes = draft.changes(from: options)
        guard !changes.isEmpty else { return }
        model.setDefaults(title: title.id, windowSize: changes.windowSize, audio: changes.audio, x87: changes.x87)
    }
}
