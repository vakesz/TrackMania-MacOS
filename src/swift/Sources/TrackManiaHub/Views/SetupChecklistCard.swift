import SwiftUI

struct SetupChecklistCard: View {
    let title: HubTitle
    /// True while an install or repair runs for this title; the bar only shows then.
    var isInstalling = false

    /// The user's choice for this title; nil follows the default.
    @State private var userExpanded: Bool?

    var body: some View {
        let items = title.summary
        let ready = items.filter(\.ready).count
        let collapsible = title.isInstalled
        let expanded = !collapsible || (userExpanded ?? !title.setupStartsCollapsed)
        VStack(alignment: .leading, spacing: 12) {
            if collapsible {
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) { userExpanded = !expanded }
                } label: {
                    header(ready: ready, total: items.count, chevron: expanded ? "chevron.up" : "chevron.down")
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Setup")
                .accessibilityValue(expanded ? "Expanded" : "Collapsed")
                .help(expanded ? "Hide the setup steps" : "Show the setup steps")
            } else {
                header(ready: ready, total: items.count, chevron: nil)
            }
            if expanded {
                checklist(items, ready: ready)
            }
        }
        .card()
    }

    private func header(ready: Int, total: Int, chevron: String?) -> some View {
        HStack(spacing: 8) {
            Text("Setup").font(.headline)
            Spacer()
            if ready == total {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(.green).accessibilityHidden(true)
            }
            Text("\(ready) of \(total) ready").font(.callout).foregroundStyle(.secondary)
            if let chevron {
                Image(systemName: chevron)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .accessibilityHidden(true)
            }
        }
    }

    @ViewBuilder
    private func checklist(_ items: [InstallationItem], ready: Int) -> some View {
        if isInstalling {
            SetupProgressBar(ready: ready, total: items.count)
        }
        VStack(alignment: .leading, spacing: 10) {
            ForEach(items) { item in
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Image(systemName: item.symbol)
                        .foregroundStyle(.secondary)
                        .frame(width: 20)
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(item.label)
                        if let note = item.note {
                            Text(note).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Spacer()
                    Image(systemName: item.ready ? "checkmark.circle.fill" : "circle")
                        .foregroundStyle(item.ready ? AnyShapeStyle(Color.green) : AnyShapeStyle(.tertiary))
                }
                .font(.callout)
                .accessibilityElement(children: .combine)
                .accessibilityValue(item.ready ? "Ready" : "Not ready")
            }
        }
    }
}

/// Steps ready so far, shown during an install. A plain bar instead of ProgressView, whose
/// linear style keeps a coloured cap at zero and so looks started when nothing is ready.
private struct SetupProgressBar: View {
    let ready: Int
    let total: Int

    var body: some View {
        let fraction = total > 0 ? Double(ready) / Double(total) : 0
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                Capsule().fill(.quaternary)
                if ready > 0 {
                    Capsule()
                        .fill(ready == total ? Color.green : Color.accentColor)
                        .frame(width: max(proxy.size.height, proxy.size.width * fraction))
                }
            }
        }
        .frame(height: 6)
        .accessibilityElement()
        .accessibilityLabel("Setup progress")
        .accessibilityValue("\(ready) of \(total) ready")
    }
}
