import SwiftUI
import AppKit

/// The dropdown menu that appears when the user clicks the status icon.
/// Read-only — no Start/Stop/Re-sign buttons; those are CLI operations
/// (plan §3.2). Layout mirrors plan §3.4 verbatim.
struct MenuContent: View {
    @ObservedObject var state: AppState

    var body: some View {
        Group {
            headerSection
            Divider()
            todaySection
            if state.doctorReport != nil {
                Divider()
                healthSection
            }
            Divider()
            foldersSection
            Divider()
            settingsSection
            Divider()
            Button("Quit DwcStatus") { NSApp.terminate(nil) }
                .keyboardShortcut("q")
        }
    }

    // ── Sections ────────────────────────────────────────────────────────

    private var headerSection: some View {
        Group {
            Text("DWC Sidecar — \(runningLabel)")
                .font(.headline)
            if let recent = state.watchState?.recent(limit: 1).first {
                Text("Last sidecar: \(recent.clipName) (\(relativeTime(from: recent.signedAt)) ago)")
                    .foregroundColor(.secondary)
            } else if state.config.watchRoot == nil {
                Text("No watch folder configured")
                    .foregroundColor(.secondary)
            }
        }
    }

    private var todaySection: some View {
        Group {
            Text("Today").font(.subheadline).foregroundColor(.secondary)
            if let watch = state.watchState {
                Text("Signed  \(watch.emitted.count - watch.quarantinedCount)")
                Text("Quarantined  \(state.quarantineCount)")
                if !watch.recent().isEmpty {
                    Text("Recent signatures (last \(min(5, watch.recent().count)))")
                        .font(.subheadline).foregroundColor(.secondary)
                    ForEach(watch.recent(limit: 5)) { emission in
                        Button(emissionLabel(emission)) {
                            revealInFinder(path: emission.omcPath)
                        }
                    }
                }
            } else {
                Text("No recent activity")
                    .foregroundColor(.secondary)
            }
        }
    }

    private var healthSection: some View {
        Group {
            let report = state.doctorReport!
            let counts = report.counts
            Text("Health (dwc doctor)")
                .font(.subheadline).foregroundColor(.secondary)
            Text("\(counts.pass) checks passed")
            if counts.warn > 0 {
                Text("\(counts.warn) warning\(counts.warn == 1 ? "" : "s") — "
                     + (firstDetail(for: .warn) ?? ""))
            }
            if counts.fail > 0 {
                Text("\(counts.fail) failure\(counts.fail == 1 ? "" : "s") — "
                     + (firstDetail(for: .fail) ?? ""))
            }
        }
    }

    private var foldersSection: some View {
        Group {
            if let root = state.config.watchRoot {
                Button("Open watch folder…") { open(path: root) }
                let qPath = (root as NSString).appendingPathComponent("quarantine")
                Button("Open quarantine…")    { open(path: qPath) }
            } else {
                Button("Choose watch folder…") { chooseWatchFolder() }
            }
        }
    }

    private var settingsSection: some View {
        Group {
            if let bin = state.config.dwcBinary {
                Text("dwc CLI: \(bin)").foregroundColor(.secondary)
            } else {
                Text("dwc CLI not found on PATH")
                    .foregroundColor(.red)
            }
            if let err = state.lastError {
                Text(err).foregroundColor(.red)
            }
        }
    }

    // ── Actions ─────────────────────────────────────────────────────────

    private var runningLabel: String {
        switch state.overallStatus {
        case .green: return "Running"
        case .amber: return "Warning"
        case .red:   return "Stopped / Failing"
        case .grey:  return "CLI unavailable"
        }
    }

    private func emissionLabel(_ e: WatchState.Emission) -> String {
        let marker = e.status == "quarantined" ? "⚠︎ " : ""
        return "\(marker)\(e.clipName)  \(relativeTime(from: e.signedAt)) ago"
    }

    private func firstDetail(for status: DoctorReport.Status) -> String? {
        state.doctorReport?.checks.first { $0.status == status }
            .map { "\($0.title): \($0.detail)" }
    }

    private func open(path: String) {
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }

    private func revealInFinder(path: String) {
        NSWorkspace.shared.activateFileViewerSelecting(
            [URL(fileURLWithPath: path)])
    }

    private func chooseWatchFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories     = true
        panel.canChooseFiles           = false
        panel.allowsMultipleSelection  = false
        panel.title                    = "Choose the DWC watch folder"
        if panel.runModal() == .OK, let url = panel.url {
            state.config.watchRoot = url.path
            try? state.config.save()
        }
    }

    /// ISO-8601 UTC → "12s", "3m", "2h" style compact label. Wall-clock
    /// math is fine here — we're rendering a menu that'll be re-rendered
    /// on every poll tick.
    private func relativeTime(from iso: String) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        guard let date = formatter.date(from: iso) else { return iso }
        let delta = Date().timeIntervalSince(date)
        if delta < 60        { return "\(Int(delta))s" }
        if delta < 3600      { return "\(Int(delta / 60))m" }
        if delta < 86_400    { return "\(Int(delta / 3600))h" }
        return "\(Int(delta / 86_400))d"
    }
}
