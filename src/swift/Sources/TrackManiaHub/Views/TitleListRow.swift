import SwiftUI

extension HubTitle {
    var supportColor: Color { isSupported ? .green : .orange }
}

struct TitleListRow: View {
    let title: HubTitle

    var body: some View {
        HStack(spacing: 11) {
            GameIconView(title: title, size: 38)
            VStack(alignment: .leading, spacing: 3) {
                ViewThatFits(in: .horizontal) {
                    Text(title.name)
                    Text(title.compactName)
                }
                .font(.system(.body, weight: .medium))
                .lineLimit(1)
                Text(title.shortName ?? title.status.rawValue.capitalized)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(title.supportLabel)
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(title.supportColor)
            }
            Spacer(minLength: 2)
            Circle()
                .fill(title.error == nil ? title.supportColor : .orange)
                .frame(width: 7, height: 7)
        }
        .padding(.vertical, 3)
        .tag(title.id)
    }
}
