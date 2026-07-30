import SwiftUI

struct SettingsView: View {
    let subject: String
    let devices: AsyncViewState<[DeviceSummary]>
    let hosts: AsyncViewState<[HostSummary]>
    let pairing: DevicePairingCoordinator
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
                }

                Section {
                    Toggle("Keep observations on this device", isOn: $localOnly)
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
