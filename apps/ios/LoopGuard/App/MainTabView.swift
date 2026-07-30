import SwiftUI

struct MainTabView: View {
    @Bindable var model: AppModel
    @State private var selection: CoreDestination = .inbox

    var body: some View {
        TabView(selection: $selection) {
            Tab("Inbox", systemImage: "tray.full", value: .inbox) {
                InboxView(
                    state: model.inbox,
                    sessions: model.sessions.value ?? [],
                    actionModel: model.pendingAction,
                    refresh: refresh
                )
            }
            .accessibilityIdentifier("inbox-tab")

            Tab("Runs", systemImage: "terminal", value: .runs) {
                RunListView(
                    state: model.sessions,
                    selection: Binding(
                        get: { model.routedSessionID },
                        set: { model.setRoutedSession($0) }
                    ),
                    refresh: refresh
                )
            }
            .accessibilityIdentifier("runs-tab")

            Tab("Changes", systemImage: "arrow.triangle.branch", value: .changes) {
                ChangeListView(state: model.changes, refresh: refresh)
            }
            .accessibilityIdentifier("changes-tab")

            Tab("Settings", systemImage: "gearshape", value: .settings) {
                SettingsView(
                    subject: model.subject,
                    devices: model.devices,
                    hosts: model.hosts,
                    pairing: model.pairing,
                    notifications: model.notifications,
                    refresh: refresh,
                    logout: model.logout
                )
            }
            .accessibilityIdentifier("settings-tab")
        }
        .onChange(of: model.notifications.route) { _, route in
            guard let route else { return }
            switch route {
            case .session:
                selection = .runs
            case .action:
                selection = .inbox
            }
        }
        .task(id: routeTaskID) {
            guard let route = model.notifications.route else { return }
            await model.handleNotificationRoute(route)
        }
        .task(id: model.notifications.deviceToken) {
            guard let token = model.notifications.deviceToken else { return }
            await model.registerPushToken(token)
        }
    }

    private var routeTaskID: String? {
        switch model.notifications.route {
        case .action(let id): "action:\(id)"
        case .session(let id): "session:\(id)"
        case nil: nil
        }
    }

    private func refresh() {
        Task { await model.loadDashboard() }
    }
}

private enum CoreDestination: Hashable {
    case inbox
    case runs
    case changes
    case settings
}
