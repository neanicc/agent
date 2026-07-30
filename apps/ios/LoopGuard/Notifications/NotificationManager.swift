import ActivityKit
import Foundation
import Observation
import UIKit
import UserNotifications

enum NotificationRoute: Sendable, Equatable {
    case action(id: String)
    case session(id: String)

    init(url: URL) throws {
        guard url.scheme == "loopguard",
              let host = url.host,
              ["actions", "runs"].contains(host),
              url.query == nil,
              url.fragment == nil
        else {
            throw NotificationEnvelopeError.invalidRoute
        }
        let parts = url.pathComponents.filter { $0 != "/" }
        guard parts.count == 1, Self.validIdentifier(parts[0]) else {
            throw NotificationEnvelopeError.invalidRoute
        }
        self = host == "actions" ? .action(id: parts[0]) : .session(id: parts[0])
    }

    var url: URL {
        switch self {
        case .action(let id): URL(string: "loopguard://actions/\(id)")!
        case .session(let id): URL(string: "loopguard://runs/\(id)")!
        }
    }

    fileprivate static func validIdentifier(_ value: String) -> Bool {
        !value.isEmpty
            && value.utf8.count <= 256
            && value.unicodeScalars.allSatisfy {
                CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "_-.")).contains($0)
            }
    }
}

enum NotificationEnvelopeError: Error, Equatable {
    case invalidRoute
    case sensitiveContent
}

struct NotificationEnvelope: Sendable, Equatable {
    let route: NotificationRoute
    let eventID: String?

    static func decode(_ userInfo: [AnyHashable: Any]) throws -> NotificationEnvelope {
        let keys = Set(userInfo.keys.compactMap { $0 as? String })
        let sensitive = Set([
            "prompt", "source", "path", "repository", "parameters", "parameters_hash",
            "diff", "command", "artifact", "target_label", "effect",
        ])
        guard keys.isDisjoint(with: sensitive) else {
            throw NotificationEnvelopeError.sensitiveContent
        }
        let allowed = Set(["aps", "type", "action_id", "session_id", "event_id"])
        guard keys.isSubset(of: allowed),
              let type = userInfo["type"] as? String
        else {
            throw NotificationEnvelopeError.invalidRoute
        }
        let route: NotificationRoute
        switch type {
        case "action":
            guard let id = userInfo["action_id"] as? String,
                  NotificationRoute.validIdentifier(id)
            else {
                throw NotificationEnvelopeError.invalidRoute
            }
            route = .action(id: id)
        case "session":
            guard let id = userInfo["session_id"] as? String,
                  NotificationRoute.validIdentifier(id)
            else {
                throw NotificationEnvelopeError.invalidRoute
            }
            route = .session(id: id)
        default:
            throw NotificationEnvelopeError.invalidRoute
        }
        let eventID = userInfo["event_id"] as? String
        guard eventID == nil || NotificationRoute.validIdentifier(eventID!) else {
            throw NotificationEnvelopeError.invalidRoute
        }
        return NotificationEnvelope(route: route, eventID: eventID)
    }
}

@MainActor
@Observable
final class NotificationManager {
    static let shared = NotificationManager()

    private(set) var route: NotificationRoute?
    private(set) var authorizationDenied = false
    private(set) var deviceToken: String?
    private(set) var registrationFailed = false
    private var handledEventIDs: Set<String> = []

    func requestAuthorization() async -> Bool {
        do {
            let granted = try await UNUserNotificationCenter.current().requestAuthorization(
                options: [.alert, .badge, .sound]
            )
            authorizationDenied = !granted
            if granted {
                UIApplication.shared.registerForRemoteNotifications()
            }
            return granted
        } catch {
            authorizationDenied = true
            return false
        }
    }

    func synchronizeRegistration() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        switch settings.authorizationStatus {
        case .authorized, .provisional, .ephemeral:
            authorizationDenied = false
            UIApplication.shared.registerForRemoteNotifications()
        case .denied:
            authorizationDenied = true
        case .notDetermined:
            authorizationDenied = false
        @unknown default:
            authorizationDenied = true
        }
    }

    @discardableResult
    func handle(userInfo: [AnyHashable: Any]) -> NotificationRoute? {
        guard let envelope = try? NotificationEnvelope.decode(userInfo) else { return nil }
        return handle(envelope: envelope)
    }

    @discardableResult
    func handle(envelope: NotificationEnvelope) -> NotificationRoute? {
        if let eventID = envelope.eventID, !handledEventIDs.insert(eventID).inserted {
            return route
        }
        route = envelope.route
        return route
    }

    @discardableResult
    func handle(url: URL) -> NotificationRoute? {
        guard let parsed = try? NotificationRoute(url: url) else { return nil }
        route = parsed
        return parsed
    }

    func clearRoute() {
        route = nil
    }

    func record(deviceToken: Data) {
        self.deviceToken = deviceToken.map { String(format: "%02x", $0) }.joined()
    }

    func recordRegistration(success: Bool) {
        registrationFailed = !success
    }
}

final class NotificationAppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        NotificationManager.shared.record(deviceToken: deviceToken)
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: any Error
    ) {
        NotificationManager.shared.recordRegistration(success: false)
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        guard (try? NotificationEnvelope.decode(notification.request.content.userInfo)) != nil else {
            return []
        }
        return [.banner, .sound]
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        guard let envelope = try? NotificationEnvelope.decode(
            response.notification.request.content.userInfo
        ) else {
            return
        }
        _ = await MainActor.run {
            NotificationManager.shared.handle(envelope: envelope)
        }
    }
}

@MainActor
struct RunActivityController {
    private let maximumLifetime: TimeInterval = 8 * 60 * 60

    func start(runID: String, phase: String) async throws -> RunActivityHandle {
        guard NotificationRoute.validIdentifier(runID) else {
            throw NotificationEnvelopeError.invalidRoute
        }
        let now = Date()
        let attributes = RunActivityAttributes(
            runID: runID,
            expiresAt: now.addingTimeInterval(maximumLifetime)
        )
        let state = RunActivityAttributes.ContentState(
            phase: String(phase.prefix(80)),
            progress: nil,
            status: "Running",
            updatedAt: now
        )
        return RunActivityHandle(
            try Activity.request(
                attributes: attributes,
                content: ActivityContent(state: state, staleDate: now.addingTimeInterval(15 * 60))
            )
        )
    }

    func update(
        _ handle: RunActivityHandle,
        phase: String,
        progress: Double?,
        status: String
    ) async {
        let now = Date()
        if now >= handle.activity.attributes.expiresAt {
            await end(handle, finalStatus: "Ended")
            return
        }
        let state = RunActivityAttributes.ContentState(
            phase: String(phase.prefix(80)),
            progress: progress.map { min(max($0, 0), 1) },
            status: String(status.prefix(40)),
            updatedAt: now
        )
        await handle.activity.update(
            ActivityContent(
                state: state,
                staleDate: min(handle.activity.attributes.expiresAt, now.addingTimeInterval(15 * 60))
            )
        )
    }

    func end(_ handle: RunActivityHandle, finalStatus: String) async {
        let state = RunActivityAttributes.ContentState(
            phase: "Complete",
            progress: 1,
            status: String(finalStatus.prefix(40)),
            updatedAt: Date()
        )
        await handle.activity.end(
            ActivityContent(state: state, staleDate: nil),
            dismissalPolicy: .default
        )
    }
}

/// A deliberately narrow sendability boundary around ActivityKit's reference type.
/// The controller remains the only API allowed to mutate the wrapped activity.
final class RunActivityHandle: @unchecked Sendable {
    fileprivate let activity: Activity<RunActivityAttributes>

    fileprivate init(_ activity: Activity<RunActivityAttributes>) {
        self.activity = activity
    }
}
