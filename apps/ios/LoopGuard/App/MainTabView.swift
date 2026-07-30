import SwiftUI

struct MainTabView: View {
    @Bindable var model: AppModel

    var body: some View {
        TabView {
            Tab("Inbox", systemImage: "tray.full") {
                InboxView(
                    state: model.inbox,
                    sessions: model.sessions.value ?? [],
                    refresh: refresh
                )
            }
            .accessibilityIdentifier("inbox-tab")

            Tab("Runs", systemImage: "terminal") {
                RunListView(state: model.sessions, refresh: refresh)
            }
            .accessibilityIdentifier("runs-tab")

            Tab("Changes", systemImage: "arrow.triangle.branch") {
                ChangeListView(state: model.changes, refresh: refresh)
            }
            .accessibilityIdentifier("changes-tab")

            Tab("Settings", systemImage: "gearshape") {
                SettingsView(
                    subject: model.subject,
                    devices: model.devices,
                    hosts: model.hosts,
                    pairing: model.pairing,
                    refresh: refresh,
                    logout: model.logout
                )
            }
            .accessibilityIdentifier("settings-tab")
        }
    }

    private func refresh() {
        Task { await model.loadDashboard() }
    }
}
