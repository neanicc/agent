import SwiftUI

struct RunDetailView: View {
    let session: SessionSummary

    private var timeline: TimelineState {
        (session.events ?? []).reduce(into: TimelineState()) { state, event in
            state.apply(event)
        }
    }

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 10) {
                    ResponsiveStatusHeading(
                        title: session.name ?? "Run \(session.id.prefix(8))",
                        label: session.state?.capitalized ?? "Observed",
                        state: session.state ?? "observed",
                        titleFont: .title2.weight(.bold)
                    )
                    if let summary = session.summary {
                        Text(summary).foregroundStyle(SemanticColor.secondaryText)
                    }
                }
                .accessibilityElement(children: .combine)
            }

            Section("Current context") {
                DetailRow(label: "Phase", value: session.phase ?? "Not reported")
                DetailRow(label: "Agent", value: session.agent ?? "Not reported")
                DetailRow(
                    label: "Model & effort",
                    value: [session.model, session.effort].compactMap { $0 }.joined(separator: " · ").nilIfEmpty
                        ?? "Not reported"
                )
                DetailRow(label: "Observed cost", value: session.cost ?? "Not reported", monospaced: true)
            }

            if let proof = session.verification {
                Section("Verification proof") {
                    StatusBadge(label: proof.verdict.capitalized, state: proof.verdict)
                    if let command = proof.command {
                        DetailRow(label: "Command", value: command, monospaced: true)
                    }
                    if let artifactID = proof.artifactID {
                        DetailRow(label: "Artifact", value: artifactID, monospaced: true)
                    }
                }
            }

            Section("Timeline") {
                if timeline.isResyncing {
                    Label("Resyncing missing events", systemImage: "arrow.triangle.2.circlepath")
                        .foregroundStyle(SemanticColor.warning)
                }
                if timeline.items.isEmpty {
                    Text("No timeline events reported yet.")
                        .foregroundStyle(SemanticColor.secondaryText)
                } else {
                    ForEach(timeline.items) { event in
                        TimelineRow(event: event)
                    }
                }
            }
        }
        .navigationTitle("Run detail")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("run-detail-screen")
    }
}

private struct DetailRow: View {
    let label: String
    let value: String
    var monospaced = false

    var body: some View {
        LabeledContent(label) {
            Text(value)
                .font(monospaced ? LoopGuardTypography.telemetry : LoopGuardTypography.body)
                .multilineTextAlignment(.trailing)
                .textSelection(.enabled)
        }
    }
}

private struct TimelineRow: View {
    let event: RunTimelineEvent

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "circle.fill")
                .font(.system(size: 7))
                .foregroundStyle(SemanticColor.accent)
                .padding(.top, 7)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(event.kind.replacingOccurrences(of: "_", with: " ").capitalized)
                    .font(.body.weight(.medium))
                if let detail = event.detail {
                    Text(detail).font(.subheadline).foregroundStyle(SemanticColor.secondaryText)
                }
                Text("Event #\(event.sessionSeq)")
                    .font(LoopGuardTypography.telemetry)
                    .foregroundStyle(SemanticColor.secondaryText)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

private extension String {
    var nilIfEmpty: String? { isEmpty ? nil : self }
}
