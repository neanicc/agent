import SwiftUI

struct RepairListView: View {
    let state: AsyncViewState<[RepairSummary]>
    let loadDetail: (String) async throws -> RepairDetail
    let preparePublication: (RepairDetail) async throws -> ActionViewModel
    let refresh: () -> Void
    @State private var selection: String?

    var body: some View {
        NavigationSplitView {
            AsyncStateView(
                state: state,
                emptyTitle: "No eligible repairs",
                retry: refresh
            ) { repairs in
                List(repairs, selection: $selection) { repair in
                    NavigationLink(value: repair.id) {
                        RepairRow(repair: repair)
                    }
                }
                .navigationDestination(for: String.self) { id in
                    RepairDetailView(
                        repairID: id,
                        load: loadDetail,
                        preparePublication: preparePublication
                    )
                }
                .refreshable { refresh() }
            }
            .navigationTitle("Repairs")
            .accessibilityIdentifier("repairs-screen")
        } detail: {
            if let selection {
                RepairDetailView(
                    repairID: selection,
                    load: loadDetail,
                    preparePublication: preparePublication
                )
            } else {
                ContentUnavailableView(
                    "Select a repair",
                    systemImage: "wrench.and.screwdriver"
                )
            }
        }
    }
}

private struct RepairRow: View {
    let repair: RepairSummary

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ResponsiveStatusHeading(
                title: "Pipeline repair \(repair.id.prefix(8))",
                label: repair.state.replacingOccurrences(of: "_", with: " ").capitalized,
                state: repair.state,
                titleFont: .headline,
                normalLineLimit: 2
            )
            Text(shortFingerprint(repair.failureFingerprint))
                .font(LoopGuardTypography.telemetry)
                .foregroundStyle(SemanticColor.secondaryText)
            HStack {
                Text(repair.winningCandidateID ?? "Candidate not ranked")
                Spacer()
                Text(repair.updatedAt, style: .relative)
            }
            .font(.subheadline)
            .foregroundStyle(SemanticColor.secondaryText)
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }
}

func shortFingerprint(_ value: String) -> String {
    let normalized = value.replacingOccurrences(of: "sha256:", with: "")
    guard normalized.count > 20 else { return value }
    return "sha256:\(normalized.prefix(12))…\(normalized.suffix(6))"
}
