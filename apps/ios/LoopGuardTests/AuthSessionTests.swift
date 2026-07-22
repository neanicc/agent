import Foundation
import Testing
@testable import LoopGuard

@MainActor
@Suite("OIDC authentication", .serialized)
struct AuthSessionTests {
    @Test("PKCE is fresh, high entropy, and S256")
    func freshPKCE() throws {
        let first = PKCEPair.generate()
        let second = PKCEPair.generate()

        #expect(first.verifier != second.verifier)
        #expect(try #require(Data(base64URLEncoded: first.verifier)).count == 32)
        #expect(first.challenge == PKCEPair.s256(first.verifier))
    }

    @Test("callback state mismatch fails before token exchange")
    func stateMismatch() async throws {
        let exchange = RecordingTokenExchange()
        let validator = StubIDTokenValidator(claims: .fixture())
        let session = makeSession(exchange: exchange, validator: validator)
        let authorization = try session.authorizationRequest()
        let callback = URL(string: "loopguard://oauth/callback?code=code&state=substituted")!

        await #expect(throws: AuthSessionError.invalidState) {
            try await session.complete(callback: callback, transaction: authorization.transaction)
        }
        #expect(await exchange.calls == 0)
    }

    @Test("ID-token nonce mismatch is rejected and tokens are not persisted")
    func nonceMismatch() async throws {
        let exchange = RecordingTokenExchange()
        let validator = StubIDTokenValidator(claims: .fixture(nonce: "substituted"))
        let service = "dev.loopguard.auth-tests.\(UUID().uuidString)"
        let keychain = KeychainStore(service: service)
        let session = makeSession(exchange: exchange, validator: validator, keychain: keychain)
        let authorization = try session.authorizationRequest()
        let callback = URL(
            string: "loopguard://oauth/callback?code=code&state=\(authorization.transaction.state)"
        )!

        await #expect(throws: AuthSessionError.invalidNonce) {
            try await session.complete(callback: callback, transaction: authorization.transaction)
        }
        #expect(try keychain.data(for: AuthSession.accessTokenAccount) == nil)
        #expect(try keychain.data(for: AuthSession.refreshTokenAccount) == nil)
    }

    @Test("expired access token is renewed with the device-only refresh credential")
    func refreshesExpiredAccessToken() async throws {
        let service = "dev.loopguard.auth-tests.\(UUID().uuidString)"
        let keychain = KeychainStore(service: service)
        try keychain.set(Data("expired-access".utf8), for: AuthSession.accessTokenAccount)
        try keychain.set(Data("refresh".utf8), for: AuthSession.refreshTokenAccount)
        try keychain.set(Data("1".utf8), for: AuthSession.accessExpiryAccount)
        try keychain.set(Data("user-1".utf8), for: AuthSession.subjectAccount)
        let session = makeSession(
            exchange: RefreshingTokenExchange(),
            validator: StubIDTokenValidator(claims: .fixture()),
            keychain: keychain
        )

        let token = try await session.accessToken()

        #expect(token == "renewed-access")
        #expect(try keychain.data(for: AuthSession.refreshTokenAccount) == Data("rotated-refresh".utf8))
    }
}

@MainActor
private func makeSession(
    exchange: any TokenExchanging,
    validator: any IDTokenValidating,
    keychain: KeychainStore = KeychainStore(service: "dev.loopguard.auth-tests.\(UUID().uuidString)")
) -> AuthSession {
    AuthSession(
        configuration: .fixture,
        keychain: keychain,
        exchanger: exchange,
        idTokenValidator: validator,
        now: { Date(timeIntervalSince1970: 1_785_000_000) }
    )
}

private actor RecordingTokenExchange: TokenExchanging {
    private(set) var calls = 0

    func exchange(
        code: String,
        verifier: String,
        configuration: OIDCConfiguration
    ) async throws -> OIDCTokenResponse {
        calls += 1
        return OIDCTokenResponse(
            accessToken: "access",
            refreshToken: "refresh",
            idToken: "id-token",
            expiresIn: 300
        )
    }
}

private struct RefreshingTokenExchange: TokenExchanging {
    func exchange(
        code: String,
        verifier: String,
        configuration: OIDCConfiguration
    ) async throws -> OIDCTokenResponse {
        throw AuthSessionError.tokenExchangeRejected
    }

    func refresh(
        refreshToken: String,
        configuration: OIDCConfiguration
    ) async throws -> OIDCTokenResponse {
        #expect(refreshToken == "refresh")
        return OIDCTokenResponse(
            accessToken: "renewed-access",
            refreshToken: "rotated-refresh",
            idToken: nil,
            expiresIn: 300
        )
    }
}

private struct StubIDTokenValidator: IDTokenValidating {
    let claims: IDTokenClaims

    func validate(
        _ token: String,
        configuration: OIDCConfiguration
    ) async throws -> IDTokenClaims {
        claims
    }
}

private extension OIDCConfiguration {
    static let fixture = OIDCConfiguration(
        issuer: URL(string: "https://identity.loopguard.test")!,
        authorizationEndpoint: URL(string: "https://identity.loopguard.test/authorize")!,
        tokenEndpoint: URL(string: "https://identity.loopguard.test/token")!,
        jwksEndpoint: URL(string: "https://identity.loopguard.test/jwks")!,
        clientID: "loopguard-ios",
        callbackScheme: "loopguard",
        scopes: ["openid", "profile", "offline_access"]
    )
}

private extension IDTokenClaims {
    static func fixture(nonce: String = "expected") -> IDTokenClaims {
        IDTokenClaims(
            issuer: "https://identity.loopguard.test",
            subject: "user-1",
            audience: ["loopguard-ios"],
            expiresAt: 1_785_000_300,
            nonce: nonce
        )
    }
}
