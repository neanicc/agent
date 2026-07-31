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
    private(set) var repairs: AsyncViewState<[RepairSummary]> = .idle
    private(set) var repairCapability: AsyncViewState<EffectiveCapabilities> = .idle
    private(set) var pendingAction: ActionViewModel?
    private(set) var routedSessionID: String?
    let auth: AuthSession
    let pairing: DevicePairingCoordinator
    let notifications: NotificationManager
    let fixtureMode: Bool
    private let fixtureScenario: String?
    private let api: ControlAPI
    private let deviceKeyStore: DeviceKeyStore
    private var registeredPushToken: String?

    var subject: String {
        if fixtureMode { return "developer@loopguard.dev" }
        if case .signedIn(let subject) = auth.state { return subject }
        return "Signed-in developer"
    }

    var repairCapabilityReady: Bool {
        !repairCapability.isStale && repairCapability.value?.repairIsReady() == true
    }

    init(
        api: ControlAPI,
        auth: AuthSession,
        pairing: DevicePairingCoordinator,
        notifications: NotificationManager,
        deviceKeyStore: DeviceKeyStore,
        fixtureMode: Bool = false,
        fixtureScenario: String? = nil
    ) {
        self.api = api
        self.auth = auth
        self.pairing = pairing
        self.notifications = notifications
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
        let previousRepairs = repairs.value
        let previousRepairCapability = repairCapability.value
        sessions = .loading
        changes = .loading
        hosts = .loading
        devices = .loading
        repairs = .loading
        repairCapability = .loading
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
        await loadRepairs(
            hostsState: results.2,
            previousRepairs: previousRepairs,
            previousCapability: previousRepairCapability
        )
    }

    func logout() {
        try? deviceKeyStore.revoke()
        auth.logout()
        sessions = .idle
        inbox = .idle
        changes = .idle
        hosts = .idle
        devices = .idle
        repairs = .idle
        repairCapability = .idle
        pendingAction = nil
        routedSessionID = nil
    }

    func handleNotificationRoute(_ route: NotificationRoute) async {
        defer { notifications.clearRoute() }
        switch route {
        case .session(let id):
            if sessions.value == nil {
                await loadDashboard()
            }
            guard sessions.value?.contains(where: { $0.id == id }) == true else {
                sessions = .failed(
                    message: "The linked run is unavailable or outside your current access.",
                    requestID: nil
                )
                return
            }
            routedSessionID = id
        case .action(let id):
            do {
                let request = try await ControlActionAPI(api: api).review(actionID: id)
                pendingAction = ActionViewModel(
                    request: request,
                    api: ControlActionAPI(api: api),
                    signer: DeviceActionSigner(keyStore: deviceKeyStore),
                    authorizer: LocalActionAuthorizer()
                )
                addActionToInbox(request)
            } catch {
                pendingAction = nil
                inbox = .failed(
                    message: "The linked action could not be refreshed. No approval was enabled.",
                    requestID: nil
                )
            }
        }
    }

    func setRoutedSession(_ id: String?) {
        routedSessionID = id
    }

    func registerPushToken(_ token: String) async {
        guard token != registeredPushToken else { return }
        do {
            let deviceID = try deviceKeyStore.pairedDeviceID()
            let payload = PushTokenRegistration(
                token: token,
                environment: PushTokenRegistration.currentEnvironment
            )
            let body = try JSONEncoder().encode(payload)
            let response: PushTokenRegistrationResponse = try await api.request(
                path: "v1/devices/\(deviceID)/push-token",
                method: "PUT",
                body: body
            )
            guard response.registered else { throw ControlAPIError.invalidResponse }
            registeredPushToken = token
            notifications.recordRegistration(success: true)
        } catch {
            notifications.recordRegistration(success: false)
        }
    }

    func repairDetail(id: String) async throws -> RepairDetail {
        if fixtureMode {
            return Self.fixtureRepairDetail(id: id)
        }
        return try await api.request(path: "v1/repairs/\(id)")
    }

    func prepareRepairPublication(_ repair: RepairDetail) async throws -> ActionViewModel {
        if fixtureMode {
            let model = ActionViewModel(
                request: .fixture(
                    expiresAt: Date().addingTimeInterval(120),
                    expectedStateVersion: repair.stateVersion,
                    risk: .high,
                    actionKind: "publish_repair",
                    targetKind: "repair",
                    targetID: repair.id,
                    targetLabel: "Repair \(repair.id.prefix(8))",
                    effect: "Publish the verified repair as a draft pull request.",
                    canonicalStateVersion: repair.stateVersion
                ),
                api: FixtureActionAPI(),
                signer: FixtureActionSigner(),
                authorizer: AllowActionAuthorizer()
            )
            pendingAction = model
            return model
        }
        let actionAPI = ControlActionAPI(api: api)
        let request = try await actionAPI.createRepairPublication(
            repair: repair,
            deviceID: try deviceKeyStore.pairedDeviceID()
        )
        let model = ActionViewModel(
            request: request,
            api: actionAPI,
            signer: DeviceActionSigner(keyStore: deviceKeyStore),
            authorizer: LocalActionAuthorizer()
        )
        pendingAction = model
        return model
    }

    private func loadRepairs(
        hostsState: AsyncViewState<[HostSummary]>,
        previousRepairs: [RepairSummary]?,
        previousCapability: EffectiveCapabilities?
    ) async {
        guard let hostID = hostsState.value?.first?.id else {
            repairCapability = .empty
            repairs = .empty
            return
        }
        do {
            let capability: EffectiveCapabilities = try await api.request(
                path: "v1/capabilities",
                query: [URLQueryItem(name: "host_id", value: hostID)]
            )
            guard !Task.isCancelled else { return }
            repairCapability = .loaded(capability)
            guard capability.repairIsReady() else {
                repairs = .empty
                return
            }
            let response: APICollection<RepairSummary> = try await api.request(path: "v1/repairs")
            repairs = response.items.isEmpty ? .empty : .loaded(response.items)
        } catch {
            repairCapability = previousCapability.map(AsyncViewState.stale) ?? .failed(
                message: "Repair capability could not be refreshed.",
                requestID: nil
            )
            repairs = previousRepairs.map(AsyncViewState.stale) ?? .failed(
                message: "Repair evidence could not be refreshed.",
                requestID: nil
            )
        }
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

    private func addActionToInbox(_ request: ActionReviewRequest) {
        let action = InboxItem(
            id: "action-\(request.actionID)",
            sessionID: request.target.id,
            kind: .approval,
            title: request.effect,
            summary: "Review expires in \(max(0, Int(request.expiresAt.timeIntervalSinceNow))) seconds.",
            updatedAt: Date(),
            requiresAttention: !request.initialState.isTerminal
        )
        let existing = inbox.value ?? []
        let merged = [action] + existing.filter { $0.id != action.id }
        inbox = .loaded(InboxItem.sort(merged))
    }

    private func loadFixtures(scenario: String?) {
        if scenario == "loading" {
            sessions = .loading
            inbox = .loading
            changes = .loading
            hosts = .loading
            devices = .loading
            repairs = .loading
            repairCapability = .loading
            return
        }
        if scenario == "empty" {
            sessions = .empty
            inbox = .empty
            changes = .empty
            hosts = .empty
            devices = .empty
            repairs = .empty
            repairCapability = .empty
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
            repairs = .failed(message: "Repair evidence is temporarily unavailable.", requestID: "req_ui_fixture")
            repairCapability = .failed(
                message: "Repair capability is temporarily unavailable.",
                requestID: "req_ui_fixture"
            )
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
        if scenario == "action" {
            let request = ActionReviewRequest.fixture(
                expiresAt: Date().addingTimeInterval(300),
                risk: .medium
            )
            pendingAction = ActionViewModel(
                request: request,
                api: FixtureActionAPI(),
                signer: FixtureActionSigner(),
                now: Date.init
            )
            inbox = .loaded(InboxItem.sort([
                InboxItem(
                    id: "approval",
                    sessionID: "run-auth-migration",
                    kind: .approval,
                    title: "Continue once needs approval",
                    summary: "Review the immutable target, expected state, and device signature before queuing.",
                    updatedAt: Date(),
                    requiresAttention: true
                ),
                .regression,
                .completed,
            ]))
        }
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
        if scenario?.hasPrefix("repair") == true {
            repairCapability = .loaded(EffectiveCapabilities(
                status: "ready",
                features: [
                    "repair": CapabilityFeature(
                        available: true,
                        status: "ready",
                        reason: "workflow_registered"
                    ),
                ],
                observedAt: Date(),
                ttlSeconds: 300
            ))
            repairs = .loaded([Self.fixtureRepairSummary])
        } else {
            repairCapability = .empty
            repairs = .empty
        }
        if scenario == "stale" {
            sessions = .stale(sessions.value ?? [])
            inbox = .stale(inbox.value ?? [])
            changes = .stale(changes.value ?? [])
            hosts = .stale(hosts.value ?? [])
            devices = .stale(devices.value ?? [])
            repairs = .stale(repairs.value ?? [])
            if let capability = repairCapability.value {
                repairCapability = .stale(capability)
            }
        }
    }

    private static var fixtureRepairSummary: RepairSummary {
        RepairSummary(
            id: "018f0000-0000-7000-8000-000000000305",
            repositoryID: "018f0000-0000-7000-8000-000000000401",
            state: "awaiting_publication",
            failureFingerprint: String(repeating: "5", count: 64),
            createdAt: Date(timeIntervalSince1970: 1_783_001_500),
            updatedAt: Date(timeIntervalSince1970: 1_783_001_800),
            winningCandidateID: "candidate-1"
        )
    }

    private static func fixtureRepairDetail(id: String) -> RepairDetail {
        RepairDetail(
            id: id,
            repositoryID: fixtureRepairSummary.repositoryID,
            state: "awaiting_publication",
            failureFingerprint: fixtureRepairSummary.failureFingerprint,
            createdAt: fixtureRepairSummary.createdAt,
            updatedAt: fixtureRepairSummary.updatedAt,
            winningCandidateID: "candidate-1",
            stateVersion: 8,
            stateHash: String(repeating: "e", count: 64),
            reproduction: RepairReproduction(
                status: "reproduced",
                reproduced: true,
                attempts: 1,
                artifactID: "artifact-reproduction",
                outputArtifactID: nil,
                assurance: "hosted_isolated"
            ),
            candidates: [
                RepairCandidate(
                    id: "candidate-1",
                    strategy: "Normalize ingestion boundary",
                    changedFiles: ["src/coordinates.py"],
                    changedLines: 8,
                    diff: """
                    - return float(value)
                    + return float(value.trimmingCharacters(in: .whitespaces))
                    """,
                    patchArtifactID: "artifact-patch",
                    evaluation: RepairEvaluation(
                        replay: .passed,
                        regression: .passed,
                        security: .passed,
                        contractBreaking: false,
                        contractChanges: [],
                        artifactIDs: ["artifact-evaluation"]
                    )
                ),
                RepairCandidate(
                    id: "candidate-2",
                    strategy: "Coerce downstream",
                    changedFiles: ["src/export.py"],
                    changedLines: 19,
                    diff: nil,
                    patchArtifactID: "artifact-patch-2",
                    evaluation: RepairEvaluation(
                        replay: .passed,
                        regression: .failed,
                        security: .passed,
                        contractBreaking: false,
                        contractChanges: [],
                        artifactIDs: ["artifact-evaluation-2"]
                    )
                ),
            ],
            ranking: RepairRanking(
                winningCandidateID: "candidate-1",
                reason: "Smallest fully verified compatible patch."
            ),
            rollback: "Revert the draft repair commit and rerun the original pipeline.",
            publication: RepairPublication(
                status: "waiting_for_approval",
                pullRequestURL: nil,
                artifactID: nil,
                reason: nil
            )
        )
    }
}

private struct PushTokenRegistration: Encodable {
    let token: String
    let environment: String

    static var currentEnvironment: String {
        #if DEBUG
        "sandbox"
        #else
        "production"
        #endif
    }
}

private struct PushTokenRegistrationResponse: Decodable, Sendable {
    let registered: Bool
}

private actor FixtureActionAPI: ActionAPI {
    func submit(_ submission: SignedActionSubmission) async throws -> ActionSnapshot {
        ActionSnapshot(
            actionID: submission.actionID,
            state: .queued,
            executedAt: nil,
            receiptID: nil
        )
    }

    func read(actionID: String) async throws -> ActionSnapshot {
        ActionSnapshot(actionID: actionID, state: .queued, executedAt: nil, receiptID: nil)
    }
}

private actor FixtureActionSigner: ActionSigning {
    func sign(_ payload: Data) async throws -> DeviceActionProof {
        DeviceActionProof(
            deviceID: "00000000-0000-0000-0000-000000000001",
            keyID: "dk_fixture",
            algorithm: "Ed25519",
            signature: Data(payload.prefix(16))
        )
    }
}
