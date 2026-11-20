import AppKit
import SwiftUI

extension HubTitle {
    /// Text and tint of the support label. The system orange and green are too light to read as
    /// text on their own tint in light mode, so light mode uses darker shades of them.
    var supportColor: Color {
        switch status {
        case .supported: Self.supportedColor
        case .provisional: Self.provisionalColor
        case .unsupported, .unknown: .secondary
        }
    }

    static let supportedColor = adaptive(light: NSColor(srgbRed: 0.10, green: 0.48, blue: 0.20, alpha: 1), dark: .systemGreen)
    static let provisionalColor = adaptive(light: NSColor(srgbRed: 0.66, green: 0.30, blue: 0.00, alpha: 1), dark: .systemOrange)

    private static func adaptive(light: NSColor, dark: NSColor) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua ? dark : light
        })
    }
}

struct TitleListRow: View {
    let title: HubTitle
    var isRunning = false
    var fraction: Double?

    var body: some View {
        HStack(spacing: 11) {
            GameIconView(title: title, size: 36)
            VStack(alignment: .leading, spacing: 2) {
                ViewThatFits(in: .horizontal) {
                    Text(title.name)
                    Text(title.compactName)
                }
                .font(.system(.body, weight: .medium))
                .lineLimit(1)
                Text(title.availability)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 2)
            accessory
        }
        .padding(.vertical, 3)
        .tag(title.id)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(accessibilityLabel)
    }

    @ViewBuilder private var accessory: some View {
        if isRunning {
            if let fraction {
                ProgressView(value: fraction).progressViewStyle(.circular).controlSize(.small)
            } else {
                ProgressView().controlSize(.small)
            }
        } else if title.error != nil {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
        } else if !title.isSupported {
            Text(title.supportLabel)
                .font(.caption2.weight(.medium))
                .foregroundStyle(title.supportColor)
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
                .background(title.supportColor.opacity(0.15), in: Capsule())
        }
    }

    private var accessibilityLabel: String {
        var parts = [title.name, title.availability]
        if !title.isSupported { parts.append(title.supportLabel) }
        if isRunning { parts.append(String(localized: "Working")) }
        return parts.joined(separator: ", ")
    }
}
