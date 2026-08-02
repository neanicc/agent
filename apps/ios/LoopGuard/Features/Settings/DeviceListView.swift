import SwiftUI

struct DeviceListView: View {
    let state: AsyncViewState<[DeviceSummary]>
    let pairing: DevicePairingCoordinator
    let refresh: () -> Void

    var body: some View {
        AsyncStateView(state: state, emptyTitle: "No paired devices", retry: refresh) { devices in
            List {
                Section {
                    ForEach(devices) { device in
                        VStack(alignment: .leading, spacing: 6) {
                            ResponsiveStatusHeading(
                                title: device.name,
                                label: device.revokedAt == nil ? "Active" : "Revoked",
                                state: device.revokedAt == nil ? "ready" : "revoked",
                                titleFont: .headline
                            )
                            Text("\(device.algorithm) · \(device.keyID)")
                                .font(LoopGuardTypography.telemetry)
                                .foregroundStyle(SemanticColor.secondaryText)
                                .textSelection(.enabled)
                            Text("Paired \(device.createdAt, style: .relative)")
                                .font(.footnote)
                                .foregroundStyle(SemanticColor.secondaryText)
                        }
                        .padding(.vertical, 4)
                        .accessibilityElement(children: .combine)
                    }
                } footer: {
                    Text("LoopGuard stores private signing keys in the Secure Enclave when available. Keys never leave this device.")
                }

                Section {
                    NavigationLink {
                        DevicePairingView(coordinator: pairing)
                    } label: {
                        Label("Pair this device", systemImage: "lock.shield")
                    }
                }
            }
            .refreshable { refresh() }
        }
        .navigationTitle("Paired devices")
        .accessibilityIdentifier("devices-screen")
    }
}
