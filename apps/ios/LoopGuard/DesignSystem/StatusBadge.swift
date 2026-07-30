import SwiftUI

struct StatusBadge: View {
    let label: String
    let state: String
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        Group {
            if dynamicTypeSize >= .accessibility4 {
                VStack(alignment: .leading, spacing: 8) {
                    Image(systemName: symbol)
                        .accessibilityHidden(true)
                    Text(label)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .font(LoopGuardTypography.status)
                .foregroundStyle(color)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Status: \(label)")
            } else if dynamicTypeSize.isAccessibilitySize {
                badge.frame(maxWidth: .infinity, alignment: .leading)
            } else {
                badge
            }
        }
    }

    private var badge: some View {
        Label(label, systemImage: symbol)
            .font(LoopGuardTypography.status)
            .foregroundStyle(color)
            .fixedSize(horizontal: !dynamicTypeSize.isAccessibilitySize, vertical: true)
            .accessibilityLabel("Status: \(label)")
    }

    private var color: Color {
        if matches("passed", "ready", "completed", "executed", "active") { return SemanticColor.success }
        if matches("failed", "blocked", "rejected", "regression", "revoked", "offline", "expired") {
            return SemanticColor.danger
        }
        if matches("warning", "loop", "stale", "pending", "partial", "outdated", "trust", "resync") {
            return SemanticColor.warning
        }
        return SemanticColor.secondaryText
    }

    private var symbol: String {
        if matches("passed", "ready", "completed", "executed", "active") { return "checkmark.circle.fill" }
        if matches("failed", "blocked", "rejected", "regression", "revoked", "offline", "expired") {
            return "exclamationmark.octagon.fill"
        }
        if matches("warning", "loop", "stale", "pending", "partial", "outdated", "trust", "resync") {
            return "exclamationmark.triangle.fill"
        }
        return "circle.fill"
    }

    private func matches(_ fragments: String...) -> Bool {
        let normalized = state.lowercased()
        return fragments.contains { normalized.contains($0) }
    }
}

struct ResponsiveStatusHeading: View {
    let title: String
    let label: String
    let state: String
    var titleFont: Font = .headline
    var normalLineLimit: Int?
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        Group {
            if dynamicTypeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 8) {
                    titleText
                    StatusBadge(label: label, state: state)
                }
            } else {
                HStack(alignment: .firstTextBaseline) {
                    titleText
                    Spacer(minLength: 12)
                    StatusBadge(label: label, state: state)
                }
            }
        }
    }

    private var titleText: some View {
        Text(title)
            .font(titleFont)
            .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : normalLineLimit)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}
