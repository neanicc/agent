import Foundation

struct RunTimelineEvent: Codable, Identifiable, Sendable, Equatable {
    let sessionSeq: Int
    let clientStreamSeq: Int
    let id: String
    let kind: String
    let phase: String?
    let detail: String?
    let occurredAt: Date?

    enum CodingKeys: String, CodingKey {
        case sessionSeq = "session_seq"
        case clientStreamSeq = "client_stream_seq"
        case id = "event_id"
        case kind
        case phase
        case detail
        case occurredAt = "occurred_at"
    }

    static func fixture(
        sessionSeq: Int,
        clientStreamSeq: Int,
        id: String,
        kind: String = "tool_completed"
    ) -> RunTimelineEvent {
        RunTimelineEvent(
            sessionSeq: sessionSeq,
            clientStreamSeq: clientStreamSeq,
            id: id,
            kind: kind,
            phase: "implementation",
            detail: nil,
            occurredAt: nil
        )
    }
}

struct TimelineState: Sendable, Equatable {
    private(set) var items: [RunTimelineEvent] = []
    private(set) var lastSessionSeq = 0
    private(set) var lastClientStreamSeq = 0
    private var pending: [Int: RunTimelineEvent] = [:]
    private var eventIDs: Set<String> = []

    var isResyncing: Bool { !pending.isEmpty }

    mutating func apply(_ event: RunTimelineEvent) {
        lastClientStreamSeq = max(lastClientStreamSeq, event.clientStreamSeq)
        guard event.sessionSeq > lastSessionSeq, !eventIDs.contains(event.id) else { return }
        if lastSessionSeq > 0, event.sessionSeq > lastSessionSeq + 1 {
            pending[event.sessionSeq] = event
            return
        }
        append(event)
        while let next = pending.removeValue(forKey: lastSessionSeq + 1) {
            append(next)
        }
    }

    private mutating func append(_ event: RunTimelineEvent) {
        guard eventIDs.insert(event.id).inserted else { return }
        items.append(event)
        items.sort { lhs, rhs in
            lhs.sessionSeq == rhs.sessionSeq
                ? lhs.clientStreamSeq < rhs.clientStreamSeq
                : lhs.sessionSeq < rhs.sessionSeq
        }
        lastSessionSeq = max(lastSessionSeq, event.sessionSeq)
    }
}
