import SwiftUI

struct ActionReviewView: View {
    @Bindable var model: ActionViewModel
    @State private var now = Date()

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Explicit confirmation")
                        .font(LoopGuardTypography.status)
                        .foregroundStyle(SemanticColor.secondaryText)
                        .textCase(.uppercase)
                    ResponsiveStatusHeading(
                        title: "Review safe action",
                        label: model.state.label,
                        state: model.state.rawValue,
                        titleFont: .title2.weight(.bold)
                    )
                    Text("Acceptance queues a signed request. It is not proof that the host executed it.")
                        .foregroundStyle(SemanticColor.secondaryText)
                }
            }

            Section("Effect") {
                Text(model.request.effect)
                    .font(.body.weight(.semibold))
            }

            Section("Immutable review") {
                ActionFact(label: "Target", value: model.request.target.label)
                ActionFact(label: "Risk", value: model.request.risk.rawValue.capitalized)
                ActionFact(
                    label: "Expected state",
                    value: model.request.expectedState,
                    accessibilityIdentifier: "action-expected-state"
                )
                ActionFact(
                    label: "Parameters",
                    value: model.request.parametersHash,
                    monospaced: true,
                    accessibilityIdentifier: "action-parameters"
                )
                ActionFact(label: "Action ID", value: model.request.actionID, monospaced: true)
                LabeledContent("Expires") {
                    Text(expiryLabel)
                        .foregroundStyle(model.secondsRemaining == 0 ? SemanticColor.danger : SemanticColor.secondaryText)
                }
            }

            Section("Delivery") {
                ForEach(deliveryStates, id: \.rawValue) { state in
                    HStack {
                        Image(systemName: deliverySymbol(for: state))
                            .foregroundStyle(deliveryColor(for: state))
                            .accessibilityHidden(true)
                        Text(state.label)
                        Spacer()
                        if state == model.state {
                            Text("Current")
                                .font(LoopGuardTypography.status)
                                .foregroundStyle(SemanticColor.secondaryText)
                        }
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityIdentifier(
                        state == model.state
                            ? "action-current-state-\(state.rawValue)"
                            : "action-lifecycle-\(state.rawValue)"
                    )
                }
            }

            if let receipt = model.receipt {
                Section("Execution receipt") {
                    StatusBadge(label: receipt.outcome.label, state: receipt.outcome.rawValue)
                    ActionFact(label: "Receipt", value: receipt.id, monospaced: true)
                    if let resolvedAt = receipt.resolvedAt {
                        LabeledContent("Resolved") {
                            Text(resolvedAt, format: .dateTime)
                        }
                    }
                }
            }

            if let error = model.errorMessage {
                Section {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .foregroundStyle(SemanticColor.danger)
                }
            }

            Section {
                Button(buttonLabel) {
                    Task { await model.submit() }
                }
                .buttonStyle(LoopGuardPrimaryButtonStyle())
                .disabled(!model.canSubmit)
                .accessibilityIdentifier("action-approve-button")

                if [.failed, .reconciling, .stale].contains(model.state) {
                    Button("Refresh current state") {
                        Task { await model.refresh() }
                    }
                    .navigationGlassControl()
                }
            } footer: {
                Text("LoopGuard signs the exact reviewed challenge on this device. Changed or stale content requires a new review.")
            }
        }
        .navigationTitle("Action review")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("action-review-screen")
        .task {
            while !Task.isCancelled, !model.state.isTerminal {
                now = Date()
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }

    private var expiryLabel: String {
        model.secondsRemaining == 0 ? "Expired" : "\(model.secondsRemaining) seconds"
    }

    private var buttonLabel: String {
        if model.isSubmitting { return model.state.label }
        return switch model.state {
        case .expired: "Approval expired"
        case .revoked: "Device pairing required"
        case .stale: "Refresh required"
        case .hostOffline: "Host offline"
        case .queued, .delivered, .hostExecuting, .reconciling, .executed, .rejected, .failed:
            model.state.label
        case .reviewed, .signed:
            model.request.requiresBiometric ? "Confirm & approve" : "Approve action"
        }
    }

    private var deliveryStates: [ActionState] {
        let normal: [ActionState] = [.reviewed, .signed, .queued, .delivered, .hostExecuting, .executed]
        return normal.contains(model.state) ? normal : normal + [model.state]
    }

    private func deliverySymbol(for state: ActionState) -> String {
        if state == model.state { return "circle.inset.filled" }
        guard let index = deliveryStates.firstIndex(of: state),
              index < model.deliveryProgress
        else { return "circle" }
        return "checkmark.circle.fill"
    }

    private func deliveryColor(for state: ActionState) -> Color {
        state == model.state ? SemanticColor.accent : SemanticColor.secondaryText
    }
}

private struct ActionFact: View {
    let label: String
    let value: String
    var monospaced = false
    var accessibilityIdentifier: String?

    var body: some View {
        LabeledContent(label) {
            Text(value)
                .font(monospaced ? LoopGuardTypography.telemetry : LoopGuardTypography.body)
                .multilineTextAlignment(.trailing)
                .textSelection(.enabled)
                .accessibilityIdentifier(accessibilityIdentifier ?? "action-fact-\(label)")
        }
    }
}
