import SwiftUI

@main
struct LoopGuardApp: App {
    @State private var model = AppEnvironment.makeModel()

    var body: some Scene {
        WindowGroup {
            Group {
                if model.fixtureMode {
                    MainTabView(model: model)
                        .task { await model.loadDashboard() }
                } else {
                    switch model.auth.state {
                    case .signedOut, .signingIn, .failed:
                        SignInView(authSession: model.auth) {
                            await model.loadDashboard()
                        }
                    case .signedIn:
                        MainTabView(model: model)
                            .task { await model.loadDashboard() }
                    }
                }
            }
        }
    }
}

@MainActor
private enum AppEnvironment {
    static func makeModel() -> AppModel {
        let environment = ProcessInfo.processInfo.environment
        let configured = environment["LOOPGUARD_API_URL"]
        let fixtureMode = environment["LOOPGUARD_UI_TEST_MODE"] == "1"
        let fixtureScenario = environment["LOOPGUARD_UI_TEST_STATE"]
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
            deviceKeyStore: keyStore,
            fixtureMode: fixtureMode,
            fixtureScenario: fixtureScenario
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
