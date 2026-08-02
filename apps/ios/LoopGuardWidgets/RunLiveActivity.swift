import ActivityKit
import SwiftUI
import WidgetKit

struct RunLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: RunActivityAttributes.self) { context in
            RunActivityLockScreenView(context: context)
                .widgetURL(context.attributes.deepLink)
                .activityBackgroundTint(Color(uiColor: .secondarySystemBackground))
                .activitySystemActionForegroundColor(.primary)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    Label("LoopGuard", systemImage: "checkmark.shield")
                        .font(.caption.weight(.semibold))
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Text(context.state.status)
                        .font(.caption)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    ActivityProgress(state: context.state)
                }
            } compactLeading: {
                Image(systemName: "checkmark.shield")
            } compactTrailing: {
                if let progress = context.state.progress {
                    Text(progress, format: .percent.precision(.fractionLength(0)))
                } else {
                    Image(systemName: "ellipsis")
                }
            } minimal: {
                Image(systemName: "checkmark.shield")
            }
            .widgetURL(context.attributes.deepLink)
        }
    }
}

private struct RunActivityLockScreenView: View {
    let context: ActivityViewContext<RunActivityAttributes>

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label("LoopGuard", systemImage: "checkmark.shield")
                    .font(.headline)
                Spacer()
                Text(context.state.status)
                    .font(.caption.weight(.semibold))
            }
            ActivityProgress(state: context.state)
        }
        .padding()
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            "LoopGuard run. \(context.state.phase). \(context.state.status)."
        )
    }
}

private struct ActivityProgress: View {
    let state: RunActivityAttributes.ContentState

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(state.phase)
                .font(.subheadline.weight(.medium))
                .lineLimit(1)
            if let progress = state.progress {
                ProgressView(value: progress)
                    .tint(.accentColor)
            } else {
                ProgressView()
                    .controlSize(.small)
            }
        }
    }
}

#Preview("Running", as: .content, using: RunActivityAttributes.preview) {
    RunLiveActivity()
} contentStates: {
    RunActivityAttributes.ContentState.previewRunning
    RunActivityAttributes.ContentState.previewVerifying
}

#Preview("Dynamic Island", as: .dynamicIsland(.expanded), using: RunActivityAttributes.preview) {
    RunLiveActivity()
} contentStates: {
    RunActivityAttributes.ContentState.previewRunning
}

private extension RunActivityAttributes {
    static let preview = RunActivityAttributes(
        runID: "run_preview",
        expiresAt: Date().addingTimeInterval(30 * 60)
    )
}

private extension RunActivityAttributes.ContentState {
    static let previewRunning = RunActivityAttributes.ContentState(
        phase: "Implementing",
        progress: 0.42,
        status: "Running",
        updatedAt: Date()
    )
    static let previewVerifying = RunActivityAttributes.ContentState(
        phase: "Verifying",
        progress: 0.84,
        status: "Checking",
        updatedAt: Date()
    )
}
