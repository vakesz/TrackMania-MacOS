import SwiftUI

struct UpdateBanner: View {
    let info: UpdateInfo
    let dismiss: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "arrow.down.circle.fill")
                .font(.title2)
                .foregroundStyle(.tint)
                .accessibilityHidden(true)
            Text("TrackMania Mac \(info.version.description) is available")
                .font(.callout.weight(.medium))
            Spacer()
            Button("Download") { NSWorkspace.shared.open(info.url) }
                .buttonStyle(.borderedProminent)
            Button("Not Now", action: dismiss)
        }
        .padding(14)
        .background(Color.accentColor.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
    }
}
