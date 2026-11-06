import SwiftUI

struct SetupChecklistCard: View {
    let title: HubTitle

    var body: some View {
        let items = title.summary
        let ready = items.filter(\.ready).count
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Setup").font(.headline)
                Spacer()
                Text("\(ready) of \(items.count) ready").font(.callout).foregroundStyle(.secondary)
            }
            ProgressView(value: Double(ready), total: Double(max(items.count, 1)))
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
                            .foregroundStyle(item.ready ? Color.green : Color.secondary)
                    }
                    .font(.callout)
                    .accessibilityElement(children: .combine)
                    .accessibilityValue(item.ready ? "Ready" : "Not ready")
                }
            }
        }
        .card()
    }
}
