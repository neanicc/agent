import SwiftUI

@main
struct LoopGuardApp: App {
    @State private var model = AppEnvironment.makeModel()

    var body: some Scene {
        WindowGroup {
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

@MainActor
private enum AppEnvironment {
    static func makeModel() -> AppModel {
        let configured = ProcessInfo.processInfo.environment["LOOPGUARD_API_URL"]
        let baseURL = configured.flatMap(URL.init(string:)) ?? URL(string: "https://api.loopguard.invalid")!
        let keychain = KeychainStore(service: "dev.loopguard.ios.session")
        let api = ControlAPI(
            baseURL: baseURL,
            tokenProvider: {
                guard let data = try keychain.data(for: "access-token"),
                      let token = String(data: data, encoding: .utf8),
                      !token.isEmpty
                else {
                    throw ControlAPIError.authenticationRequired
                }
                return token
            }
        )
        return AppModel(api: api)
    }
}
