import SwiftUI

struct WelcomeView: View {
    let dismiss: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Welcome to TrackMania Mac")
                .font(.largeTitle.weight(.semibold))
            VStack(alignment: .leading, spacing: 14) {
                point("cpu", "Rosetta 2 is required. The installer adds it if it is missing.")
                point("arrow.down.circle", "Each title downloads roughly 1–2 GB.")
                point("network", "The first time a game starts it shows its own \"Network check\". Click OK.")
                point("macwindow.on.rectangle", "Game windows can open behind other windows. Look for them in Mission Control or the Dock.")
                point("exclamationmark.triangle", "\"Provisional\" means a title installs but is not fully qualified yet. \"Unsupported\" titles are listed but not expected to run yet.")
            }
            HStack {
                Spacer()
                Button("Get Started", action: dismiss)
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(28)
        .frame(width: 480)
    }

    private func point(_ symbol: String, _ text: LocalizedStringKey) -> some View {
        Label {
            Text(text).fixedSize(horizontal: false, vertical: true)
        } icon: {
            Image(systemName: symbol).foregroundStyle(.tint).frame(width: 24)
        }
    }
}
