import Testing
@testable import LoopGuard

@Suite("Safety inbox ordering")
struct InboxOrderingTests {
    @Test("blocking work sorts before informational completion")
    func inboxOrdersBlockingBeforeInformational() {
        let sorted = InboxItem.sort([.completed, .regression, .loop])

        #expect(sorted.map(\.kind) == [.regression, .loop, .completed])
    }

    @Test("stable priority uses newest first inside a severity")
    func newestFirstWithinSeverity() {
        let older = InboxItem.loop.updated(at: .distantPast)
        let newer = InboxItem.loop.updated(at: .distantFuture)

        #expect(InboxItem.sort([older, newer]).map(\.updatedAt) == [.distantFuture, .distantPast])
    }
}
