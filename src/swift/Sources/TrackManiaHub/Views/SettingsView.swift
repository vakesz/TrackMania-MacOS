import SwiftUI

struct SettingsView: View {
    @AppStorage(HubDefaults.checkForUpdates) private var checkForUpdates = true

    var body: some View {
        Form {
            Toggle("Check for updates automatically", isOn: $checkForUpdates)
        }
        .formStyle(.grouped)
        .frame(width: 420)
        .fixedSize(horizontal: false, vertical: true)
    }
}
