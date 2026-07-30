import SwiftUI

struct ChangeListView: View {
    let state: AsyncViewState<[ChangeSummary]>
    let refresh: () -> Void
    @State private var selection: String?

    var body: some View {
        NavigationSplitView {
            AsyncStateView(state: state, emptyTitle: "No changes observed", retry: refresh) { changes in
                List(changes, selection: $selection) { change in
                    NavigationLink(value: change.id) {
                        ChangeRow(change: change)
                    }
                }
                .navigationDestination(for: String.self) { id in
                    if let change = changes.first(where: { $0.id == id }) {
                        ChangeDetailView(change: change)
                    }
                }
                .refreshable { refresh() }
            }
            .navigationTitle("Changes")
            .accessibilityIdentifier("changes-screen")
        } detail: {
            if let changes = state.value,
               let selection,
               let change = changes.first(where: { $0.id == selection })
            {
                ChangeDetailView(change: change)
            } else {
                ContentUnavailableView("Select a change", systemImage: "arrow.triangle.branch")
            }
        }
    }
}

private struct ChangeRow: View {
    let change: ChangeSummary

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ResponsiveStatusHeading(
                title: change.summary,
                label: change.state?.capitalized ?? "Observed",
                state: change.state ?? "observed",
                titleFont: .headline,
                normalLineLimit: 2
            )
            Text(change.repository ?? "Repository not reported")
                .font(.subheadline)
                .foregroundStyle(SemanticColor.secondaryText)
            if let createdAt = change.createdAt {
                Text(createdAt, style: .relative)
                    .font(LoopGuardTypography.telemetry)
                    .foregroundStyle(SemanticColor.secondaryText)
            }
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }
}
