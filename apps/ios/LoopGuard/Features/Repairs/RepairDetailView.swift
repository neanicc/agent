import SwiftUI

struct RepairDetailView: View {
    let repairID: String
    let load: (String) async throws -> RepairDetail
    let preparePublication: (RepairDetail) async throws -> ActionViewModel
    @State private var state: AsyncViewState<RepairDetail> = .loading
    @State private var actionPresentation: RepairActionPresentation?
    @State private var actionError: String?

    var body: some View {
        AsyncStateView(
            state: state,
            emptyTitle: "Repair not found",
            retry: { Task { await refresh() } }
        ) { repair in
            List {
                Section {
                    VStack(alignment: .leading, spacing: 10) {
                        ResponsiveStatusHeading(
                            title: "Repair \(repair.id.prefix(8))",
                            label: repair.state.replacingOccurrences(
                                of: "_",
                                with: " "
                            ).capitalized,
                            state: repair.state,
                            titleFont: .title2.weight(.bold)
                        )
                        Text("Publication is bound to the exact state version and hash.")
                            .foregroundStyle(SemanticColor.secondaryText)
                    }
                }

                Section("Workflow evidence") {
                    RepairFact(
                        label: "Repository",
                        value: repair.repositoryID,
                        monospaced: true
                    )
                    RepairFact(
                        label: "Failure",
                        value: shortFingerprint(repair.failureFingerprint),
                        monospaced: true
                    )
                    RepairFact(
                        label: "State version",
                        value: String(repair.stateVersion),
                        monospaced: true
                    )
                    RepairFact(
                        label: "State hash",
                        value: shortFingerprint(repair.stateHash),
                        monospaced: true
                    )
                }

                Section("Reproduction proof") {
                    StatusBadge(
                        label: reproductionPassed(repair.reproduction)
                            ? "Reproduced"
                            : "Inconclusive",
                        state: reproductionPassed(repair.reproduction)
                            ? "passed"
                            : "inconclusive"
                    )
                    RepairFact(
                        label: "Attempts",
                        value: repair.reproduction.attempts.map(String.init)
                            ?? "Not reported",
                        monospaced: true
                    )
                    RepairFact(
                        label: "Artifact",
                        value: repair.reproduction.artifactID
                            ?? repair.reproduction.outputArtifactID
                            ?? "Not reported",
                        monospaced: true
                    )
                    RepairFact(
                        label: "Assurance",
                        value: repair.reproduction.assurance ?? "Not reported"
                    )
                }

                Section("Candidate evidence") {
                    if repair.candidates.isEmpty {
                        Text("No candidate cleared bounded generation.")
                            .foregroundStyle(SemanticColor.secondaryText)
                    }
                    ForEach(repair.candidates) { candidate in
                        RepairCandidateView(
                            candidate: candidate,
                            isWinner: candidate.id
                                == repair.ranking.winningCandidateID
                        )
                    }
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Deterministic ranking")
                            .font(LoopGuardTypography.status)
                            .foregroundStyle(SemanticColor.secondaryText)
                            .textCase(.uppercase)
                        Text(
                            repair.ranking.reason
                                ?? "No deterministic winner was reported."
                        )
                    }
                    .padding(.vertical, 4)
                }

                Section {
                    StatusBadge(
                        label: repair.publication.status
                            .replacingOccurrences(of: "_", with: " ")
                            .capitalized,
                        state: repair.publication.status
                    )
                    Text("LoopGuard creates a draft pull request. It cannot merge or deploy it.")
                        .foregroundStyle(SemanticColor.secondaryText)
                    if let url = repair.publication.pullRequestURL {
                        RepairFact(
                            label: "Draft pull request",
                            value: url,
                            monospaced: true
                        )
                    }
                    if repair.state == "awaiting_publication" {
                        Button("Review draft publication") {
                            Task { await reviewPublication(repair) }
                        }
                        .buttonStyle(LoopGuardPrimaryButtonStyle())
                        .accessibilityIdentifier("repair-publication-review")
                    }
                    if let actionError {
                        Label(
                            actionError,
                            systemImage: "exclamationmark.triangle"
                        )
                        .foregroundStyle(SemanticColor.danger)
                    }
                } header: {
                    Text("Publication")
                } footer: {
                    Text(
                        "A lost response is reconciled by action ID. Candidate evaluation and publication are never silently repeated."
                    )
                }

                Section("Rollback") {
                    Text(repair.rollback)
                }
            }
            .refreshable { await refresh() }
        }
        .navigationTitle("Repair detail")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("repair-detail-screen")
        .sheet(item: $actionPresentation) { presentation in
            NavigationStack {
                ActionReviewView(model: presentation.model)
            }
        }
        .task(id: repairID) { await refresh() }
    }

    private func refresh() async {
        let previous = state.value
        state = .loading
        do {
            state = .loaded(try await load(repairID))
        } catch is CancellationError {
            state = previous.map(AsyncViewState.stale) ?? .idle
        } catch {
            state = previous.map(AsyncViewState.stale) ?? .failed(
                message: "Repair evidence could not be loaded.",
                requestID: nil
            )
        }
    }

    private func reviewPublication(_ repair: RepairDetail) async {
        do {
            actionError = nil
            actionPresentation = RepairActionPresentation(
                model: try await preparePublication(repair)
            )
        } catch {
            actionError = "A current signed-action challenge could not be created."
        }
    }
}

private struct RepairActionPresentation: Identifiable {
    let model: ActionViewModel

    var id: String { model.request.actionID }
}

private struct RepairCandidateView: View {
    let candidate: RepairCandidate
    let isWinner: Bool

    var body: some View {
        DisclosureGroup {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    CandidateCheck(
                        label: "Replay",
                        result: candidate.evaluation?.replay
                    )
                    CandidateCheck(
                        label: "Regression",
                        result: candidate.evaluation?.regression
                    )
                    CandidateCheck(
                        label: "Security",
                        result: candidate.evaluation?.security
                    )
                }
                .accessibilityElement(children: .contain)

                RepairFact(
                    label: "Contract",
                    value: candidate.evaluation?.contractBreaking == true
                        ? "Breaking"
                        : "Compatible"
                )
                RepairFact(
                    label: "Patch",
                    value: "\(candidate.changedFiles.count) files · \(candidate.changedLines) lines"
                )
                if !candidate.changedFiles.isEmpty {
                    Text(candidate.changedFiles.joined(separator: ", "))
                        .font(LoopGuardTypography.telemetry)
                        .foregroundStyle(SemanticColor.secondaryText)
                        .textSelection(.enabled)
                }
                if let diff = candidate.diff {
                    ScrollView(.horizontal) {
                        Text(diff)
                            .font(LoopGuardTypography.telemetry)
                            .textSelection(.enabled)
                    }
                    .accessibilityLabel("Bounded candidate diff")
                }
            }
            .padding(.vertical, 6)
        } label: {
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(candidate.id)
                        .font(LoopGuardTypography.telemetry)
                    if isWinner {
                        StatusBadge(label: "Winner", state: "passed")
                    }
                }
                Text(candidate.strategy ?? "Strategy not reported")
                    .font(.subheadline)
                    .foregroundStyle(SemanticColor.secondaryText)
            }
        }
    }
}

private struct CandidateCheck: View {
    let label: String
    let result: RepairCheckResult?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(LoopGuardTypography.status)
            StatusBadge(
                label: (result ?? .inconclusive).label,
                state: (result ?? .inconclusive).state
            )
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct RepairFact: View {
    let label: String
    let value: String
    var monospaced = false

    var body: some View {
        LabeledContent(label) {
            Text(value)
                .font(
                    monospaced
                        ? LoopGuardTypography.telemetry
                        : LoopGuardTypography.body
                )
                .multilineTextAlignment(.trailing)
                .textSelection(.enabled)
        }
    }
}

private func reproductionPassed(_ value: RepairReproduction) -> Bool {
    value.reproduced == true || value.status == "reproduced"
}
