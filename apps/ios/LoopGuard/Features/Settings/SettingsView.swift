import SwiftUI

struct SettingsView: View {
    let subject: String
    let devices: AsyncViewState<[DeviceSummary]>
    let hosts: AsyncViewState<[HostSummary]>
    let pairing: DevicePairingCoordinator
    let notifications: NotificationManager
    let refresh: () -> Void
    let logout: () -> Void

    @AppStorage("notifications.attention") private var attentionNotifications = true
    @AppStorage("privacy.localOnly") private var localOnly = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Account") {
                    LabeledContent("Signed in as", value: subject)
                    Button("Sign out", role: .destructive, action: logout)
                }

                Section("Security") {
                    NavigationLink {
                        DeviceListView(state: devices, pairing: pairing, refresh: refresh)
                    } label: {
                        Label("Paired devices", systemImage: "lock.shield")
                    }
                }

                Section("Connections") {
                    NavigationLink {
                        HostIntegrationListView(state: hosts, refresh: refresh)
                    } label: {
                        Label("Hosts & integrations", systemImage: "point.3.connected.trianglepath.dotted")
                    }
                }

                Section("Notifications") {
                    Toggle("Only items needing attention", isOn: $attentionNotifications)
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel("Only items needing attention")
                        .accessibilityValue(attentionNotifications ? "On" : "Off")
                        .onChange(of: attentionNotifications) { _, enabled in
                            guard enabled else { return }
                            Task {
                                if !(await notifications.requestAuthorization()) {
                                    attentionNotifications = false
                                }
                            }
                        }
                    if notifications.authorizationDenied {
                        Label(
                            "Notifications are disabled in system settings.",
                            systemImage: "bell.slash"
                        )
                        .foregroundStyle(SemanticColor.secondaryText)
                    }
                    if notifications.registrationFailed {
                        Label(
                            "This device could not register for remote alerts. Try again after reconnecting.",
                            systemImage: "exclamationmark.arrow.trianglehead.2.clockwise.rotate.90"
                        )
                        .foregroundStyle(SemanticColor.secondaryText)
                    }
                }

                Section {
                    Toggle("Keep observations on this device", isOn: $localOnly)
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel("Keep observations on this device")
                        .accessibilityValue(localOnly ? "On" : "Off")
                } header: {
                    Text("Privacy")
                } footer: {
                    Text(localOnly
                         ? "Local-only mode is selected. Cloud synchronization is disabled for new observations."
                         : "Encrypted control data can synchronize across your signed-in devices.")
                }

                Section("About") {
                    LabeledContent("Version", value: appVersion)
                    Link(
                        "Privacy & security",
                        destination: URL(string: "https://loopguard.dev/privacy")!
                    )
                }
            }
            .navigationTitle("Settings")
            .accessibilityIdentifier("settings-screen")
        }
    }

    private var appVersion: String {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "0"
        return "\(version) (\(build))"
    }
}
