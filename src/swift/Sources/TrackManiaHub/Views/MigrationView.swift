import SwiftUI

/// Shown instead of the titles while files of an earlier release wait to move into the
/// data folder: until they have, the titles would look uninstalled.
struct MigrationView: View {
    @ObservedObject var model: HubViewModel
    let migration: HubMigration

    var body: some View {
        if model.migrating {
            MigrationProgress(activity: model.activity, destination: migration.to)
        } else {
            ContentUnavailableView {
                Label("Your games are still in the old folders", systemImage: "folder.badge.questionmark")
            } description: {
                VStack(spacing: 8) {
                    Text(reason)
                    Text(footer)
                        .foregroundStyle(.secondary)
                }
            } actions: {
                Button("Try Again", systemImage: "arrow.clockwise") { model.migrate() }
                    .buttonStyle(.borderedProminent)
            }
        }
    }

    private var footer: String { Self.footer(for: migration) }

    /// Before anything moved, the old apps still find every file; after an interrupted
    /// move they may not, so the hub must finish it first.
    static func footer(for migration: HubMigration) -> String {
        migration.partial
            ? String(localized: """
                Part of the move has already happened, and your Game and Settings apps may not \
                work until it finishes. Finish it here before playing.
                """)
            : String(localized: """
                Nothing was moved. Your Game and Settings apps in Applications keep working \
                with your files where they are.
                """)
    }

    private var reason: String {
        model.migrationFailure ?? migration.reason
            ?? String(localized: "TrackMania Mac moves them into one folder before it can continue.")
    }
}

/// Only this view follows output batches while the files move.
private struct MigrationProgress: View {
    @ObservedObject var activity: InstallerActivity
    let destination: String?

    var body: some View {
        VStack(spacing: 12) {
            ProgressView()
            Text("Moving your games into one folder")
                .font(.headline)
            if let destination {
                Text(destination)
                    .font(.callout.monospaced())
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
            if let stage = activity.progress.stage {
                Text(stage)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
        }
        .padding()
    }
}
