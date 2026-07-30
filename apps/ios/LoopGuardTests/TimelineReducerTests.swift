import Testing
@testable import LoopGuard

@Suite("Run timeline reducer")
struct TimelineReducerTests {
    @Test("duplicate session sequence is ignored while client cursor advances")
    func duplicateSessionSequenceIsIgnored() {
        var state = TimelineState()
        state.apply(.fixture(sessionSeq: 3, clientStreamSeq: 7, id: "e3"))
        state.apply(.fixture(sessionSeq: 3, clientStreamSeq: 8, id: "e3"))

        #expect(state.items.count == 1)
        #expect(state.lastSessionSeq == 3)
        #expect(state.lastClientStreamSeq == 8)
    }

    @Test("out-of-order gap waits until the missing event arrives")
    func gapReordersDeterministically() {
        var state = TimelineState()
        state.apply(.fixture(sessionSeq: 1, clientStreamSeq: 1, id: "e1"))
        state.apply(.fixture(sessionSeq: 3, clientStreamSeq: 2, id: "e3"))
        #expect(state.isResyncing)
        #expect(state.items.map(\.sessionSeq) == [1])

        state.apply(.fixture(sessionSeq: 2, clientStreamSeq: 3, id: "e2"))

        #expect(state.items.map(\.sessionSeq) == [1, 2, 3])
        #expect(!state.isResyncing)
    }
}
