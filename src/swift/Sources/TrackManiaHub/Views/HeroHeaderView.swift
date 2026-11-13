import SwiftUI

struct HeroHeaderView: View {
    let title: HubTitle

    var body: some View {
        let tint = FallbackIcon.color(for: title)
        HStack(spacing: 20) {
            GameIconView(title: title, size: 96)
                .shadow(color: .black.opacity(0.25), radius: 10, y: 4)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 10) {
                Text(title.name)
                    .font(.system(size: 32, weight: .bold, design: .rounded))
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 8) {
                    chip(title.supportLabel, color: title.supportColor)
                        .help(supportHelp)
                    chip(title.availability, color: .secondary)
                    if title.requiresSerial == true {
                        chip("Serial key required", systemImage: "key.fill", color: .secondary)
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .padding(24)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.regularMaterial)
        .overlay {
            LinearGradient(colors: [tint.opacity(0.28), tint.opacity(0.05)],
                           startPoint: .topLeading, endPoint: .bottomTrailing)
                .allowsHitTesting(false)
        }
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay { RoundedRectangle(cornerRadius: 16).strokeBorder(tint.opacity(0.25)) }
        .accessibilityElement(children: .contain)
    }

    private var supportHelp: String {
        switch title.status {
        case .supported: String(localized: "Installs and plays; changes are checked against this title")
        case .provisional: String(localized: "Installs, but graphics, DRM and online play are not yet qualified under Wine")
        case .unsupported, .unknown: String(localized: "Listed for completeness; not expected to run yet")
        }
    }

    private func chip(_ text: String, systemImage: String? = nil, color: Color) -> some View {
        HStack(spacing: 4) {
            if let systemImage { Image(systemName: systemImage) }
            Text(text)
        }
        .font(.caption.weight(.medium))
        .foregroundStyle(color == .secondary ? Color.secondary : color)
        .padding(.horizontal, 10)
        .padding(.vertical, 4)
        .background(color.opacity(0.15), in: Capsule())
    }
}
