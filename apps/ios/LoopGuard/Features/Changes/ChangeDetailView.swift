import SwiftUI

struct ChangeDetailView: View {
    let change: ChangeSummary

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 10) {
                    ResponsiveStatusHeading(
                        title: change.summary,
                        label: change.state?.capitalized ?? "Observed",
                        state: change.state ?? "observed",
                        titleFont: .title2.weight(.bold)
                    )
                    if let repository = change.repository {
                        Label(repository, systemImage: "shippingbox")
                            .font(.subheadline)
                            .foregroundStyle(SemanticColor.secondaryText)
                    }
                }
                .accessibilityElement(children: .combine)
            }

            Section("Provenance") {
                ChangeDetailRow(label: "Actor", value: change.actor ?? "Not reported")
                ChangeDetailRow(label: "Source", value: change.source ?? "Not reported")
                ChangeDetailRow(label: "Branch", value: change.branch ?? "Not reported", monospaced: true)
                if let createdAt = change.createdAt {
                    LabeledContent("Observed") {
                        Text(createdAt, format: .dateTime)
                    }
                }
            }

            if let proof = change.verification {
                Section("Verification proof") {
                    StatusBadge(label: proof.verdict.capitalized, state: proof.verdict)
                    if let command = proof.command {
                        ChangeDetailRow(label: "Command", value: command, monospaced: true)
                    }
                    if let artifactID = proof.artifactID {
                        ChangeDetailRow(label: "Artifact", value: artifactID, monospaced: true)
                    }
                }
            }

            Section("Observed diff") {
                if let diff = change.diff, !diff.isEmpty {
                    ScrollView(.horizontal) {
                        Text(diff)
                            .font(LoopGuardTypography.telemetry)
                            .textSelection(.enabled)
                            .padding(.vertical, 4)
                    }
                    .accessibilityLabel("Observed code difference")
                } else {
                    Text("No diff artifact was attached.")
                        .foregroundStyle(SemanticColor.secondaryText)
                }
            }
        }
        .navigationTitle("Change detail")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("change-detail-screen")
    }
}

private struct ChangeDetailRow: View {
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
