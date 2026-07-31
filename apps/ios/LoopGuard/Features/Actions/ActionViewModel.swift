import Foundation
import LocalAuthentication
import Observation

enum ActionState: String, Codable, Sendable, Equatable {
    case reviewed
    case signed
    case queued
    case delivered
    case hostExecuting = "host_executing"
    case reconciling
    case executed
    case rejected
    case expired
    case revoked
    case stale
    case hostOffline = "host_offline"
    case failed

    var isTerminal: Bool {
        [.executed, .rejected, .expired, .revoked, .stale].contains(self)
    }

    var label: String {
        switch self {
        case .reviewed: "Reviewed"
        case .signed: "Signed on device"
        case .queued: "Queued for host"
        case .delivered: "Delivered to host"
        case .hostExecuting: "Host executing"
        case .reconciling: "Reconciling outcome"
        case .executed: "Executed"
        case .rejected: "Rejected"
        case .expired: "Expired"
        case .revoked: "Device revoked"
        case .stale: "Refresh required"
        case .hostOffline: "Host offline"
        case .failed: "Couldn’t verify outcome"
        }
    }
}

enum ActionRisk: String, Codable, Sendable, Equatable {
    case low
    case medium
    case high
}

struct ActionTarget: Codable, Sendable, Equatable {
    let kind: String
    let id: String
    let label: String
}

struct ActionReviewRequest: Identifiable, Sendable, Equatable {
    let actionID: String
    let actionKind: String
    let target: ActionTarget
    let effect: String
    let risk: ActionRisk
    let parametersHash: String
    let expectedState: String
    let expectedStateVersion: Int
    let expectedStateHash: String
    let nonce: String
    let expiresAt: Date
    let canonicalPayload: Data
    let hostAvailable: Bool
    let requiresBiometric: Bool
    let initialState: ActionState

    var id: String { actionID }

    static func fixture(
        expiresAt: Date = Date(timeIntervalSince1970: 300),
        hostAvailable: Bool = true,
        expectedStateVersion: Int = 14,
        risk: ActionRisk = .medium,
        actionKind: String = "continue_once",
        targetKind: String = "session",
        targetID: String = "run-auth-migration",
        targetLabel: String = "Harden authentication migration",
        effect: String = "Continue this paused run once from its verified state.",
        canonicalStateVersion: Int = 14
    ) -> ActionReviewRequest {
        let canonicalVersion = canonicalStateVersion
        let values: [String: Any] = [
            "schema_version": 1,
            "canonicalization_version": 1,
            "action_id": "act_fixture",
            "target": [
                "kind": targetKind,
                "target_id": targetID,
            ],
            "host_id": "host-studio",
            "kind": actionKind,
            "parameters_hash": "sha256:fixture-parameters",
            "expected_state_version": canonicalVersion,
            "expected_state_hash": "sha256:state14",
            "tenant_id": "00000000-0000-0000-0000-000000000010",
            "requested_by": "00000000-0000-0000-0000-000000000020",
            "requested_by_device_id": "00000000-0000-0000-0000-000000000001",
            "issued_at": "1970-01-01T00:01:40Z",
            "expires_at": ActionTimestamp.format(expiresAt),
            "nonce": "fixture-action-nonce",
        ]
        let payload = try! JSONSerialization.data(withJSONObject: values, options: [.sortedKeys])
        return ActionReviewRequest(
            actionID: "act_fixture",
            actionKind: actionKind,
            target: ActionTarget(kind: targetKind, id: targetID, label: targetLabel),
            effect: effect,
            risk: risk,
            parametersHash: "sha256:fixture-parameters",
            expectedState: "Run remains paused at state version \(expectedStateVersion) until host acceptance.",
            expectedStateVersion: expectedStateVersion,
            expectedStateHash: "sha256:state14",
            nonce: "fixture-action-nonce",
            expiresAt: expiresAt,
            canonicalPayload: payload,
            hostAvailable: hostAvailable,
            requiresBiometric: risk == .high,
            initialState: .reviewed
        )
    }
}

struct ActionReceipt: Sendable, Equatable {
    let id: String
    let outcome: ActionState
    let resolvedAt: Date?
}

struct ActionSnapshot: Sendable, Equatable {
    let actionID: String
    let state: ActionState
    let executedAt: Date?
    let receiptID: String?

    static func fixture(
        state: ActionState,
        receiptID: String? = nil
    ) -> ActionSnapshot {
        ActionSnapshot(
            actionID: "act_fixture",
            state: state,
            executedAt: state == .executed ? Date(timeIntervalSince1970: 150) : nil,
            receiptID: receiptID
        )
    }
}

struct DeviceActionProof: Sendable, Equatable {
    let deviceID: String
    let keyID: String
    let algorithm: String
    let signature: Data
}

struct SignedActionSubmission: Sendable, Equatable {
    let actionID: String
    let proof: DeviceActionProof
}

protocol ActionAPI: Sendable {
    func submit(_ submission: SignedActionSubmission) async throws -> ActionSnapshot
    func read(actionID: String) async throws -> ActionSnapshot
}

protocol ActionSigning: Sendable {
    func sign(_ payload: Data) async throws -> DeviceActionProof
}

protocol ActionAuthorizing: Sendable {
    func authorize(reason: String) async throws -> Bool
}

struct DeviceActionSigner: ActionSigning {
    private let suppliedDeviceID: String?
    let keyStore: DeviceKeyStore

    init(deviceID: String? = nil, keyStore: DeviceKeyStore) {
        suppliedDeviceID = deviceID
        self.keyStore = keyStore
    }

    func sign(_ payload: Data) async throws -> DeviceActionProof {
        let capability = try keyStore.prepare()
        let deviceID = try suppliedDeviceID ?? keyStore.pairedDeviceID()
        return try DeviceActionProof(
            deviceID: deviceID,
            keyID: capability.keyID,
            algorithm: capability.algorithm,
            signature: keyStore.sign(payload)
        )
    }
}

struct LocalActionAuthorizer: ActionAuthorizing {
    func authorize(reason: String) async throws -> Bool {
        let context = LAContext()
        var error: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else {
            return false
        }
        return try await context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason)
    }
}

struct AllowActionAuthorizer: ActionAuthorizing {
    func authorize(reason _: String) async throws -> Bool { true }
}

@MainActor
@Observable
final class ActionViewModel {
    let request: ActionReviewRequest
    private(set) var state: ActionState
    private(set) var receipt: ActionReceipt?
    private(set) var errorMessage: String?
    private(set) var isSubmitting = false
    private(set) var deliveryProgress: Int

    private let api: any ActionAPI
    private let signer: any ActionSigning
    private let authorizer: any ActionAuthorizing
    private let now: @Sendable () -> Date
    private var submissionStarted = false

    init(
        request: ActionReviewRequest,
        api: any ActionAPI,
        signer: any ActionSigning,
        authorizer: any ActionAuthorizing = AllowActionAuthorizer(),
        now: @escaping @Sendable () -> Date = Date.init
    ) {
        self.request = request
        self.api = api
        self.signer = signer
        self.authorizer = authorizer
        self.now = now
        let initialState: ActionState = request.expiresAt <= now()
            ? .expired
            : request.hostAvailable
                ? request.initialState
                : .hostOffline
        state = initialState
        deliveryProgress = Self.progress(for: initialState) ?? 0
    }

    var secondsRemaining: Int {
        max(0, Int(request.expiresAt.timeIntervalSince(now()).rounded(.up)))
    }

    var canSubmit: Bool {
        !submissionStarted
            && !isSubmitting
            && request.hostAvailable
            && !state.isTerminal
            && state == .reviewed
            && secondsRemaining > 0
    }

    func submit() async {
        guard !submissionStarted, !isSubmitting else { return }
        guard request.expiresAt > now() else {
            state = .expired
            return
        }
        guard request.hostAvailable else {
            state = .hostOffline
            return
        }
        guard request.canonicalPayloadMatchesReview else {
            state = .stale
            errorMessage = "The signed challenge no longer matches the reviewed action. Refresh before deciding."
            return
        }

        submissionStarted = true
        isSubmitting = true
        defer { isSubmitting = false }

        if request.requiresBiometric {
            do {
                guard try await authorizer.authorize(
                    reason: "Approve \(request.effect)"
                ) else {
                    submissionStarted = false
                    errorMessage = "Device-owner confirmation is required for this high-risk action."
                    return
                }
            } catch {
                submissionStarted = false
                errorMessage = "Device-owner confirmation could not be completed."
                return
            }
        }

        let proof: DeviceActionProof
        do {
            proof = try await signer.sign(request.canonicalPayload)
            state = .signed
            deliveryProgress = max(deliveryProgress, Self.progress(for: .signed) ?? 0)
        } catch {
            state = .revoked
            errorMessage = "This device no longer has a valid signing key. Pair it again before reviewing actions."
            return
        }

        do {
            apply(try await api.submit(SignedActionSubmission(actionID: request.actionID, proof: proof)))
        } catch let ControlAPIError.problem(_, problem)
            where problem.code == "LGAPI-DEVICE-PROOF-REQUIRED"
        {
            state = .revoked
            errorMessage = "This device was revoked or replaced during review. Pair it again before approving actions."
        } catch {
            state = .reconciling
            do {
                apply(try await api.read(actionID: request.actionID))
            } catch {
                state = .failed
                errorMessage = "The outcome is unknown. Refresh current action state; LoopGuard will not sign or submit it again."
            }
        }
    }

    func refresh() async {
        do {
            apply(try await api.read(actionID: request.actionID))
        } catch {
            errorMessage = "Current action state could not be refreshed."
        }
    }

    private func apply(_ snapshot: ActionSnapshot) {
        guard snapshot.actionID == request.actionID else {
            state = .stale
            errorMessage = "The server returned a different action. Refresh before deciding."
            return
        }
        state = snapshot.state
        if let progress = Self.progress(for: snapshot.state) {
            deliveryProgress = max(deliveryProgress, progress)
        }
        if [.executed, .rejected].contains(snapshot.state) {
            receipt = ActionReceipt(
                id: snapshot.receiptID ?? snapshot.actionID,
                outcome: snapshot.state,
                resolvedAt: snapshot.executedAt
            )
        }
    }

    private static func progress(for state: ActionState) -> Int? {
        switch state {
        case .reviewed: 0
        case .signed: 1
        case .queued: 2
        case .delivered: 3
        case .hostExecuting: 4
        case .executed: 5
        default: nil
        }
    }
}

private extension ActionReviewRequest {
    var canonicalPayloadMatchesReview: Bool {
        guard let object = try? JSONSerialization.jsonObject(with: canonicalPayload) as? [String: Any],
              object["action_id"] as? String == actionID,
              object["kind"] as? String == actionKind,
              object["parameters_hash"] as? String == parametersHash,
              (object["expected_state_version"] as? NSNumber)?.intValue == expectedStateVersion,
              object["expected_state_hash"] as? String == expectedStateHash,
              object["nonce"] as? String == nonce,
              let targetObject = object["target"] as? [String: Any],
              targetObject["kind"] as? String == target.kind,
              targetObject["target_id"] as? String == target.id,
              let expiryText = object["expires_at"] as? String,
              let payloadExpiry = ActionTimestamp.parse(expiryText),
              abs(payloadExpiry.timeIntervalSince(expiresAt)) < 0.001
        else {
            return false
        }
        return true
    }
}

struct ControlActionAPI: ActionAPI {
    let api: ControlAPI

    func submit(_ submission: SignedActionSubmission) async throws -> ActionSnapshot {
        let body = try JSONEncoder().encode(ActionSubmissionPayload(submission))
        let response: ActionSnapshotPayload = try await api.request(
            path: "v1/actions",
            method: "POST",
            body: body
        )
        return try response.snapshot()
    }

    func read(actionID: String) async throws -> ActionSnapshot {
        let response: ActionSnapshotPayload = try await api.request(path: "v1/actions/\(actionID)")
        return try response.snapshot()
    }

    func review(actionID: String) async throws -> ActionReviewRequest {
        let response: ActionReviewPayload = try await api.request(path: "v1/actions/\(actionID)")
        return try response.request()
    }

    func createRepairPublication(
        repair: RepairDetail,
        deviceID: String
    ) async throws -> ActionReviewRequest {
        let body = try JSONEncoder().encode(
            RepairPublicationChallengePayload(
                repair: repair,
                deviceID: deviceID
            )
        )
        let challenge: ActionChallengeIdentifier = try await api.request(
            path: "v1/actions/challenge",
            method: "POST",
            body: body
        )
        return try await review(actionID: challenge.actionID)
    }
}

private struct RepairPublicationChallengePayload: Encodable {
    let target: Target
    let deviceID: String
    let kind = "publish_repair"
    let parameters: [String: String] = [:]
    let expectedStateVersion: Int
    let expectedStateHash: String
    let expiresIn = 120

    init(repair: RepairDetail, deviceID: String) {
        target = Target(
            kind: "repair",
            targetID: repair.id,
            hostID: "repair-workflow"
        )
        self.deviceID = deviceID
        expectedStateVersion = repair.stateVersion
        expectedStateHash = repair.stateHash
    }

    enum CodingKeys: String, CodingKey {
        case target
        case deviceID = "device_id"
        case kind
        case parameters
        case expectedStateVersion = "expected_state_version"
        case expectedStateHash = "expected_state_hash"
        case expiresIn = "expires_in"
    }

    struct Target: Encodable {
        let kind: String
        let targetID: String
        let hostID: String

        enum CodingKeys: String, CodingKey {
            case kind
            case targetID = "target_id"
            case hostID = "host_id"
        }
    }
}

private struct ActionChallengeIdentifier: Decodable {
    let actionID: String

    enum CodingKeys: String, CodingKey {
        case actionID = "action_id"
    }
}

private struct ActionSubmissionPayload: Encodable {
    let actionID: String
    let deviceID: String
    let deviceKeyID: String
    let deviceAlgorithm: String
    let deviceSignature: String

    init(_ submission: SignedActionSubmission) {
        actionID = submission.actionID
        deviceID = submission.proof.deviceID
        deviceKeyID = submission.proof.keyID
        deviceAlgorithm = submission.proof.algorithm
        deviceSignature = submission.proof.signature.base64URLEncodedString
    }

    enum CodingKeys: String, CodingKey {
        case actionID = "action_id"
        case deviceID = "device_id"
        case deviceKeyID = "device_key_id"
        case deviceAlgorithm = "device_algorithm"
        case deviceSignature = "device_signature"
    }
}

private struct ActionSnapshotPayload: Decodable {
    let actionID: String
    let state: String
    let executedAt: Date?
    let receiptID: String?

    enum CodingKeys: String, CodingKey {
        case actionID = "action_id"
        case state
        case executedAt = "executed_at"
        case receiptID = "receipt_id"
    }

    func snapshot() throws -> ActionSnapshot {
        guard let parsedState = ActionState.serverValue(state) else {
            throw ControlAPIError.invalidResponse
        }
        return ActionSnapshot(
            actionID: actionID,
            state: parsedState,
            executedAt: executedAt,
            receiptID: receiptID
        )
    }
}

private struct ActionReviewPayload: Decodable {
    let actionID: String
    let target: ActionTargetPayload
    let kind: String
    let state: String
    let targetLabel: String
    let effect: String
    let risk: ActionRisk
    let parametersHash: String
    let expectedState: String
    let expectedStateVersion: Int
    let expectedStateHash: String
    let nonce: String
    let expiresAt: Date
    let canonicalPayload: String
    let hostAvailable: Bool
    let requiresBiometric: Bool

    enum CodingKeys: String, CodingKey {
        case actionID = "action_id"
        case target
        case kind
        case state
        case targetLabel = "target_label"
        case effect
        case risk
        case parametersHash = "parameters_hash"
        case expectedState = "expected_state"
        case expectedStateVersion = "expected_state_version"
        case expectedStateHash = "expected_state_hash"
        case nonce
        case expiresAt = "expires_at"
        case canonicalPayload = "canonical_payload"
        case hostAvailable = "host_available"
        case requiresBiometric = "requires_biometric"
    }

    func request() throws -> ActionReviewRequest {
        guard let payload = Data(base64URLEncoded: canonicalPayload),
              let parsedState = ActionState.serverValue(state)
        else {
            throw ControlAPIError.invalidResponse
        }
        return ActionReviewRequest(
            actionID: actionID,
            actionKind: kind,
            target: ActionTarget(kind: target.kind, id: target.targetID, label: targetLabel),
            effect: effect,
            risk: risk,
            parametersHash: parametersHash,
            expectedState: expectedState,
            expectedStateVersion: expectedStateVersion,
            expectedStateHash: expectedStateHash,
            nonce: nonce,
            expiresAt: expiresAt,
            canonicalPayload: payload,
            hostAvailable: hostAvailable,
            requiresBiometric: requiresBiometric,
            initialState: parsedState
        )
    }
}

private struct ActionTargetPayload: Decodable {
    let kind: String
    let targetID: String

    enum CodingKeys: String, CodingKey {
        case kind
        case targetID = "target_id"
    }
}

private extension ActionState {
    static func serverValue(_ value: String) -> ActionState? {
        switch value {
        case "executing": .hostExecuting
        default: ActionState(rawValue: value)
        }
    }
}

private enum ActionTimestamp {
    static func format(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.string(from: date)
    }

    static func parse(_ value: String) -> Date? {
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return fractional.date(from: value) ?? ISO8601DateFormatter().date(from: value)
    }
}
