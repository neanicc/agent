import SwiftUI

@main
struct LoopGuardApp: App {
    @State private var model = AppEnvironment.makeModel()

    var body: some Scene {
        WindowGroup {
            Group {
                switch model.auth.state {
                case .signedOut, .signingIn, .failed:
                    SignInView(authSession: model.auth) {
                        await model.loadSessions()
                    }
                case .signedIn:
                    NavigationStack {
                        AsyncStateView(
                            state: model.sessions,
                            emptyTitle: "No runs observed",
                            retry: { Task { await model.loadSessions() } }
                        ) { sessions in
                            List(sessions) { session in
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(session.name ?? "Run \(session.id.prefix(8))")
                                        .font(.headline)
                                    Text(session.repository ?? "Repository not reported")
                                        .font(LoopGuardTypography.secondary)
                                        .foregroundStyle(SemanticColor.secondaryText)
                                }
                                .accessibilityElement(children: .combine)
                            }
                        }
                        .navigationTitle("LoopGuard")
                        .background(SemanticColor.canvas)
                    }
                    .task { await model.loadSessions() }
                }
            }
        }
    }
}

@MainActor
private enum AppEnvironment {
    static func makeModel() -> AppModel {
        let configured = ProcessInfo.processInfo.environment["LOOPGUARD_API_URL"]
        let baseURL = configured.flatMap(URL.init(string:)) ?? URL(string: "https://api.loopguard.invalid")!
        let keychain = KeychainStore(service: "dev.loopguard.ios.session")
        let auth = AuthSession(
            configuration: oidcConfiguration(),
            keychain: keychain
        )
        let api = ControlAPI(
            baseURL: baseURL,
            tokenProvider: {
                try await auth.accessToken()
            }
        )
        let keyStore = DeviceKeyStore(keychain: keychain)
        return AppModel(
            api: api,
            auth: auth,
            pairing: DevicePairingCoordinator(
                api: ControlDevicePairingAPI(api: api),
                keyStore: keyStore
            ),
            deviceKeyStore: keyStore
        )
    }

    private static func oidcConfiguration() -> OIDCConfiguration {
        let info = Bundle.main.infoDictionary ?? [:]
        func url(_ key: String, fallback: String) -> URL {
            URL(string: info[key] as? String ?? fallback) ?? URL(string: fallback)!
        }
        return OIDCConfiguration(
            issuer: url("LoopGuardOIDCIssuer", fallback: "https://identity.loopguard.invalid"),
            authorizationEndpoint: url(
                "LoopGuardOIDCAuthorizationEndpoint",
                fallback: "https://identity.loopguard.invalid/authorize"
            ),
            tokenEndpoint: url(
                "LoopGuardOIDCTokenEndpoint",
                fallback: "https://identity.loopguard.invalid/token"
            ),
            jwksEndpoint: url(
                "LoopGuardOIDCJWKSEndpoint",
                fallback: "https://identity.loopguard.invalid/.well-known/jwks.json"
            ),
            clientID: info["LoopGuardOIDCClientID"] as? String ?? "loopguard-ios",
            callbackScheme: "loopguard",
            scopes: ["openid", "profile", "email", "offline_access"]
        )
    }
}
