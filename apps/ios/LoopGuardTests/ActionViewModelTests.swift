import Foundation
import Testing
@testable import LoopGuard

@MainActor
@Suite("Action review state machine")
struct ActionViewModelTests {
    @Test("expired action cannot submit")
    func expiredActionCannotSubmit() async {
        let api = RecordingActionAPI()
        let model = ActionViewModel(
            request: .fixture(expiresAt: Date(timeIntervalSince1970: 99)),
            api: api,
            signer: RecordingActionSigner(),
            now: { Date(timeIntervalSince1970: 100) }
        )

        await model.submit()

        #expect(model.state == .expired)
        #expect(await api.submittedActionIDs.isEmpty)
    }

    @Test("double tap signs and submits once")
    func doubleTapSubmitsOnce() async {
        let api = RecordingActionAPI()
        let signer = RecordingActionSigner()
        let model = ActionViewModel(
            request: .fixture(),
            api: api,
            signer: signer,
            now: { Date(timeIntervalSince1970: 100) }
        )

        async let first: Void = model.submit()
        async let second: Void = model.submit()
        _ = await (first, second)

        #expect(await api.submittedActionIDs == ["act_fixture"])
        #expect(await signer.payloads.count == 1)
        #expect(model.state == .queued)
    }

    @Test("host offline is fail closed")
    func hostOfflineCannotSubmit() async {
        let api = RecordingActionAPI()
        let model = ActionViewModel(
            request: .fixture(hostAvailable: false),
            api: api,
            signer: RecordingActionSigner(),
            now: { Date(timeIntervalSince1970: 100) }
        )

        await model.submit()

        #expect(model.state == .hostOffline)
        #expect(await api.submittedActionIDs.isEmpty)
    }

    @Test("acknowledgement loss reconciles without re-signing")
    func acknowledgementLossReconciles() async {
        let api = RecordingActionAPI(
            submitError: URLError(.timedOut),
            readSnapshot: .fixture(state: .executed, receiptID: "receipt_1")
        )
        let signer = RecordingActionSigner()
        let model = ActionViewModel(
            request: .fixture(),
            api: api,
            signer: signer,
            now: { Date(timeIntervalSince1970: 100) }
        )

        await model.submit()
        await model.submit()

        #expect(model.state == .executed)
        #expect(model.receipt?.id == "receipt_1")
        #expect(await api.submittedActionIDs == ["act_fixture"])
        #expect(await api.readActionIDs == ["act_fixture"])
        #expect(await signer.payloads.count == 1)
    }

    @Test("queued action refreshes to authoritative expiry")
    func queuedActionCanExpire() async {
        let api = RecordingActionAPI(readSnapshot: .fixture(state: .expired))
        let model = ActionViewModel(
            request: .fixture(),
            api: api,
            signer: RecordingActionSigner(),
            now: { Date(timeIntervalSince1970: 100) }
        )

        await model.submit()
        #expect(model.state == .queued)
        await model.refresh()

        #expect(model.state == .expired)
        #expect(await api.submittedActionIDs == ["act_fixture"])
    }

    @Test("revoked device fails before network submission")
    func revokedDeviceFailsClosed() async {
        let api = RecordingActionAPI()
        let model = ActionViewModel(
            request: .fixture(),
            api: api,
            signer: RevokedActionSigner(),
            now: { Date(timeIntervalSince1970: 100) }
        )

        await model.submit()

        #expect(model.state == .revoked)
        #expect(await api.submittedActionIDs.isEmpty)
    }

    @Test("server-side device revocation is terminal after signing")
    func serverRevocationIsTerminal() async {
        let api = RecordingActionAPI(
            submitError: ControlAPIError.problem(
                status: 401,
                APIProblem(
                    type: nil,
                    title: "Device proof required",
                    detail: "Pair this device again.",
                    code: "LGAPI-DEVICE-PROOF-REQUIRED",
                    requestID: "req_1",
                    retryable: false,
                    documentationURL: nil
                )
            )
        )
        let signer = RecordingActionSigner()
        let model = ActionViewModel(
            request: .fixture(),
            api: api,
            signer: signer,
            now: { Date(timeIntervalSince1970: 100) }
        )

        await model.submit()

        #expect(model.state == .revoked)
        #expect(await signer.payloads.count == 1)
        #expect(await api.submittedActionIDs == ["act_fixture"])
        #expect(await api.readActionIDs.isEmpty)
    }

    @Test("high-risk action requires device-owner authorization")
    func highRiskRequiresAuthorization() async {
        let api = RecordingActionAPI()
        let signer = RecordingActionSigner()
        let model = ActionViewModel(
            request: .fixture(risk: .high),
            api: api,
            signer: signer,
            authorizer: DenyActionAuthorizer(),
            now: { Date(timeIntervalSince1970: 100) }
        )

        await model.submit()

        #expect(model.state == .reviewed)
        #expect(await signer.payloads.isEmpty)
        #expect(await api.submittedActionIDs.isEmpty)
    }
}

actor RecordingActionAPI: ActionAPI {
    private(set) var submittedActionIDs: [String] = []
    private(set) var readActionIDs: [String] = []
    let submitError: (any Error)?
    let readSnapshot: ActionSnapshot

    init(
        submitError: (any Error)? = nil,
        readSnapshot: ActionSnapshot = .fixture(state: .queued)
    ) {
        self.submitError = submitError
        self.readSnapshot = readSnapshot
    }

    func submit(_ submission: SignedActionSubmission) async throws -> ActionSnapshot {
        submittedActionIDs.append(submission.actionID)
        if let submitError { throw submitError }
        return .fixture(state: .queued)
    }

    func read(actionID: String) async throws -> ActionSnapshot {
        readActionIDs.append(actionID)
        return readSnapshot
    }
}

actor RecordingActionSigner: ActionSigning {
    private(set) var payloads: [Data] = []

    func sign(_ payload: Data) async throws -> DeviceActionProof {
        payloads.append(payload)
        return DeviceActionProof(
            deviceID: "00000000-0000-0000-0000-000000000001",
            keyID: "dk_fixture",
            algorithm: "Ed25519",
            signature: Data("signature".utf8)
        )
    }
}

struct RevokedActionSigner: ActionSigning {
    func sign(_ payload: Data) async throws -> DeviceActionProof {
        throw DeviceKeyStoreError.keyNotFound
    }
}

struct DenyActionAuthorizer: ActionAuthorizing {
    func authorize(reason: String) async throws -> Bool { false }
}
