import SwiftUI

struct SignInView: View {
    @Bindable var authSession: AuthSession
    let onSignedIn: @MainActor () async -> Void

    var body: some View {
        GeometryReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    Spacer(minLength: max(24, proxy.size.height * 0.12))
                    Image(systemName: "shield.checkered")
                        .font(.system(size: 38, weight: .medium))
                        .foregroundStyle(SemanticColor.accent)
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 12) {
                        Text("LoopGuard")
                            .font(.largeTitle.weight(.bold))
                        Text("See what your agents are doing. Intervene safely.")
                            .font(.title2.weight(.semibold))
                        Text("Run events, verification proof, and explicit approvals—kept in one quiet operational view.")
                            .font(.body)
                            .foregroundStyle(SemanticColor.secondaryText)
                    }
                    if case .failed(let message) = authSession.state {
                        Label(message, systemImage: "exclamationmark.triangle")
                            .font(.callout)
                            .foregroundStyle(SemanticColor.danger)
                            .accessibilityIdentifier("sign-in-error")
                    }
                    Button {
                        Task {
                            await authSession.signIn()
                            if case .signedIn = authSession.state { await onSignedIn() }
                        }
                    } label: {
                        if authSession.state == .signingIn {
                            ProgressView().frame(maxWidth: .infinity)
                        } else {
                            Text("Continue with your organization").frame(maxWidth: .infinity)
                        }
                    }
                    .buttonStyle(LoopGuardPrimaryButtonStyle())
                    .disabled(authSession.state == .signingIn)
                    .accessibilityIdentifier("sign-in-button")
                    Text("Authentication opens your organization’s identity provider. LoopGuard never receives your password.")
                        .font(.footnote)
                        .foregroundStyle(SemanticColor.secondaryText)
                }
                .frame(maxWidth: 520, minHeight: proxy.size.height, alignment: .topLeading)
                .padding(24)
                .frame(maxWidth: .infinity)
            }
            .background(SemanticColor.canvas)
        }
    }
}
