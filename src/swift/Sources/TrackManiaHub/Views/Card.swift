import SwiftUI

extension View {
    /// Rounded grouped surface used by the detail sections.
    func card() -> some View {
        padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12))
    }
}

struct Callout: View {
    let text: String
    let systemImage: String
    var tint: Color = .secondary

    var body: some View {
        Label {
            Text(text).fixedSize(horizontal: false, vertical: true)
        } icon: {
            Image(systemName: systemImage).foregroundStyle(tint)
        }
        .font(.callout)
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(tint.opacity(tint == .secondary ? 0.08 : 0.12), in: RoundedRectangle(cornerRadius: 12))
    }
}
