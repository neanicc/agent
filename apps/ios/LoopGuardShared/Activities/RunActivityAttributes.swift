import ActivityKit
import Foundation

struct RunActivityAttributes: ActivityAttributes {
    struct ContentState: Codable, Hashable {
        let phase: String
        let progress: Double?
        let status: String
        let updatedAt: Date
    }

    let runID: String
    let expiresAt: Date

    var deepLink: URL {
        var components = URLComponents()
        components.scheme = "loopguard"
        components.host = "runs"
        components.path = "/\(runID)"
        return components.url!
    }
}
