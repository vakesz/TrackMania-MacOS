import SwiftUI

extension HubAppNames: Identifiable {
    var id: String { name }
}

/// The text of the rename sheet and what its buttons may do. Swift only trims; the backend validates.
struct RenameAppsDraft: Equatable {
    let current: String
    let custom: Bool
    var text: String

    init(_ names: HubAppNames) {
        current = names.name
        custom = names.custom
        text = names.name
    }

    var trimmed: String { text.trimmingCharacters(in: .whitespacesAndNewlines) }

    var canApply: Bool { !trimmed.isEmpty && trimmed != current }

    var canReset: Bool { custom }

    var preview: String {
        let name = trimmed
        return String(localized: "Applications will show “\(name)” and “\(name) Settings”.")
    }
}

/// Asks for the base name of a title's apps. `onApply` gets the new name, or nil for Reset.
struct RenameAppsSheet: View {
    let onApply: (String?) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var draft: RenameAppsDraft

    init(names: HubAppNames, onApply: @escaping (String?) -> Void) {
        self.onApply = onApply
        _draft = State(initialValue: RenameAppsDraft(names))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Rename apps").font(.headline)
            TextField("Name", text: $draft.text)
                .textFieldStyle(.roundedBorder)
                .onSubmit(apply)
            Text(draft.preview).font(.callout).foregroundStyle(.secondary)
            HStack {
                Button("Reset to default") { onApply(nil); dismiss() }
                    .disabled(!draft.canReset)
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button("Apply", action: apply)
                    .keyboardShortcut(.defaultAction)
                    .disabled(!draft.canApply)
            }
        }
        .padding(20)
        .frame(width: 400)
    }

    private func apply() {
        guard draft.canApply else { return }
        onApply(draft.trimmed)
        dismiss()
    }
}
