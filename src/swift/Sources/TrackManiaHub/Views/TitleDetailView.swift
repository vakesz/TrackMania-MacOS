import SwiftUI

struct TitleDetailView: View {
    let title: HubTitle
    @ObservedObject var model: HubViewModel
    @ObservedObject var updates: UpdateChecker
    @AppStorage(HubDefaults.dismissedUpdate) private var dismissedUpdate = ""
    @State private var runtimeChoiceAction: HubAction?
    @State private var uninstallAction: HubAction?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                if let info = updates.available, info.tag != dismissedUpdate {
                    UpdateBanner(info: info) { dismissedUpdate = info.tag }
                }
                HeroHeaderView(title: title)
                ActionBar(title: title, model: model, perform: perform)
                if let run = model.run(for: title) {
                    ActivityCard(run: run, model: model, activity: model.activity)
                }
                if let message = model.message(for: title) {
                    Text(message).font(.callout).foregroundStyle(.secondary)
                }
                SetupChecklistCard(title: title)
                DetailsCard(title: title, model: model)
                if !title.notes.isEmpty {
                    Callout(text: title.notes, systemImage: "info.circle")
                }
                if let error = title.error {
                    Callout(text: error, systemImage: "exclamationmark.triangle.fill", tint: .orange)
                }
            }
            .padding(32)
            .frame(maxWidth: 780, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .top)
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
                + "Building from source clones this hub's exact source and the pinned upstream projects, "
                + "then compiles them; it needs Xcode, Rosetta 2 and Homebrew, can take hours, "
                + "and removes its build files when it succeeds.")
        }
        .confirmationDialog(
            "Uninstall \(title.name)?",
            isPresented: Binding(get: { uninstallAction != nil },
                                 set: { if !$0 { uninstallAction = nil } }),
            titleVisibility: .visible,
            presenting: uninstallAction
        ) { action in
            Button(Self.confirmLabel(action.label), role: .destructive) { model.run(action, for: title) }
            Button("Cancel", role: .cancel) {}
        } message: { _ in
            Text(HubUIText.uninstall(title))
        }
    }

    /// Menu items end in "…" because they open a dialog; the dialog's own button must not.
    nonisolated static func confirmLabel(_ label: String) -> String {
        label.hasSuffix("…") ? String(label.dropLast()) : label
    }

    private func perform(_ action: HubAction) {
        if action.kind == .import {
            model.chooseImportSource(for: title, action: action)
        } else if action.kind == .destructive {
            uninstallAction = action
        } else if title.needsRuntimeChoice(for: action) {
            runtimeChoiceAction = action
        } else {
            model.run(action, for: title)
        }
    }
}

enum HubUIText {
    static func uninstall(_ title: HubTitle) -> String {
        let removed = title.locations?.prefix ?? String(localized: "this game's Wine prefix")
        let kept = title.locations?.userdata.map { String(localized: "your saved data at \($0)") }
            ?? String(localized: "your saved data")
        return String(localized: "This removes \(removed). It keeps \(kept), the cached installers and the runtime.")
    }
}
