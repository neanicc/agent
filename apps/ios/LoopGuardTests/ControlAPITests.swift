import Foundation
import Testing
@testable import LoopGuard

@Suite("Control API")
struct ControlAPITests {
    @Test("authenticated requests add bearer and request ID")
    func requestAddsBearerAndRequestID() async throws {
        let transport = RecordingTransport(response: .json(["items": []]))
        let api = ControlAPI(
            baseURL: URL(string: "https://api.test")!,
            tokenProvider: { "token" },
            transport: transport
        )

        _ = try await api.sessions(after: nil)

        #expect(await transport.lastRequest?.value(forHTTPHeaderField: "Authorization") == "Bearer token")
        #expect(await transport.lastRequest?.value(forHTTPHeaderField: "X-Request-ID")?.hasPrefix("ios_") == true)
    }

    @Test("problem documents preserve request ID and code")
    func problemDocumentIsTyped() async throws {
        let transport = RecordingTransport(
            response: .problem(status: 409, code: "stale_state", requestID: "req-42")
        )
        let api = ControlAPI(
            baseURL: URL(string: "https://api.test")!,
            tokenProvider: { "token" },
            transport: transport
        )

        await #expect(throws: ControlAPIError.self) {
            _ = try await api.sessions(after: nil)
        }
    }

    @Test("server timestamps with fractional seconds decode")
    func fractionalTimestampDecodes() async throws {
        let transport = RecordingTransport(response: .json([
            "items": [[
                "id": "run_1",
                "updated_at": "2026-07-30T12:34:56.123456Z",
            ]],
        ]))
        let api = ControlAPI(
            baseURL: URL(string: "https://api.test")!,
            tokenProvider: { "token" },
            transport: transport
        )

        let response = try await api.sessions(after: nil)

        #expect(response.items.first?.updatedAt != nil)
    }

    @Test("authenticated action read reconstructs the exact review challenge")
    func actionReviewDecodes() async throws {
        let expiry = "2026-07-30T12:35:26.123456Z"
        let canonicalObject: [String: Any] = [
            "action_id": "act_1",
            "kind": "continue_once",
            "target": ["kind": "session", "target_id": "run_1"],
            "parameters_hash": "sha256:parameters",
            "expected_state_version": 7,
            "expected_state_hash": "sha256:state7",
            "nonce": "nonce_1",
            "expires_at": expiry,
        ]
        let canonical = try JSONSerialization.data(withJSONObject: canonicalObject, options: [.sortedKeys])
        let transport = RecordingTransport(response: .json([
            "action_id": "act_1",
            "target": ["kind": "session", "target_id": "run_1"],
            "kind": "continue_once",
            "state": "reviewed",
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
        ]))
        let api = ControlAPI(
            baseURL: URL(string: "https://api.test")!,
            tokenProvider: { "token" },
            transport: transport
        )

        let review = try await ControlActionAPI(api: api).review(actionID: "act_1")

        #expect(review.canonicalPayload == canonical)
        #expect(review.expectedStateVersion == 7)
        #expect(review.initialState == .reviewed)
    }
}

actor RecordingTransport: ControlTransport {
    enum Stub {
        case json([String: Any])
        case problem(status: Int, code: String, requestID: String)
    }

    private(set) var lastRequest: URLRequest?
    private let response: Stub

    init(response: Stub) {
        self.response = response
    }

    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        lastRequest = request
        switch response {
        case .json(let object):
            return try makeResponse(request: request, status: 200, object: object, contentType: "application/json")
        case .problem(let status, let code, let requestID):
            return try makeResponse(
                request: request,
                status: status,
                object: [
                    "type": "https://docs.loopguard.dev/errors/\(code)",
                    "title": "Action conflict",
                    "detail": "Refresh current state.",
                    "code": code,
                    "request_id": requestID,
                    "retryable": false,
                ],
                contentType: "application/problem+json"
            )
        }
    }

    private func makeResponse(
        request: URLRequest,
        status: Int,
        object: [String: Any],
        contentType: String
    ) throws -> (Data, HTTPURLResponse) {
        let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
        let response = HTTPURLResponse(
            url: request.url!,
            statusCode: status,
            httpVersion: "HTTP/1.1",
            headerFields: ["Content-Type": contentType]
        )!
        return (data, response)
    }
}
