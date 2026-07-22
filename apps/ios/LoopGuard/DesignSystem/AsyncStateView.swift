import SwiftUI

enum AsyncViewState<Value: Sendable>: Sendable {
    case idle
    case loading
    case loaded(Value)
    case empty
    case failed(message: String, requestID: String?)
    case stale(Value)

    var value: Value? {
        switch self {
        case .loaded(let value), .stale(let value): value
        default: nil
        }
    }

    var isStale: Bool {
        if case .stale = self { return true }
        return false
    }
}

struct AsyncStateView<Value: Sendable, Content: View>: View {
    let state: AsyncViewState<Value>
    let emptyTitle: String
    let retry: (() -> Void)?
    @ViewBuilder let content: (Value) -> Content

    var body: some View {
        switch state {
        case .idle, .loading:
            ProgressView("Loading")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .accessibilityIdentifier("async-loading")
        case .empty:
            ContentUnavailableView(emptyTitle, systemImage: "tray")
        case .failed(let message, let requestID):
            ContentUnavailableView {
                Label("Couldn’t load this view", systemImage: "exclamationmark.triangle")
            } description: {
                VStack(spacing: 8) {
                    Text(message)
                    if let requestID { Text("Request \(requestID)").font(LoopGuardTypography.telemetry) }
                }
            } actions: {
                if let retry { Button("Retry", action: retry).navigationGlassControl() }
            }
        case .loaded(let value):
            content(value)
        case .stale(let value):
            VStack(spacing: 0) {
                Label("Showing stale data", systemImage: "wifi.slash")
                    .font(LoopGuardTypography.status)
                    .foregroundStyle(SemanticColor.warning)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding()
                    .background(SemanticColor.warning.opacity(0.12))
                content(value)
            }
        }
    }
}
