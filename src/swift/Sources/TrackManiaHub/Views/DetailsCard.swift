import SwiftUI

struct DetailsCard: View {
    let title: HubTitle
    @ObservedObject var model: HubViewModel

    var body: some View {
        let location = title.installLocationPath
        let exists = location.map { FileManager.default.fileExists(atPath: $0) } ?? false
        VStack(alignment: .leading, spacing: 12) {
            Text("Details").font(.headline)
            Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 16, verticalSpacing: 8) {
                if let size = title.sourceSize {
                    row("Download size", ByteCountFormatter.string(fromByteCount: size, countStyle: .file))
                }
                if let installer = title.installerLabel {
                    row("Installer", installer)
                }
                if let location {
                    GridRow {
                        label("Install location")
                        HStack {
                            Text((location as NSString).abbreviatingWithTildeInPath)
                                .lineLimit(1).truncationMode(.middle).textSelection(.enabled)
                            if exists {
                                Button("Show in Finder") { model.reveal(path: location) }
                                    .buttonStyle(.link)
                            }
                        }
                    }
                }
                if let serial = title.requiresSerial {
                    row("Serial key", serial ? String(localized: "Required") : String(localized: "Not required"))
                }
            }
            .font(.callout)
        }
        .card()
    }

    private func label(_ text: LocalizedStringKey) -> some View {
        Text(text).foregroundStyle(.secondary).gridColumnAlignment(.leading)
    }

    private func row(_ name: LocalizedStringKey, _ value: String) -> some View {
        GridRow {
            label(name)
            Text(value).textSelection(.enabled)
        }
    }
}
