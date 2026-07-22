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

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case state
        case repository
        case updatedAt = "updated_at"
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
