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

struct CapabilityFeature: Codable, Sendable, Equatable {
    let available: Bool
    let status: String?
    let reason: String?
}

struct EffectiveCapabilities: Codable, Sendable, Equatable {
    let status: String
    let features: [String: CapabilityFeature]
    let observedAt: Date?
    let ttlSeconds: Int

    enum CodingKeys: String, CodingKey {
        case status
        case features
        case observedAt = "observed_at"
        case ttlSeconds = "ttl_seconds"
    }

    func repairIsReady(now: Date = Date()) -> Bool {
        guard status == "ready",
              let repair = features["repair"],
              repair.available,
              repair.status == nil || repair.status == "ready",
              let observedAt,
              ttlSeconds > 0
        else { return false }
        return observedAt.addingTimeInterval(TimeInterval(ttlSeconds)) > now
    }
}

struct RepairSummary: Codable, Identifiable, Sendable, Equatable {
    let id: String
    let repositoryID: String
    let state: String
    let failureFingerprint: String
    let createdAt: Date
    let updatedAt: Date
    let winningCandidateID: String?

    enum CodingKeys: String, CodingKey {
        case id
        case repositoryID = "repository_id"
        case state
        case failureFingerprint = "failure_fingerprint"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case winningCandidateID = "winning_candidate_id"
    }
}

struct RepairReproduction: Codable, Sendable, Equatable {
    let status: String?
    let reproduced: Bool?
    let attempts: Int?
    let artifactID: String?
    let outputArtifactID: String?
    let assurance: String?

    enum CodingKeys: String, CodingKey {
        case status
        case reproduced
        case attempts
        case artifactID = "artifact_id"
        case outputArtifactID = "output_artifact_id"
        case assurance
    }
}

enum RepairCheckResult: Codable, Sendable, Equatable {
    case passed
    case failed
    case inconclusive

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let value = try? container.decode(Bool.self) {
            self = value ? .passed : .failed
            return
        }
        let value = (try? container.decode(String.self))?.lowercased()
        self = switch value {
        case "passed", "pass", "true": .passed
        case "failed", "fail", "false": .failed
        default: .inconclusive
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(label.lowercased())
    }

    var label: String {
        switch self {
        case .passed: "Passed"
        case .failed: "Failed"
        case .inconclusive: "Inconclusive"
        }
    }

    var state: String { label.lowercased() }
}

struct RepairEvaluation: Codable, Sendable, Equatable {
    let replay: RepairCheckResult?
    let regression: RepairCheckResult?
    let security: RepairCheckResult?
    let contractBreaking: Bool?
    let contractChanges: [[String: String]]?
    let artifactIDs: [String]?

    enum CodingKeys: String, CodingKey {
        case replay
        case regression
        case security
        case contractBreaking = "contract_breaking"
        case contractChanges = "contract_changes"
        case artifactIDs = "artifact_ids"
    }
}

struct RepairCandidate: Codable, Identifiable, Sendable, Equatable {
    let id: String
    let strategy: String?
    let changedFiles: [String]
    let changedLines: Int
    let diff: String?
    let patchArtifactID: String?
    let evaluation: RepairEvaluation?

    enum CodingKeys: String, CodingKey {
        case id
        case strategy
        case changedFiles = "changed_files"
        case changedLines = "changed_lines"
        case diff
        case patchArtifactID = "patch_artifact_id"
        case evaluation
    }
}

struct RepairRanking: Codable, Sendable, Equatable {
    let winningCandidateID: String?
    let reason: String?

    enum CodingKeys: String, CodingKey {
        case winningCandidateID = "winning_candidate_id"
        case reason
    }
}

struct RepairPublication: Codable, Sendable, Equatable {
    let status: String
    let pullRequestURL: String?
    let artifactID: String?
    let reason: String?

    enum CodingKeys: String, CodingKey {
        case status
        case pullRequestURL = "pull_request_url"
        case artifactID = "artifact_id"
        case reason
    }
}

struct RepairDetail: Codable, Identifiable, Sendable, Equatable {
    let id: String
    let repositoryID: String
    let state: String
    let failureFingerprint: String
    let createdAt: Date
    let updatedAt: Date
    let winningCandidateID: String?
    let stateVersion: Int
    let stateHash: String
    let reproduction: RepairReproduction
    let candidates: [RepairCandidate]
    let ranking: RepairRanking
    let rollback: String
    let publication: RepairPublication

    enum CodingKeys: String, CodingKey {
        case id
        case repositoryID = "repository_id"
        case state
        case failureFingerprint = "failure_fingerprint"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case winningCandidateID = "winning_candidate_id"
        case stateVersion = "state_version"
        case stateHash = "state_hash"
        case reproduction
        case candidates
        case ranking
        case rollback
        case publication
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
