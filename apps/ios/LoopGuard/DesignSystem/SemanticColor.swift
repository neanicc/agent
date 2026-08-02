import SwiftUI

enum SemanticColor {
    static let accent = Color.accentColor
    static let canvas = Color(uiColor: .systemGroupedBackground)
    static let surface = Color(uiColor: .secondarySystemGroupedBackground)
    static let rule = Color(uiColor: .separator)
    static let primaryText = Color.primary
    static let secondaryText = Color.secondary
    static let success = Color(uiColor: .systemGreen)
    static let warning = Color(uiColor: .systemOrange)
    static let danger = Color(uiColor: .systemRed)
}

enum ControlMetrics {
    static let minimumTouchTarget: CGFloat = 44
    static let controlRadius: CGFloat = 10
    static let surfaceRadius: CGFloat = 14
}
