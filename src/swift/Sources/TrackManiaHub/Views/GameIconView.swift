import SwiftUI

struct GameIconView: View {
    let title: HubTitle
    let size: CGFloat

    var body: some View {
        Group {
            if let path = title.icon, let image = IconCache.shared.image(atPath: path) {
                Image(nsImage: image)
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .padding(size * 0.08)
            } else {
                Text(FallbackIcon.label(for: title))
                    .font(.system(size: max(9, size * 0.24), weight: .heavy, design: .rounded))
                    .minimumScaleFactor(0.6)
                    .foregroundStyle(.white)
                    .padding(size * 0.13)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(FallbackIcon.color(for: title))
            }
        }
        .frame(width: size, height: size)
        .background(.quaternary)
        .clipShape(RoundedRectangle(cornerRadius: size * 0.22, style: .continuous))
    }
}
