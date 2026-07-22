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
