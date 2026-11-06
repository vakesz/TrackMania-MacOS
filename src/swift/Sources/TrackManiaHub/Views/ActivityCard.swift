import SwiftUI

struct ActivityCard: View {
    let run: RunRecord
    @ObservedObject var model: HubViewModel
    @State private var showDetails = false

    private var isRunning: Bool { run.outcome == nil }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                statusIcon
                VStack(alignment: .leading, spacing: 2) {
                    Text(heading).font(.headline)
                    if let detail { Text(detail).font(.callout).foregroundStyle(.secondary).lineLimit(2) }
                }
                Spacer()
                if isRunning {
                    Button(model.cancelling ? "Cancelling…" : "Cancel") { model.cancelRunning() }
                        .disabled(model.cancelling)
                }
                if !model.log.isEmpty {
                    Button("Copy Log") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(model.log.text, forType: .string)
                    }
                }
            }
            if isRunning {
                if let fraction = model.progress.fraction {
                    ProgressView(value: fraction)
                } else {
                    ProgressView().progressViewStyle(.linear)
                }
            }
            if let path = run.reportPath {
                Button("Show in Finder") { model.reveal(path: path) }
            }
            if !model.log.isEmpty {
                DisclosureGroup("Details", isExpanded: $showDetails) {
                    if showDetails { LogConsole(model: model) }
                }
            }
        }
        .card()
        .onChange(of: run.outcome) { _, outcome in
            if case .failed = outcome { showDetails = true }
        }
    }

    private var heading: String {
        switch run.outcome {
        case nil: run.actionLabel
        case .succeeded: String(localized: "\(run.actionLabel) finished")
        case .failed: String(localized: "\(run.actionLabel) failed")
        case .cancelled: String(localized: "\(run.actionLabel) cancelled")
        }
    }

    private var detail: String? {
        switch run.outcome {
        case nil: model.progress.stage ?? String(localized: "Working…")
        case let .failed(reason): reason
        case .succeeded, .cancelled: nil
        }
    }

    @ViewBuilder private var statusIcon: some View {
        switch run.outcome {
        case nil:
            ProgressView().controlSize(.small).accessibilityLabel("Working")
        case .succeeded:
            Image(systemName: "checkmark.circle.fill").foregroundStyle(.green).accessibilityLabel("Succeeded")
        case .failed:
            Image(systemName: "xmark.octagon.fill").foregroundStyle(.red).accessibilityLabel("Failed")
        case .cancelled:
            Image(systemName: "slash.circle").foregroundStyle(.secondary).accessibilityLabel("Cancelled")
        }
    }
}

/// Only built while the details are expanded, so output batches cost nothing when collapsed.
private struct LogConsole: View {
    @ObservedObject var model: HubViewModel

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    Text(model.log.text)
                        .font(.system(.caption, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                    Color.clear.frame(height: 1).id("end")
                }
                .padding(8)
            }
            .frame(minHeight: 120, maxHeight: 260)
            .background(.background, in: RoundedRectangle(cornerRadius: 8))
            .onAppear { proxy.scrollTo("end", anchor: .bottom) }
            .onChange(of: model.logRevision) { proxy.scrollTo("end", anchor: .bottom) }
        }
    }
}
