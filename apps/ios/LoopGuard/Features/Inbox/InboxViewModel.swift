import Foundation
import Observation

enum InboxKind: String, Codable, Sendable, CaseIterable {
    case regression
    case loop
    case approval
    case verificationFailed = "verification_failed"
    case completed

    var priority: Int {
        switch self {
        case .regression, .approval: 0
        case .loop, .verificationFailed: 1
        case .completed: 9
        }
    }
}

struct InboxItem: Codable, Identifiable, Sendable, Equatable {
    let id: String
    let sessionID: String?
    let kind: InboxKind
    let title: String
    let summary: String
    let updatedAt: Date
    let requiresAttention: Bool

    static func sort(_ items: [InboxItem]) -> [InboxItem] {
        items.sorted { lhs, rhs in
            if lhs.kind.priority != rhs.kind.priority { return lhs.kind.priority < rhs.kind.priority }
            if lhs.updatedAt != rhs.updatedAt { return lhs.updatedAt > rhs.updatedAt }
            return lhs.id < rhs.id
        }
    }

    func updated(at date: Date) -> InboxItem {
        InboxItem(
            id: id,
            sessionID: sessionID,
            kind: kind,
            title: title,
            summary: summary,
            updatedAt: date,
            requiresAttention: requiresAttention
        )
    }

    static let regression = InboxItem(
        id: "regression",
        sessionID: "run-auth-migration",
        kind: .regression,
        title: "Regression needs review",
        summary: "A deterministic verification changed from passing to failing.",
        updatedAt: Date(timeIntervalSince1970: 1_785_000_300),
        requiresAttention: true
    )
    static let loop = InboxItem(
        id: "loop",
        sessionID: "run-auth-migration",
        kind: .loop,
        title: "Agent may be looping",
        summary: "The same tool pattern repeated without progress.",
        updatedAt: Date(timeIntervalSince1970: 1_785_000_200),
        requiresAttention: true
    )
    static let completed = InboxItem(
        id: "completed",
        sessionID: "run-docs",
        kind: .completed,
        title: "Run completed quietly",
        summary: "All required verification passed.",
        updatedAt: Date(timeIntervalSince1970: 1_785_000_100),
        requiresAttention: false
    )
}

@MainActor
@Observable
final class InboxViewModel {
    private(set) var state: AsyncViewState<[InboxItem]>

    init(items: [InboxItem]) {
        let sorted = InboxItem.sort(items)
        state = sorted.isEmpty ? .empty : .loaded(sorted)
    }

    func replace(with items: [InboxItem], stale: Bool = false) {
        let sorted = InboxItem.sort(items)
        if sorted.isEmpty {
            state = .empty
        } else {
            state = stale ? .stale(sorted) : .loaded(sorted)
        }
    }
}
