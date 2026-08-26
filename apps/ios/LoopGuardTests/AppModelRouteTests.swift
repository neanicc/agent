import Foundation
import Testing
@testable import LoopGuard

@MainActor
@Suite("Authenticated notification deep links")
struct AppModelRouteTests {
    @Test("action deep link fetches current review before enabling approval")
    func actionRouteFetchesReview() async throws {
        let expiry = "2030-07-30T12:35:26Z"
        let transport = try RouteTransport(data: JSONSerialization.data(
            withJSONObject: actionResponse(state: "reviewed", expiry: expiry),
            options: [.sortedKeys]
        ))
        let model = makeModel(transport: transport)

        await model.handleNotificationRoute(.action(id: "act_1"))

        #expect(model.pendingAction?.state == .reviewed)
        #expect(model.pendingAction?.canSubmit == true)
        #expect(await transport.lastPath == "/v1/actions/act_1")
    }

    @Test("expired deep link remains fail closed after refresh")
    func expiredActionRouteIsDisabled() async throws {
        let transport = try RouteTransport(data: JSONSerialization.data(
            withJSONObject: actionResponse(
                state: "expired",
                expiry: "2020-07-30T12:35:26Z"
            ),
            options: [.sortedKeys]
        ))
        let model = makeModel(transport: transport)

        await model.handleNotificationRoute(.action(id: "act_expired"))

        #expect(model.pendingAction?.state == .expired)
        #expect(model.pendingAction?.canSubmit == false)
    }

    @Test("a failed action refresh preserves the inbox and surfaces a route error")
    func failedActionRouteKeepsInbox() async throws {
        let model = makeModel(transport: FailingTransport())

        await model.handleNotificationRoute(.action(id: "act_1"))

        #expect(model.pendingAction == nil)
        #expect(model.routeErrorMessage != nil)
        if case .failed = model.inbox {
            Issue.record("a route failure must not replace the inbox state")
        }
    }

    @Test("a session deep link outside the loaded runs keeps them visible")
    func missingSessionRouteKeepsRuns() async throws {
        let model = makeModel(transport: FailingTransport(), fixtureMode: true)
        await model.loadDashboard()
        let loadedCount = model.sessions.value?.count ?? 0
        #expect(loadedCount > 0)

        await model.handleNotificationRoute(.session(id: "run-that-does-not-exist"))

        #expect(model.routeErrorMessage != nil)
        #expect(model.sessions.value?.count == loadedCount)
    }

    private func makeModel(transport: any ControlTransport, fixtureMode: Bool = false) -> AppModel {
        let keychain = KeychainStore(service: "dev.loopguard.route-tests.\(UUID().uuidString)")
        let api = ControlAPI(
            baseURL: URL(string: "https://api.test")!,
            tokenProvider: { "token" },
            transport: transport
        )
        let configuration = OIDCConfiguration(
            issuer: URL(string: "https://identity.test")!,
            authorizationEndpoint: URL(string: "https://identity.test/authorize")!,
            tokenEndpoint: URL(string: "https://identity.test/token")!,
            jwksEndpoint: URL(string: "https://identity.test/jwks")!,
            clientID: "loopguard-ios",
            callbackScheme: "loopguard",
            scopes: ["openid"]
        )
        let keyStore = DeviceKeyStore(
            keychain: keychain,
            secureEnclaveAvailable: { false }
        )
        return AppModel(
            api: api,
            auth: AuthSession(configuration: configuration, keychain: keychain),
            pairing: DevicePairingCoordinator(api: UnusedPairingAPI(), keyStore: keyStore),
            notifications: NotificationManager(),
            deviceKeyStore: keyStore,
            fixtureMode: fixtureMode
        )
    }

    private func actionResponse(state: String, expiry: String) throws -> [String: Any] {
        let canonicalObject: [String: Any] = [
            "action_id": state == "expired" ? "act_expired" : "act_1",
            "kind": "continue_once",
            "target": ["kind": "session", "target_id": "run_1"],
            "parameters_hash": "sha256:parameters",
            "expected_state_version": 7,
            "expected_state_hash": "sha256:state7",
            "nonce": "nonce_1",
            "expires_at": expiry,
        ]
        let canonical = try JSONSerialization.data(
            withJSONObject: canonicalObject,
            options: [.sortedKeys]
        )
        return [
            "action_id": canonicalObject["action_id"]!,
            "target": ["kind": "session", "target_id": "run_1"],
            "kind": "continue_once",
            "state": state,
            "target_label": "Run 1",
            "effect": "Continue once.",
            "risk": "medium",
            "parameters_hash": "sha256:parameters",
            "expected_state": "State 7 must match.",
            "expected_state_version": 7,
            "expected_state_hash": "sha256:state7",
            "nonce": "nonce_1",
            "expires_at": expiry,
            "canonical_payload": canonical.base64URLEncodedString,
            "host_available": true,
            "requires_biometric": false,
        ]
    }
}

private actor RouteTransport: ControlTransport {
    private let data: Data
    private(set) var lastPath: String?

    init(data: Data) {
        self.data = data
    }

    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        lastPath = request.url?.path
        return (
            data,
            HTTPURLResponse(
                url: request.url!,
                statusCode: 200,
                httpVersion: "HTTP/1.1",
                headerFields: ["Content-Type": "application/json"]
            )!
        )
    }
}

private actor FailingTransport: ControlTransport {
    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        throw URLError(.notConnectedToInternet)
    }
}

private actor UnusedPairingAPI: DevicePairingAPI {
    func start() async throws -> PairingChallenge {
        throw DevicePairingError.invalidServerResponse
    }

    func complete(_ request: PairingCompletionRequest) async throws -> PairedDevice {
        throw DevicePairingError.invalidServerResponse
    }
}
