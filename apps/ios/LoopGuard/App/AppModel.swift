import Foundation
import Observation

@MainActor
@Observable
final class AppModel {
    private(set) var sessions: AsyncViewState<[SessionSummary]> = .idle
    private(set) var inbox: AsyncViewState<[InboxItem]> = .idle
    private(set) var changes: AsyncViewState<[ChangeSummary]> = .idle
    private(set) var hosts: AsyncViewState<[HostSummary]> = .idle
    private(set) var devices: AsyncViewState<[DeviceSummary]> = .idle
    let auth: AuthSession
    let pairing: DevicePairingCoordinator
    let fixtureMode: Bool
    private let fixtureScenario: String?
    private let api: ControlAPI
    private let deviceKeyStore: DeviceKeyStore

    var subject: String {
        if fixtureMode { return "developer@loopguard.dev" }
        if case .signedIn(let subject) = auth.state { return subject }
        return "Signed-in developer"
    }

    init(
        api: ControlAPI,
        auth: AuthSession,
        pairing: DevicePairingCoordinator,
        deviceKeyStore: DeviceKeyStore,
        fixtureMode: Bool = false,
        fixtureScenario: String? = nil
    ) {
        self.api = api
        self.auth = auth
        self.pairing = pairing
        self.deviceKeyStore = deviceKeyStore
        self.fixtureMode = fixtureMode
        self.fixtureScenario = fixtureScenario
    }

    func loadSessions() async {
        await loadDashboard()
    }

    func loadDashboard() async {
        if fixtureMode {
            loadFixtures(scenario: fixtureScenario)
            return
        }

        let previousSessions = sessions.value
        let previousChanges = changes.value
        let previousHosts = hosts.value
        let previousDevices = devices.value
        sessions = .loading
        changes = .loading
        hosts = .loading
        devices = .loading
        inbox = .loading

        async let loadedSessions: AsyncViewState<[SessionSummary]> = loadCollection(
            path: "v1/sessions",
            previous: previousSessions
        )
        async let loadedChanges: AsyncViewState<[ChangeSummary]> = loadCollection(
            path: "v1/changes",
            previous: previousChanges
        )
        async let loadedHosts: AsyncViewState<[HostSummary]> = loadCollection(
            path: "v1/hosts",
            previous: previousHosts
        )
        async let loadedDevices: AsyncViewState<[DeviceSummary]> = loadCollection(
            path: "v1/devices",
            previous: previousDevices
        )
        let results = await (loadedSessions, loadedChanges, loadedHosts, loadedDevices)
        guard !Task.isCancelled else { return }
        sessions = results.0
        changes = results.1
        hosts = results.2
        devices = results.3
        updateInbox(from: results.0)
    }

    func logout() {
        try? deviceKeyStore.revoke()
        auth.logout()
        sessions = .idle
        inbox = .idle
        changes = .idle
        hosts = .idle
        devices = .idle
    }

    private func loadCollection<Item: Decodable & Sendable>(
        path: String,
        previous: [Item]?
    ) async -> AsyncViewState<[Item]> {
        do {
            let response: APICollection<Item> = try await api.request(path: path)
            return response.items.isEmpty ? .empty : .loaded(response.items)
        } catch let ControlAPIError.problem(_, problem) {
            if let previous { return .stale(previous) }
            return .failed(message: problem.detail, requestID: problem.requestID)
        } catch ControlAPIError.authenticationRequired, AuthSessionError.authenticationRequired {
            if let previous { return .stale(previous) }
            return .failed(message: "Sign in to connect this device.", requestID: nil)
        } catch is CancellationError {
            return previous.map(AsyncViewState.stale) ?? .idle
        } catch {
            if let previous { return .stale(previous) }
            return .failed(message: "LoopGuard could not reach the control API.", requestID: nil)
        }
    }

    private func updateInbox(from sessionsState: AsyncViewState<[SessionSummary]>) {
        guard let values = sessionsState.value else {
            inbox = sessionsState.isStale ? .stale([]) : .empty
            return
        }
        let items = values.compactMap(Self.inboxItem)
        let sorted = InboxItem.sort(items)
        if sorted.isEmpty {
            inbox = .empty
        } else {
            inbox = sessionsState.isStale ? .stale(sorted) : .loaded(sorted)
        }
    }

    private static func inboxItem(_ session: SessionSummary) -> InboxItem? {
        let state = session.state?.lowercased() ?? ""
        let severity = session.severity?.lowercased() ?? ""
        let needsAttention = session.requiresAttention == true
        let kind: InboxKind
        if severity == "regression" {
            kind = .regression
        } else if state == "looping" || severity == "loop" {
            kind = .loop
        } else if state == "waiting_for_approval" {
            kind = .approval
        } else if session.verification?.verdict == "failed" {
            kind = .verificationFailed
        } else if state == "completed" {
            kind = .completed
        } else if needsAttention {
            kind = .approval
        } else {
            return nil
        }
        return InboxItem(
            id: "\(session.id)-\(kind.rawValue)",
            sessionID: session.id,
            kind: kind,
            title: session.name ?? "Run \(session.id.prefix(8))",
            summary: session.summary ?? Self.defaultInboxSummary(kind),
            updatedAt: session.updatedAt ?? .distantPast,
            requiresAttention: kind != .completed
        )
    }

    private static func defaultInboxSummary(_ kind: InboxKind) -> String {
        switch kind {
        case .regression: "A deterministic verification changed from passing to failing."
        case .loop: "The run repeated work without observable progress."
        case .approval: "This run is waiting for an explicit decision."
        case .verificationFailed: "Required verification did not pass."
        case .completed: "All required verification passed."
        }
    }

    private func loadFixtures(scenario: String?) {
        if scenario == "loading" {
            sessions = .loading
            inbox = .loading
            changes = .loading
            hosts = .loading
            devices = .loading
            return
        }
        if scenario == "empty" {
            sessions = .empty
            inbox = .empty
            changes = .empty
            hosts = .empty
            devices = .empty
            return
        }
        if scenario == "error" {
            let state = AsyncViewState<[SessionSummary]>.failed(
                message: "The control service returned a scoped fixture error.",
                requestID: "req_ui_fixture"
            )
            sessions = state
            inbox = .failed(message: "The safety inbox could not be refreshed.", requestID: "req_ui_fixture")
            changes = .failed(message: "Changes are temporarily unavailable.", requestID: "req_ui_fixture")
            hosts = .failed(message: "Host status is temporarily unavailable.", requestID: "req_ui_fixture")
            devices = .failed(message: "Device status is temporarily unavailable.", requestID: "req_ui_fixture")
            return
        }
        let run = SessionSummary(
            id: "run-auth-migration",
            name: "Harden authentication migration",
            state: "blocked",
            repository: "acme/control-plane",
            updatedAt: Date(timeIntervalSince1970: 1_783_001_800),
            phase: "Verification",
            agent: "Codex",
            model: "GPT-5",
            effort: "High",
            cost: "$2.41 observed",
            summary: "A deterministic regression appeared after the token rotation change.",
            requiresAttention: true,
            severity: "regression",
            verification: VerificationSummary(
                verdict: "failed",
                command: "swift test --parallel",
                artifactID: "verify_01JY4W"
            ),
            events: scenario == "resync"
                ? [
                    .fixture(sessionSeq: 1, clientStreamSeq: 1, id: "evt-plan", kind: "plan_completed"),
                    .fixture(sessionSeq: 3, clientStreamSeq: 2, id: "evt-test", kind: "verification_failed"),
                ]
                : [
                    .fixture(sessionSeq: 1, clientStreamSeq: 1, id: "evt-plan", kind: "plan_completed"),
                    .fixture(sessionSeq: 2, clientStreamSeq: 2, id: "evt-code", kind: "implementation_completed"),
                    .fixture(sessionSeq: 3, clientStreamSeq: 3, id: "evt-test", kind: "verification_failed"),
                ]
        )
        let quietRun = SessionSummary(
            id: "run-docs",
            name: "Refresh API reference",
            state: "completed",
            repository: "acme/docs",
            updatedAt: Date(timeIntervalSince1970: 1_783_001_200),
            phase: "Complete",
            agent: "Claude Code",
            model: "Sonnet",
            effort: "Medium",
            cost: "$0.62 observed",
            summary: "Reference pages were updated and every link check passed.",
            requiresAttention: false,
            verification: VerificationSummary(
                verdict: "passed",
                command: "npm run check:links",
                artifactID: "verify_01JY4Q"
            ),
            events: [
                .fixture(sessionSeq: 1, clientStreamSeq: 1, id: "evt-docs", kind: "documentation_completed"),
                .fixture(sessionSeq: 2, clientStreamSeq: 2, id: "evt-links", kind: "verification_passed"),
            ]
        )
        sessions = .loaded([run, quietRun])
        inbox = .loaded(InboxItem.sort([.regression, .loop, .completed]))
        changes = .loaded([
            ChangeSummary(
                id: "change-auth-refresh",
                summary: "Rotate access tokens without interrupting active runs",
                repository: "acme/control-plane",
                state: "blocked",
                actor: "Codex",
                source: "Observed working tree",
                branch: "feat/token-rotation",
                createdAt: Date(timeIntervalSince1970: 1_783_001_500),
                diff: """
                - token = cached_access_token
                + token = await token_vault.rotated_access_token()
                + audit.record("access_token_rotated")
                """,
                verification: VerificationSummary(
                    verdict: "failed",
                    command: "swift test --parallel",
                    artifactID: "verify_01JY4W"
                )
            ),
        ])
        hosts = .loaded([
            HostSummary(
                id: "host-studio",
                name: "Studio Mac",
                state: "ready",
                repository: "acme/control-plane",
                adapterVersion: "0.1.0",
                updatedAt: Date(timeIntervalSince1970: 1_783_001_700)
            ),
            HostSummary(
                id: "host-ci",
                name: "CI runner",
                state: "offline",
                repository: "acme/docs",
                adapterVersion: "0.1.0",
                updatedAt: Date(timeIntervalSince1970: 1_782_900_000)
            ),
            HostSummary(
                id: "host-trust",
                name: "New workstation",
                state: "trust_required",
                repository: "acme/mobile",
                adapterVersion: "0.1.0",
                updatedAt: Date(timeIntervalSince1970: 1_783_001_300)
            ),
            HostSummary(
                id: "host-partial",
                name: "Partial hook coverage",
                state: "partial",
                repository: "acme/legacy",
                adapterVersion: "outdated",
                updatedAt: Date(timeIntervalSince1970: 1_783_001_100)
            ),
        ])
        devices = .loaded([
            DeviceSummary(
                id: "device-phone",
                name: "Developer’s iPhone",
                algorithm: "P-256",
                keyID: "lgk_a12f9c7b",
                createdAt: Date(timeIntervalSince1970: 1_782_800_000),
                revokedAt: nil
            ),
        ])
        if scenario == "stale" {
            sessions = .stale(sessions.value ?? [])
            inbox = .stale(inbox.value ?? [])
            changes = .stale(changes.value ?? [])
            hosts = .stale(hosts.value ?? [])
            devices = .stale(devices.value ?? [])
        }
    }
}
