import Foundation
import Observation

@MainActor
@Observable
final class AppModel {
    private(set) var sessions: AsyncViewState<[SessionSummary]> = .idle
    let auth: AuthSession
    let pairing: DevicePairingCoordinator
    private let api: ControlAPI
    private let deviceKeyStore: DeviceKeyStore

    init(
        api: ControlAPI,
        auth: AuthSession,
        pairing: DevicePairingCoordinator,
        deviceKeyStore: DeviceKeyStore
    ) {
        self.api = api
        self.auth = auth
        self.pairing = pairing
        self.deviceKeyStore = deviceKeyStore
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
        } catch AuthSessionError.authenticationRequired {
            sessions = .idle
        } catch is CancellationError {
            return
        } catch {
            sessions = .failed(message: "LoopGuard could not reach the control API.", requestID: nil)
        }
    }

    func logout() {
        try? deviceKeyStore.revoke()
        auth.logout()
        sessions = .idle
    }
}
