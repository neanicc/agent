import Testing
@testable import LoopGuard

@Suite("Native design system")
struct DesignSystemTests {
    @Test("interactive controls retain a 44 point touch target")
    func touchTarget() {
        #expect(ControlMetrics.minimumTouchTarget == 44)
    }

    @Test("async state preserves stale content as a distinct state")
    func staleStateIsExplicit() {
        let state = AsyncViewState<String>.stale("Last valid run")
        #expect(state.value == "Last valid run")
        #expect(state.isStale)
    }
}
