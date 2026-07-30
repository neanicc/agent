import SwiftUI
import UIKit

struct HostIntegrationListView: View {
    let state: AsyncViewState<[HostSummary]>
    let refresh: () -> Void
    @State private var copiedCommand: String?

    var body: some View {
        Group {
            if case .empty = state {
                List {
                    Section {
                        ContentUnavailableView("No connected hosts", systemImage: "desktopcomputer")
                    }
                    localSetupSection
                }
            } else {
                AsyncStateView(state: state, emptyTitle: "No connected hosts", retry: refresh) { hosts in
                    hostList(hosts)
                }
            }
        }
        .navigationTitle("Hosts & integrations")
        .accessibilityIdentifier("hosts-screen")
        .alert("Copied", isPresented: copiedAlert) {
            Button("Done", role: .cancel) {}
        } message: {
            Text(copiedCommand ?? "")
        }
    }

    private func hostList(_ hosts: [HostSummary]) -> some View {
            List {
                Section("Connected hosts") {
                    ForEach(hosts) { host in
                        VStack(alignment: .leading, spacing: 6) {
                            ResponsiveStatusHeading(
                                title: host.name,
                                label: host.state?.capitalized ?? "Observed",
                                state: host.state ?? "observed",
                                titleFont: .headline
                            )
                            if let repository = host.repository {
                                Text(repository)
                                    .font(.subheadline)
                                    .foregroundStyle(SemanticColor.secondaryText)
                            }
                            if let version = host.adapterVersion {
                                Text("Adapter \(version)")
                                    .font(LoopGuardTypography.telemetry)
                                    .foregroundStyle(SemanticColor.secondaryText)
                            }
                        }
                        .padding(.vertical, 4)
                        .accessibilityElement(children: .combine)
                    }
                }

                localSetupSection
            }
            .refreshable { refresh() }
    }

    private var localSetupSection: some View {
        Section {
            CommandRow(
                title: "Check this repository",
                command: "loopguard doctor",
                copy: copy
            )
            CommandRow(
                title: "Connect this repository",
                command: "loopguard host pair",
                copy: copy
            )
        } header: {
            Text("Local setup")
        } footer: {
            Text("Run commands only in the repository you intend to connect. LoopGuard never performs a remote global install.")
        }
    }

    private var copiedAlert: Binding<Bool> {
        Binding(
            get: { copiedCommand != nil },
            set: { if !$0 { copiedCommand = nil } }
        )
    }

    private func copy(_ command: String) {
        UIPasteboard.general.string = command
        copiedCommand = command
    }
}

private struct CommandRow: View {
    let title: String
    let command: String
    let copy: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.headline)
            HStack {
                Text(command)
                    .font(LoopGuardTypography.telemetry)
                    .textSelection(.enabled)
                Spacer()
                Button {
                    copy(command)
                } label: {
                    Label("Copy", systemImage: "doc.on.doc")
                }
                .buttonStyle(.borderless)
                .frame(minHeight: ControlMetrics.minimumTouchTarget)
            }
        }
        .padding(.vertical, 3)
    }
}
