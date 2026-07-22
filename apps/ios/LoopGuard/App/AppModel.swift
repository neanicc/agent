import Foundation
import Observation

@MainActor
@Observable
final class AppModel {
    private(set) var sessions: AsyncViewState<[SessionSummary]> = .idle
    private let api: ControlAPI

    init(api: ControlAPI) {
        self.api = api
    }

    func loadSessions() async {
        sessions = .loading
        do {
            let response = try await api.sessions(after: nil)
            sessions = response.items.isEmpty ? .empty : .loaded(response.items)
        } catch let ControlAPIError.problem(_, problem) {
            sessions = .failed(message: problem.detail, requestID: problem.requestID)
        } catch ControlAPIError.authenticationRequired {
            sessions = .failed(message: "Sign in to connect this device.", requestID: nil)
        } catch is CancellationError {
            return
        } catch {
            sessions = .failed(message: "LoopGuard could not reach the control API.", requestID: nil)
        }
    }
}
