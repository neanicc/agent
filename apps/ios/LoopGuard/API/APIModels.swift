import Foundation

struct APICollection<Item: Decodable & Sendable>: Decodable, Sendable {
    let items: [Item]
    let nextCursor: String?

    enum CodingKeys: String, CodingKey {
        case items
        case nextCursor = "next_cursor"
    }
}

struct SessionSummary: Codable, Identifiable, Sendable {
    let id: String
    let name: String?
    let state: String?
    let repository: String?
    let updatedAt: Date?
    let phase: String?
    let agent: String?
    let model: String?
    let effort: String?
    let cost: String?
    let summary: String?
    let requiresAttention: Bool?
    let severity: String?
    let verification: VerificationSummary?
    let events: [RunTimelineEvent]?

    init(
        id: String,
        name: String? = nil,
        state: String? = nil,
        repository: String? = nil,
        updatedAt: Date? = nil,
        phase: String? = nil,
        agent: String? = nil,
        model: String? = nil,
        effort: String? = nil,
        cost: String? = nil,
        summary: String? = nil,
        requiresAttention: Bool? = nil,
        severity: String? = nil,
        verification: VerificationSummary? = nil,
        events: [RunTimelineEvent]? = nil
    ) {
        self.id = id
        self.name = name
        self.state = state
        self.repository = repository
        self.updatedAt = updatedAt
        self.phase = phase
        self.agent = agent
        self.model = model
        self.effort = effort
        self.cost = cost
        self.summary = summary
        self.requiresAttention = requiresAttention
        self.severity = severity
        self.verification = verification
        self.events = events
    }

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case state
        case repository
        case updatedAt = "updated_at"
        case phase
        case agent
        case model
        case effort
        case cost
        case summary
        case requiresAttention = "requires_attention"
        case severity
        case verification
        case events
    }
}

struct VerificationSummary: Codable, Sendable, Equatable {
    let verdict: String
    let command: String?
    let artifactID: String?

    enum CodingKeys: String, CodingKey {
        case verdict
        case command
        case artifactID = "artifact_id"
    }
}

struct ChangeSummary: Codable, Identifiable, Sendable, Equatable {
    let id: String
    let summary: String
    let repository: String?
    let state: String?
    let actor: String?
    let source: String?
    let branch: String?
    let createdAt: Date?
    let diff: String?
    let verification: VerificationSummary?

    enum CodingKeys: String, CodingKey {
        case id
        case summary
        case repository
        case state
        case actor
        case source
        case branch
        case createdAt = "created_at"
        case diff
        case verification
    }
}

struct HostSummary: Codable, Identifiable, Sendable, Equatable {
    let id: String
    let name: String
    let state: String?
    let repository: String?
    let adapterVersion: String?
    let updatedAt: Date?

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case state
        case repository
        case adapterVersion = "adapter_version"
        case updatedAt = "updated_at"
    }
}

struct DeviceSummary: Codable, Identifiable, Sendable, Equatable {
    let id: String
    let name: String
    let algorithm: String
    let keyID: String
    let createdAt: Date
    let revokedAt: Date?

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case algorithm
        case keyID = "key_id"
        case createdAt = "created_at"
        case revokedAt = "revoked_at"
    }
}

struct APIProblem: Codable, Error, Equatable, Sendable {
    let type: String?
    let title: String
    let detail: String
    let code: String
    let requestID: String?
    let retryable: Bool
    let documentationURL: String?

    enum CodingKeys: String, CodingKey {
        case type
        case title
        case detail
        case code
        case requestID = "request_id"
        case retryable
        case documentationURL = "doc_url"
    }
}

enum ControlAPIError: Error, Equatable, Sendable {
    case authenticationRequired
    case invalidBaseURL
    case invalidResponse
    case problem(status: Int, APIProblem)
}
