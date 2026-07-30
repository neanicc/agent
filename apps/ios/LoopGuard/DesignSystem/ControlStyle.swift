import SwiftUI

struct LoopGuardPrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.body.weight(.semibold))
            .frame(maxWidth: .infinity, minHeight: ControlMetrics.minimumTouchTarget)
            .padding(.horizontal, 16)
            .foregroundStyle(isEnabled ? Color.white : SemanticColor.secondaryText)
            .background(
                backgroundColor(configuration),
                in: RoundedRectangle(cornerRadius: ControlMetrics.controlRadius, style: .continuous)
            )
            .contentShape(Rectangle())
    }

    private func backgroundColor(_ configuration: Configuration) -> Color {
        guard isEnabled else { return SemanticColor.secondaryText.opacity(0.16) }
        return configuration.isPressed
            ? SemanticColor.accent.opacity(0.78)
            : SemanticColor.accent
    }
}

struct NavigationGlassControlModifier: ViewModifier {
    @ViewBuilder
    func body(content: Content) -> some View {
        if #available(iOS 26.0, *) {
            content.buttonStyle(.glass)
        } else {
            content.buttonStyle(.bordered)
        }
    }
}

extension View {
    func navigationGlassControl() -> some View {
        modifier(NavigationGlassControlModifier())
    }
}
