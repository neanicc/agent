import SwiftUI

struct RunListView: View {
    let state: AsyncViewState<[SessionSummary]>
    @Binding var selection: String?
    let refresh: () -> Void

    var body: some View {
        NavigationSplitView {
            AsyncStateView(state: state, emptyTitle: "No runs observed", retry: refresh) { sessions in
                List(sessions, selection: $selection) { session in
                    NavigationLink(value: session.id) {
                        RunRow(session: session)
                    }
                }
                .navigationDestination(for: String.self) { id in
                    if let session = sessions.first(where: { $0.id == id }) {
                        RunDetailView(session: session)
                    }
                }
                .refreshable { refresh() }
            }
            .navigationTitle("Runs")
            .accessibilityIdentifier("runs-screen")
        } detail: {
            if let sessions = state.value,
               let selection,
               let session = sessions.first(where: { $0.id == selection })
            {
                RunDetailView(session: session)
            } else {
                ContentUnavailableView("Select a run", systemImage: "terminal")
            }
        }
    }
}

private struct RunRow: View {
    let session: SessionSummary

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ResponsiveStatusHeading(
                title: session.name ?? "Run \(session.id.prefix(8))",
                label: session.state?.capitalized ?? "Observed",
                state: session.state ?? "observed",
                titleFont: .headline
            )
            Text(session.repository ?? "Repository not reported")
                .font(.subheadline)
                .foregroundStyle(SemanticColor.secondaryText)
            if let phase = session.phase {
                Text(phase.uppercased())
                    .font(LoopGuardTypography.telemetry)
                    .foregroundStyle(SemanticColor.secondaryText)
            }
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }
}
