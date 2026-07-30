import Foundation
import Testing
@testable import LoopGuard

@Suite("Privacy-safe notification routing")
struct NotificationManagerTests {
    @Test("generic action payload routes by opaque identifier")
    func actionRoute() throws {
        let envelope = try NotificationEnvelope.decode([
            "type": "action",
            "action_id": "act_01JY4W",
            "event_id": "push_1",
            "aps": ["alert": "LoopGuard needs your attention"],
        ])

        #expect(envelope.route == .action(id: "act_01JY4W"))
        #expect(envelope.eventID == "push_1")
    }

    @Test("sensitive notification keys are rejected")
    func sensitivePayloadIsRejected() {
        #expect(throws: NotificationEnvelopeError.sensitiveContent) {
            try NotificationEnvelope.decode([
                "type": "session",
                "session_id": "run_1",
                "repository": "private/repository",
                "prompt": "secret prompt",
            ])
        }
    }

    @Test("deep links accept only bounded action and run routes")
    func deepLinksAreBounded() throws {
        #expect(try NotificationRoute(url: URL(string: "loopguard://actions/act_1")!) == .action(id: "act_1"))
        #expect(try NotificationRoute(url: URL(string: "loopguard://runs/run_1")!) == .session(id: "run_1"))
        #expect(throws: NotificationEnvelopeError.invalidRoute) {
            try NotificationRoute(url: URL(string: "https://example.com/runs/run_1")!)
        }
    }

    @Test("duplicate deliveries do not replace the first routed event")
    @MainActor
    func duplicateDeliveryIsIdempotent() {
        let manager = NotificationManager()
        let first: [AnyHashable: Any] = [
            "type": "action",
            "action_id": "act_1",
            "event_id": "push_1",
        ]
        let duplicate: [AnyHashable: Any] = [
            "type": "session",
            "session_id": "run_2",
            "event_id": "push_1",
        ]

        #expect(manager.handle(userInfo: first) == .action(id: "act_1"))
        #expect(manager.handle(userInfo: duplicate) == .action(id: "act_1"))
    }

    @Test("live activity links only an opaque run identifier and has a bounded lifetime")
    func liveActivityAttributesAreBounded() {
        let attributes = RunActivityAttributes(
            runID: "run_1",
            expiresAt: Date(timeIntervalSince1970: 8 * 60 * 60)
        )

        #expect(attributes.deepLink == URL(string: "loopguard://runs/run_1"))
        #expect(attributes.expiresAt.timeIntervalSince1970 == 8 * 60 * 60)
    }
}
