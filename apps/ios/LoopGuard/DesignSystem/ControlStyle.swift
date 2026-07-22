import SwiftUI

struct LoopGuardPrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.body.weight(.semibold))
            .frame(maxWidth: .infinity, minHeight: ControlMetrics.minimumTouchTarget)
            .padding(.horizontal, 16)
            .foregroundStyle(Color.white)
            .background(
                configuration.isPressed
                    ? SemanticColor.accent.opacity(0.78)
                    : SemanticColor.accent,
                in: RoundedRectangle(cornerRadius: ControlMetrics.controlRadius, style: .continuous)
            )
            .contentShape(Rectangle())
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
