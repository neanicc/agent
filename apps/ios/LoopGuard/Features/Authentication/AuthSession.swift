import AuthenticationServices
import CryptoKit
import Foundation
import Observation
import UIKit

struct OIDCConfiguration: Sendable, Equatable {
    let issuer: URL
    let authorizationEndpoint: URL
    let tokenEndpoint: URL
    let jwksEndpoint: URL
    let clientID: String
    let callbackScheme: String
    let scopes: [String]

    var callbackURL: String { "\(callbackScheme)://oauth/callback" }

    func validate() throws {
        guard !clientID.isEmpty, !callbackScheme.isEmpty, scopes.contains("openid") else {
            throw AuthSessionError.invalidConfiguration
        }
        for url in [issuer, authorizationEndpoint, tokenEndpoint, jwksEndpoint] {
            guard url.scheme == "https", url.user == nil, url.password == nil, url.fragment == nil else {
                throw AuthSessionError.invalidConfiguration
            }
        }
    }
}

struct PKCEPair: Sendable, Equatable {
    let verifier: String
    let challenge: String

    static func generate() -> PKCEPair {
        var bytes = [UInt8](repeating: 0, count: 32)
        let status = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        precondition(status == errSecSuccess, "Secure random generation failed")
        let verifier = Data(bytes).base64URLEncodedString
        return PKCEPair(verifier: verifier, challenge: s256(verifier))
    }

    static func s256(_ verifier: String) -> String {
        Data(SHA256.hash(data: Data(verifier.utf8))).base64URLEncodedString
    }
}

struct AuthorizationTransaction: Sendable, Equatable {
    let state: String
    let nonce: String
    let verifier: String
    let createdAt: Date
}

struct OIDCAuthorizationRequest: Sendable, Equatable {
    let url: URL
    let transaction: AuthorizationTransaction
}

struct OIDCTokenResponse: Sendable, Equatable {
    let accessToken: String
    let refreshToken: String?
    let idToken: String?
    let expiresIn: TimeInterval
}

struct IDTokenClaims: Sendable, Equatable {
    let issuer: String
    let subject: String
    let audience: [String]
    let expiresAt: TimeInterval
    let nonce: String
}

protocol TokenExchanging: Sendable {
    func exchange(
        code: String,
        verifier: String,
        configuration: OIDCConfiguration
    ) async throws -> OIDCTokenResponse

    func refresh(
        refreshToken: String,
        configuration: OIDCConfiguration
    ) async throws -> OIDCTokenResponse
}

extension TokenExchanging {
    func refresh(
        refreshToken _: String,
        configuration _: OIDCConfiguration
    ) async throws -> OIDCTokenResponse {
        throw AuthSessionError.authenticationRequired
    }
}

protocol IDTokenValidating: Sendable {
    func validate(
        _ token: String,
        configuration: OIDCConfiguration
    ) async throws -> IDTokenClaims
}

enum AuthenticationState: Sendable, Equatable {
    case signedOut
    case signingIn
    case signedIn(subject: String)
    case failed(message: String)
}

enum AuthSessionError: Error, Equatable {
    case invalidConfiguration
    case invalidCallback
    case invalidState
    case invalidNonce
    case invalidClaims
    case authenticationRequired
    case tokenExchangeRejected
}

@MainActor
@Observable
final class AuthSession {
    nonisolated static let accessTokenAccount = "access-token"
    nonisolated static let refreshTokenAccount = "refresh-token"
    nonisolated static let accessExpiryAccount = "access-expiry"
    nonisolated static let subjectAccount = "subject"

    private(set) var state: AuthenticationState
    let configuration: OIDCConfiguration

    private let keychain: KeychainStore
    private let exchanger: any TokenExchanging
    private let idTokenValidator: any IDTokenValidating
    private let now: @Sendable () -> Date
    private var webAuthenticationSession: ASWebAuthenticationSession?

    init(
        configuration: OIDCConfiguration,
        keychain: KeychainStore,
        exchanger: any TokenExchanging = URLSessionTokenExchanger(),
        idTokenValidator: any IDTokenValidating = RemoteJWKSIDTokenValidator(),
        now: @escaping @Sendable () -> Date = Date.init
    ) {
        self.configuration = configuration
        self.keychain = keychain
        self.exchanger = exchanger
        self.idTokenValidator = idTokenValidator
        self.now = now
        if let subject = try? keychain.string(for: Self.subjectAccount),
           ((try? keychain.data(for: Self.accessTokenAccount)) != nil
               || (try? keychain.data(for: Self.refreshTokenAccount)) != nil)
        {
            state = .signedIn(subject: subject)
        } else {
            state = .signedOut
        }
    }

    func authorizationRequest() throws -> OIDCAuthorizationRequest {
        try configuration.validate()
        let pkce = PKCEPair.generate()
        let transaction = AuthorizationTransaction(
            state: Self.randomValue(),
            nonce: Self.randomValue(),
            verifier: pkce.verifier,
            createdAt: now()
        )
        var components = URLComponents(url: configuration.authorizationEndpoint, resolvingAgainstBaseURL: false)
        components?.queryItems = [
            URLQueryItem(name: "response_type", value: "code"),
            URLQueryItem(name: "client_id", value: configuration.clientID),
            URLQueryItem(name: "redirect_uri", value: configuration.callbackURL),
            URLQueryItem(name: "scope", value: configuration.scopes.joined(separator: " ")),
            URLQueryItem(name: "state", value: transaction.state),
            URLQueryItem(name: "nonce", value: transaction.nonce),
            URLQueryItem(name: "code_challenge", value: pkce.challenge),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
        ]
        guard let url = components?.url else { throw AuthSessionError.invalidConfiguration }
        return OIDCAuthorizationRequest(url: url, transaction: transaction)
    }

    func signIn() async {
        state = .signingIn
        do {
            let request = try authorizationRequest()
            let callback = try await authenticate(request.url)
            try await complete(callback: callback, transaction: request.transaction)
        } catch is CancellationError {
            state = .signedOut
        } catch {
            state = .failed(message: "Sign-in could not be completed. Try again.")
        }
    }

    func complete(callback: URL, transaction: AuthorizationTransaction) async throws {
        guard now().timeIntervalSince(transaction.createdAt) <= 600 else {
            throw AuthSessionError.invalidState
        }
        guard let components = URLComponents(url: callback, resolvingAgainstBaseURL: false),
              components.scheme == configuration.callbackScheme,
              components.host == "oauth",
              components.path == "/callback",
              let code = components.queryValue("code"),
              let callbackState = components.queryValue("state")
        else {
            throw AuthSessionError.invalidCallback
        }
        guard Self.constantTimeEqual(callbackState, transaction.state) else {
            throw AuthSessionError.invalidState
        }

        let tokens = try await exchanger.exchange(
            code: code,
            verifier: transaction.verifier,
            configuration: configuration
        )
        guard let idToken = tokens.idToken else { throw AuthSessionError.invalidClaims }
        let claims = try await idTokenValidator.validate(idToken, configuration: configuration)
        guard Self.constantTimeEqual(claims.nonce, transaction.nonce) else {
            throw AuthSessionError.invalidNonce
        }
        guard claims.issuer == configuration.issuer.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/")),
              claims.audience.contains(configuration.clientID),
              claims.expiresAt > now().timeIntervalSince1970,
              !claims.subject.isEmpty,
              tokens.expiresIn > 0
        else {
            throw AuthSessionError.invalidClaims
        }

        try keychain.set(Data(tokens.accessToken.utf8), for: Self.accessTokenAccount)
        if let refreshToken = tokens.refreshToken {
            try keychain.set(Data(refreshToken.utf8), for: Self.refreshTokenAccount)
        }
        try keychain.set(
            Data(String(now().addingTimeInterval(tokens.expiresIn).timeIntervalSince1970).utf8),
            for: Self.accessExpiryAccount
        )
        try keychain.set(Data(claims.subject.utf8), for: Self.subjectAccount)
        state = .signedIn(subject: claims.subject)
    }

    func accessToken() async throws -> String {
        guard case .signedIn = state else {
            state = .signedOut
            throw AuthSessionError.authenticationRequired
        }
        if let expiry = try keychain.string(for: Self.accessExpiryAccount).flatMap(TimeInterval.init),
           expiry > now().timeIntervalSince1970 + 30,
           let token = try keychain.string(for: Self.accessTokenAccount),
           !token.isEmpty
        {
            return token
        }
        guard let refreshToken = try keychain.string(for: Self.refreshTokenAccount), !refreshToken.isEmpty else {
            state = .signedOut
            throw AuthSessionError.authenticationRequired
        }
        do {
            let refreshed = try await exchanger.refresh(
                refreshToken: refreshToken,
                configuration: configuration
            )
            guard !refreshed.accessToken.isEmpty, refreshed.expiresIn > 0 else {
                throw AuthSessionError.tokenExchangeRejected
            }
            try keychain.set(Data(refreshed.accessToken.utf8), for: Self.accessTokenAccount)
            if let rotated = refreshed.refreshToken {
                try keychain.set(Data(rotated.utf8), for: Self.refreshTokenAccount)
            }
            try keychain.set(
                Data(String(now().addingTimeInterval(refreshed.expiresIn).timeIntervalSince1970).utf8),
                for: Self.accessExpiryAccount
            )
            return refreshed.accessToken
        } catch {
            logout()
            throw AuthSessionError.authenticationRequired
        }
    }

    func logout() {
        for account in [
            Self.accessTokenAccount,
            Self.refreshTokenAccount,
            Self.accessExpiryAccount,
            Self.subjectAccount,
        ] {
            try? keychain.remove(account)
        }
        state = .signedOut
    }

    private func authenticate(_ url: URL) async throws -> URL {
        let callbackURL: URL = try await withCheckedThrowingContinuation { continuation in
            let session = ASWebAuthenticationSession(
                url: url,
                callback: .customScheme(configuration.callbackScheme)
            ) { callbackURL, error in
                if let callbackURL {
                    continuation.resume(returning: callbackURL)
                } else if let authenticationError = error as? ASWebAuthenticationSessionError,
                          authenticationError.code == .canceledLogin
                {
                    continuation.resume(throwing: CancellationError())
                } else {
                    continuation.resume(throwing: error ?? AuthSessionError.invalidCallback)
                }
            }
            session.prefersEphemeralWebBrowserSession = true
            session.presentationContextProvider = AuthenticationAnchorProvider.shared
            webAuthenticationSession = session
            guard session.start() else {
                continuation.resume(throwing: AuthSessionError.invalidConfiguration)
                return
            }
        }
        webAuthenticationSession = nil
        return callbackURL
    }

    private static func randomValue() -> String {
        PKCEPair.generate().verifier
    }

    private static func constantTimeEqual(_ left: String, _ right: String) -> Bool {
        let lhs = Array(left.utf8)
        let rhs = Array(right.utf8)
        var difference = lhs.count ^ rhs.count
        for index in 0 ..< max(lhs.count, rhs.count) {
            difference |= Int(lhs.indices.contains(index) ? lhs[index] : 0)
                ^ Int(rhs.indices.contains(index) ? rhs[index] : 0)
        }
        return difference == 0
    }
}

@MainActor
private final class AuthenticationAnchorProvider: NSObject, ASWebAuthenticationPresentationContextProviding {
    static let shared = AuthenticationAnchorProvider()

    func presentationAnchor(for _: ASWebAuthenticationSession) -> ASPresentationAnchor {
        guard let scene = UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }).first else {
            preconditionFailure("A window scene is required to present sign-in")
        }
        return scene.windows.first(where: \.isKeyWindow) ?? ASPresentationAnchor(windowScene: scene)
    }
}

struct URLSessionTokenExchanger: TokenExchanging {
    private let session: URLSession

    init(session: URLSession = .shared) {
        self.session = session
    }

    func exchange(
        code: String,
        verifier: String,
        configuration: OIDCConfiguration
    ) async throws -> OIDCTokenResponse {
        try await tokenRequest(
            parameters: [
                URLQueryItem(name: "grant_type", value: "authorization_code"),
                URLQueryItem(name: "client_id", value: configuration.clientID),
                URLQueryItem(name: "redirect_uri", value: configuration.callbackURL),
                URLQueryItem(name: "code", value: code),
                URLQueryItem(name: "code_verifier", value: verifier),
            ],
            configuration: configuration
        )
    }

    func refresh(
        refreshToken: String,
        configuration: OIDCConfiguration
    ) async throws -> OIDCTokenResponse {
        try await tokenRequest(
            parameters: [
                URLQueryItem(name: "grant_type", value: "refresh_token"),
                URLQueryItem(name: "client_id", value: configuration.clientID),
                URLQueryItem(name: "refresh_token", value: refreshToken),
            ],
            configuration: configuration
        )
    }

    private func tokenRequest(
        parameters: [URLQueryItem],
        configuration: OIDCConfiguration
    ) async throws -> OIDCTokenResponse {
        var request = URLRequest(url: configuration.tokenEndpoint)
        request.httpMethod = "POST"
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        var form = URLComponents()
        form.queryItems = parameters
        request.httpBody = form.percentEncodedQuery?.data(using: .utf8)
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, (200 ..< 300).contains(http.statusCode) else {
            throw AuthSessionError.tokenExchangeRejected
        }
        let payload = try JSONDecoder().decode(TokenPayload.self, from: data)
        guard payload.tokenType?.lowercased() == "bearer" || payload.tokenType == nil else {
            throw AuthSessionError.tokenExchangeRejected
        }
        return OIDCTokenResponse(
            accessToken: payload.accessToken,
            refreshToken: payload.refreshToken,
            idToken: payload.idToken,
            expiresIn: payload.expiresIn
        )
    }
}

private struct TokenPayload: Decodable {
    let accessToken: String
    let refreshToken: String?
    let idToken: String?
    let expiresIn: TimeInterval
    let tokenType: String?

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case refreshToken = "refresh_token"
        case idToken = "id_token"
        case expiresIn = "expires_in"
        case tokenType = "token_type"
    }
}

private extension URLComponents {
    func queryValue(_ name: String) -> String? {
        queryItems?.first(where: { $0.name == name })?.value
    }
}

private extension KeychainStore {
    func string(for account: String) throws -> String? {
        guard let data = try data(for: account) else { return nil }
        guard let value = String(data: data, encoding: .utf8) else {
            throw KeychainStoreError.invalidResult
        }
        return value
    }
}
