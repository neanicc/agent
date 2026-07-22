import Foundation

protocol ControlTransport: Sendable {
    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse)
}

struct URLSessionControlTransport: ControlTransport {
    private let session: URLSession

    init(session: URLSession = .shared) {
        self.session = session
    }

    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw ControlAPIError.invalidResponse
        }
        return (data, httpResponse)
    }
}

struct ControlAPI: Sendable {
    typealias TokenProvider = @Sendable () async throws -> String

    private let baseURL: URL
    private let tokenProvider: TokenProvider
    private let transport: any ControlTransport
    private let decoder: JSONDecoder

    init(
        baseURL: URL,
        tokenProvider: @escaping TokenProvider,
        transport: any ControlTransport = URLSessionControlTransport()
    ) {
        self.baseURL = baseURL
        self.tokenProvider = tokenProvider
        self.transport = transport
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        self.decoder = decoder
    }

    func sessions(after cursor: String?) async throws -> APICollection<SessionSummary> {
        var query: [URLQueryItem] = []
        if let cursor, !cursor.isEmpty {
            query.append(URLQueryItem(name: "page_cursor", value: cursor))
        }
        return try await request(path: "v1/sessions", query: query)
    }

    func request<Response: Decodable & Sendable>(
        path: String,
        method: String = "GET",
        query: [URLQueryItem] = [],
        body: Data? = nil
    ) async throws -> Response {
        let token = try await tokenProvider()
        guard !token.isEmpty else { throw ControlAPIError.authenticationRequired }
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false),
              components.scheme == "https" || components.host == "127.0.0.1" || components.host == "localhost"
        else {
            throw ControlAPIError.invalidBaseURL
        }
        let basePath = components.path.hasSuffix("/") ? components.path : components.path + "/"
        components.path = basePath + path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        components.queryItems = query.isEmpty ? nil : query
        guard let url = components.url else { throw ControlAPIError.invalidBaseURL }

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.httpBody = body
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 30
        request.setValue("application/json, application/problem+json", forHTTPHeaderField: "Accept")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("ios_\(UUID().uuidString.lowercased())", forHTTPHeaderField: "X-Request-ID")
        if body != nil {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }

        let (data, response) = try await transport.data(for: request)
        guard (200 ..< 300).contains(response.statusCode) else {
            if let problem = try? decoder.decode(APIProblem.self, from: data) {
                throw ControlAPIError.problem(status: response.statusCode, problem)
            }
            throw ControlAPIError.invalidResponse
        }
        do {
            return try decoder.decode(Response.self, from: data)
        } catch {
            throw ControlAPIError.invalidResponse
        }
    }
}
