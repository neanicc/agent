import SwiftUI

struct InboxView: View {
    let state: AsyncViewState<[InboxItem]>
    let sessions: [SessionSummary]
    let refresh: () -> Void

    var body: some View {
        NavigationStack {
            AsyncStateView(state: state, emptyTitle: "Nothing needs attention", retry: refresh) { items in
                List {
                    let attention = items.filter(\.requiresAttention)
                    let quiet = items.filter { !$0.requiresAttention }
                    if !attention.isEmpty {
                        Section("Needs attention") {
                            ForEach(attention) { item in row(item) }
                        }
                    }
                    if !quiet.isEmpty {
                        Section("Quiet completions") {
                            ForEach(quiet) { item in row(item) }
                        }
                    }
                }
                .listStyle(.insetGrouped)
                .refreshable { refresh() }
            }
            .navigationTitle("Inbox")
            .accessibilityIdentifier("inbox-screen")
        }
    }

    @ViewBuilder
    private func row(_ item: InboxItem) -> some View {
        if let session = sessions.first(where: { $0.id == item.sessionID }) {
            NavigationLink {
                RunDetailView(session: session)
            } label: {
                InboxRow(item: item)
            }
        } else {
            InboxRow(item: item)
        }
    }
}

private struct InboxRow: View {
    let item: InboxItem

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            ResponsiveStatusHeading(
                title: item.title,
                label: label,
                state: item.kind.rawValue,
                titleFont: .headline
            )
            Text(item.summary)
                .font(.subheadline)
                .foregroundStyle(SemanticColor.secondaryText)
                .lineLimit(3)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
            Text(item.updatedAt, style: .relative)
                .font(LoopGuardTypography.telemetry)
                .foregroundStyle(SemanticColor.secondaryText)
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }

    private var label: String {
        switch item.kind {
        case .regression: "Regression"
        case .loop: "Possible loop"
        case .approval: "Approval"
        case .verificationFailed: "Verification failed"
        case .completed: "Completed"
        }
    }
}
