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
                if title.showsNotesFirst { notes }
                if let run = model.run(for: title) {
                    ActivityCard(run: run, model: model, activity: model.activity)
                }
                if let message = model.message(for: title) {
                    Text(message).font(.callout).foregroundStyle(.secondary)
                }
                SetupChecklistCard(title: title, isInstalling: model.isRunning(title)).id(title.id)
                DetailsCard(title: title, model: model)
                if !title.showsNotesFirst { notes }
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
            // Keeping saves comes first; removing them is never the only or default choice
            // unless saves are all that is left.
            ForEach(UninstallChoice.choices(for: title), id: \.self) { choice in
                Button(choice.label, role: .destructive) {
                    model.run(action, for: title, includeSaves: choice.includesSaves, label: choice.label)
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: { _ in
            Text(HubUIText.uninstall(title))
        }
    }

    /// What to know about the title. An unsupported title shows it under the buttons, before
    /// anyone installs it; the others keep it below the details.
    @ViewBuilder private var notes: some View {
        if !title.notes.isEmpty {
            Callout(text: title.notes, systemImage: "info.circle")
        }
        if let link = title.link, let destination = link.destination {
            Link(destination: destination) {
                Label(link.label, systemImage: "arrow.up.right.square")
            }
            .font(.callout)
            .padding(.horizontal, 14)
        }
        if let note = title.contributionNote {
            Callout(text: note, systemImage: "hand.raised")
        }
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

/// The destructive buttons of the uninstall dialog, chosen from the backend's preview.
enum UninstallChoice: Hashable {
    case keepSaves, everything, gameOnly, savesOnly

    var label: String {
        switch self {
        case .keepSaves: String(localized: "Remove game, keep saves")
        case .everything: String(localized: "Remove everything including saves")
        case .gameOnly: String(localized: "Remove game")
        case .savesOnly: String(localized: "Remove saves")
        }
    }

    var includesSaves: Bool { self == .everything || self == .savesOnly }

    static func choices(for title: HubTitle) -> [UninstallChoice] {
        guard let removal = title.removal else { return [.gameOnly] }
        switch (removal.items.isEmpty, removal.saves != nil) {
        case (false, true): return [.keepSaves, .everything]
        case (true, true): return [.savesOnly]
        case (false, false), (true, false): return [.gameOnly]
        }
    }
}

enum HubUIText {
    static let registerKeyButton = String(localized: "Register player key")
    static let registerKeyNote = String(localized: "First time? Register your player key, then press Play. Play can't ask for the key itself.")
    static let cdKeyRequired = String(localized: "CD key required")
    static let cdKeyHelpLink = String(localized: "How to get your CD key")

    /// The dialog message: every path the backend reported, then the saves folder.
    static func uninstall(_ title: HubTitle) -> String {
        guard let removal = title.removal else {
            let removed = title.locations?.prefix ?? String(localized: "this game's Wine prefix")
            let kept = title.locations?.userdata.map { String(localized: "your saved data at \($0)") }
                ?? String(localized: "your saved data")
            return String(localized: "This removes \(removed). It keeps \(kept) and the runtime.")
        }
        var parts: [String] = []
        if !removal.items.isEmpty {
            let lines = removal.items.map { "• \($0.label): \(display($0.path))" }
            parts.append(String(localized: "This removes:") + "\n" + lines.joined(separator: "\n"))
        }
        if let saves = removal.saves.map(display) {
            parts.append(removal.items.isEmpty
                ? String(localized: "Only your saved data is left, in \(saves). Removing it deletes this game's saves, replays, tracks and settings. This cannot be undone.")
                : String(localized: "Your saves, replays, tracks and settings are in \(saves). \"Remove everything including saves\" deletes that folder too. This cannot be undone."))
        } else {
            parts.append(String(localized: "No saved data was found for this game."))
        }
        parts.append(String(localized: "The Wine runtime and downloads shared with other games are kept."))
        return parts.joined(separator: "\n\n")
    }

    /// Display only: the home folder shown as ~. The backend acts on the full path.
    private static func display(_ path: String) -> String {
        (path as NSString).abbreviatingWithTildeInPath
    }
}
