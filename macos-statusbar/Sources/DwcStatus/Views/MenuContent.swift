import SwiftUI
import AppKit

/// Menu panel content for DwcStatus, displayed inside a
/// ``MenuBarExtra`` in ``.window`` style — so we own layout end-to-end
/// and follow macOS HIG conventions for menu-bar dropdowns:
///
/// - Fixed panel width (280pt) so state changes don't reflow geometry.
/// - Section headers in caption2, secondary, uppercase, with letter-spacing.
/// - Item rows: leading SF Symbol, body text, trailing affordance/count.
/// - Counts right-aligned with ``monospacedDigit()`` so columns line up.
/// - Buttons styled ``.borderless`` with full-row hit area so they feel
///   like menu rows, not raised buttons.
/// - Dividers — never blank lines — separate semantic groups.
/// - Read-only by intent (plan §3.2): no Start/Stop/Re-sign — CLI only.
struct MenuContent: View {
    @ObservedObject var state: AppState

    /// Side-effect adapters — protocols so tests can inject mocks.
    /// Defaults talk to the real system pasteboard and AppleScript engine.
    var pasteboard: PasteboardWriting   = SystemPasteboard()
    var scriptRunner: AppleScriptRunning = SystemAppleScriptRunner()

    private static let panelWidth: CGFloat = 280
    private static let hPadding:   CGFloat = 14
    private static let vPadding:   CGFloat = 10

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            switch state.onboardingState {
            case .cliMissing:          cliMissingPanel
            case .signersUnconfigured: signersUnconfiguredPanel
            case .ready:               readyPanel
            }
        }
        .frame(width: Self.panelWidth, alignment: .leading)
        .onAppear { state.recheckOnboarding() }
    }

    // MARK: - Ready (full panel)

    private var readyPanel: some View {
        VStack(alignment: .leading, spacing: 0) {
            headerSection
                .padding(.horizontal, Self.hPadding)
                .padding(.top, 14)
                .padding(.bottom, 12)

            Divider()

            todaySection
                .padding(.horizontal, Self.hPadding)
                .padding(.vertical, Self.vPadding)

            if let watch = state.watchState, !watch.recent().isEmpty {
                Divider()
                recentSection(watch: watch)
                    .padding(.horizontal, Self.hPadding)
                    .padding(.vertical, Self.vPadding)
            }

            if let report = state.doctorReport {
                Divider()
                healthSection(report: report)
                    .padding(.horizontal, Self.hPadding)
                    .padding(.vertical, Self.vPadding)
            }

            Divider()

            actionsSection
                .padding(.horizontal, Self.hPadding)
                .padding(.vertical, Self.vPadding)

            Divider()

            cliSection
                .padding(.horizontal, Self.hPadding)
                .padding(.vertical, Self.vPadding)

            Divider()

            quitSection
                .padding(.horizontal, Self.hPadding)
                .padding(.top, 8)
                .padding(.bottom, 10)
        }
    }

    // MARK: - Onboarding panels (plan §6.3)

    private var cliMissingPanel: some View {
        VStack(alignment: .leading, spacing: 0) {
            onboardingHeader(
                title: "Install the DWC CLI",
                subtitle: "DWC Status needs the dwc command-line tool",
                symbol: "shippingbox",
                tint: .accentColor
            )

            Divider()

            VStack(alignment: .leading, spacing: 8) {
                Text("Recommended (Homebrew):")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                commandRow(OnboardingActions.brewInstallCommand)

                Text("Without Homebrew (pipx):")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.top, 4)
                commandRow(OnboardingActions.pipxFallbackCommand)
            }
            .padding(.horizontal, Self.hPadding)
            .padding(.vertical, Self.vPadding)

            Divider()

            recheckAndQuitSection
        }
    }

    private var signersUnconfiguredPanel: some View {
        VStack(alignment: .leading, spacing: 0) {
            onboardingHeader(
                title: "Configure signing",
                subtitle: "Run dwc init to generate a signing key",
                symbol: "key",
                tint: .accentColor
            )

            Divider()

            VStack(alignment: .leading, spacing: 8) {
                Text("DWC Status doesn't perform setup itself — `dwc init` walks you through it (plan §5).")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                menuRow(icon: "terminal", label: "Open Terminal at dwc init") {
                    runDwcInitInTerminal()
                }
            }
            .padding(.horizontal, Self.hPadding)
            .padding(.vertical, Self.vPadding)

            Divider()

            recheckAndQuitSection
        }
    }

    private func onboardingHeader(
        title: String, subtitle: String, symbol: String, tint: Color
    ) -> some View {
        HStack(alignment: .center, spacing: 12) {
            Image(systemName: symbol)
                .symbolRenderingMode(.palette)
                .foregroundStyle(tint)
                .font(.system(size: 22, weight: .regular))
                .frame(width: 26, height: 26)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.headline)
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, Self.hPadding)
        .padding(.top, 14)
        .padding(.bottom, 12)
    }

    /// Monospaced command row with a copy-to-clipboard button.
    private func commandRow(_ command: String) -> some View {
        HStack(spacing: 8) {
            Text(command)
                .font(.system(.caption, design: .monospaced))
                .lineLimit(1)
                .truncationMode(.middle)
                .help(command)
            Spacer(minLength: 4)
            Button {
                pasteboard.setString(command)
            } label: {
                Image(systemName: "doc.on.doc")
                    .font(.caption)
            }
            .buttonStyle(.borderless)
            .help("Copy to clipboard")
        }
        .padding(8)
        .background(Color.secondary.opacity(0.08))
        .cornerRadius(6)
    }

    private func runDwcInitInTerminal() {
        let source = OnboardingActions.dwcInitTerminalScript(
            watchRoot: state.config.watchRoot
        )
        scriptRunner.run(source: source)
    }

    private var recheckAndQuitSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            menuRow(icon: "arrow.clockwise",
                    label: "I've finished setup, recheck") {
                state.recheckOnboarding()
            }
            .padding(.horizontal, Self.hPadding)
            .padding(.vertical, Self.vPadding)

            Divider()

            quitSection
                .padding(.horizontal, Self.hPadding)
                .padding(.top, 8)
                .padding(.bottom, 10)
        }
    }

    // MARK: - Header

    private var headerSection: some View {
        HStack(alignment: .center, spacing: 12) {
            Image(systemName: state.overallStatus.headerSymbol)
                .symbolRenderingMode(.palette)
                .foregroundStyle(state.overallStatus.color)
                .font(.system(size: 22, weight: .regular))
                .frame(width: 26, height: 26)
            VStack(alignment: .leading, spacing: 2) {
                Text("DWC Sidecar Status")
                    .font(.headline)
                Text(headerSubtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Spacer(minLength: 0)
        }
    }

    private var headerSubtitle: String {
        switch state.overallStatus {
        case .green: return state.config.watchRoot == nil ? "Idle · no watch folder" : "Running"
        case .amber: return "Warning"
        case .red:   return "Failing"
        case .grey:  return "CLI unavailable"
        }
    }

    // MARK: - Today

    private var todaySection: some View {
        VStack(alignment: .leading, spacing: 4) {
            sectionHeader("Today")
            counterRow(icon: "checkmark.seal.fill", color: .green,
                       label: "Signed",      count: signedCount)
            counterRow(icon: "exclamationmark.triangle.fill", color: .orange,
                       label: "Quarantined", count: state.quarantineCount)
        }
    }

    private var signedCount: Int {
        guard let watch = state.watchState else { return 0 }
        return watch.emitted.count - watch.quarantinedCount
    }

    // MARK: - Recent signatures

    private func recentSection(watch: WatchState) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            sectionHeader("Recent signatures")
            VStack(alignment: .leading, spacing: 2) {
                ForEach(watch.recent(limit: 5)) { emission in
                    signatureRow(emission)
                }
            }
        }
    }

    private func signatureRow(_ emission: WatchState.Emission) -> some View {
        Button {
            revealInFinder(path: emission.omcPath)
        } label: {
            HStack(spacing: 6) {
                if emission.status == "quarantined" {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                        .font(.caption2)
                } else {
                    Image(systemName: "checkmark.seal.fill")
                        .foregroundStyle(.green)
                        .font(.caption2)
                }
                Text(emission.clipName)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer(minLength: 4)
                Text(relativeTime(from: emission.signedAt))
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
                Image(systemName: "arrow.up.right.square")
                    .foregroundStyle(.tertiary)
                    .font(.caption2)
            }
            .font(.callout)
            .padding(.vertical, 2)
            .contentShape(Rectangle())
        }
        .buttonStyle(.borderless)
        .help("Reveal in Finder")
    }

    // MARK: - Health

    private func healthSection(report: DoctorReport) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            sectionHeader("Health")
            healthLine(icon: "checkmark.circle.fill", color: .green,
                       text: "\(report.counts.pass) checks passed")
            if report.counts.warn > 0 {
                healthLine(
                    icon: "exclamationmark.triangle.fill",
                    color: .orange,
                    text: "\(report.counts.warn) warning\(report.counts.warn == 1 ? "" : "s")",
                    detail: firstDetail(for: .warn)
                )
            }
            if report.counts.fail > 0 {
                healthLine(
                    icon: "xmark.octagon.fill",
                    color: .red,
                    text: "\(report.counts.fail) failure\(report.counts.fail == 1 ? "" : "s")",
                    detail: firstDetail(for: .fail)
                )
            }
        }
    }

    private func healthLine(icon: String, color: Color, text: String, detail: String? = nil) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            HStack(spacing: 6) {
                Image(systemName: icon)
                    .foregroundStyle(color)
                Text(text)
            }
            .font(.callout)
            if let detail {
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.leading, 22)
                    .lineLimit(2)
            }
        }
    }

    private func firstDetail(for status: DoctorReport.Status) -> String? {
        state.doctorReport?.checks.first { $0.status == status }
            .map { "\($0.title): \($0.detail)" }
    }

    // MARK: - Actions

    private var actionsSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let root = state.config.watchRoot {
                menuRow(icon: "folder",
                        label: "Open watch folder…") { open(path: root) }
                let qPath = (root as NSString).appendingPathComponent("quarantine")
                menuRow(icon: "exclamationmark.triangle",
                        label: "Open quarantine…") { open(path: qPath) }
            } else {
                menuRow(icon: "folder.badge.plus",
                        label: "Choose watch folder…") { chooseWatchFolder() }
            }
        }
    }

    // MARK: - DWC CLI

    private var cliSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            sectionHeader("DWC CLI")
            if let bin = state.config.dwcBinary {
                Text(bin)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .help(bin)
            } else {
                HStack(spacing: 6) {
                    Image(systemName: "exclamationmark.octagon.fill")
                        .foregroundStyle(.red)
                    Text("Not found on PATH")
                        .foregroundStyle(.red)
                }
                .font(.caption)
            }
            menuRow(icon: "doc.text.magnifyingglass",
                    label: "Choose DWC binary…") { chooseDwcBinary() }
            if let err = state.lastError {
                Text(err)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .lineLimit(2)
                    .padding(.top, 2)
            }
        }
    }

    // MARK: - Quit

    private var quitSection: some View {
        Button {
            NSApp.terminate(nil)
        } label: {
            HStack {
                Text("Quit DWC Status")
                Spacer()
                Text("⌘Q")
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
            }
            .font(.callout)
            .contentShape(Rectangle())
        }
        .buttonStyle(.borderless)
        .keyboardShortcut("q")
    }

    // MARK: - Reusable row builders

    private func sectionHeader(_ title: String) -> some View {
        Text(title)
            .font(.caption2.weight(.semibold))
            .foregroundStyle(.secondary)
            .tracking(0.6)
            .textCase(.uppercase)
            .padding(.bottom, 2)
    }

    private func counterRow(icon: String, color: Color, label: String, count: Int) -> some View {
        HStack(spacing: 8) {
            Image(systemName: icon)
                .foregroundStyle(color)
                .frame(width: 16, alignment: .center)
            Text(label)
            Spacer(minLength: 8)
            Text("\(count)")
                .monospacedDigit()
                .foregroundStyle(count == 0 ? .secondary : .primary)
        }
        .font(.callout)
    }

    private func menuRow(icon: String, label: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: icon)
                    .foregroundStyle(.secondary)
                    .frame(width: 16, alignment: .center)
                Text(label)
                Spacer(minLength: 0)
            }
            .font(.callout)
            .padding(.vertical, 2)
            .contentShape(Rectangle())
        }
        .buttonStyle(.borderless)
    }

    // MARK: - Actions

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

    private func chooseDwcBinary() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories     = false
        panel.canChooseFiles           = true
        panel.allowsMultipleSelection  = false
        panel.title                    = "Locate the dwc CLI binary"
        panel.message                  = "Pick the dwc executable — usually under /opt/homebrew/bin, /usr/local/bin, or a Python framework's bin/."
        panel.showsHiddenFiles         = true
        panel.treatsFilePackagesAsDirectories = true
        if panel.runModal() == .OK, let url = panel.url,
           FileManager.default.isExecutableFile(atPath: url.path) {
            state.config.dwcBinary = url.path
            try? state.config.save()
        }
    }

    /// ISO-8601 UTC → "12s", "3m", "2h" style compact label.
    private func relativeTime(from iso: String) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        guard let date = formatter.date(from: iso) else { return iso }
        let delta = Date().timeIntervalSince(date)
        if delta < 60     { return "\(Int(delta))s" }
        if delta < 3600   { return "\(Int(delta / 60))m" }
        if delta < 86_400 { return "\(Int(delta / 3600))h" }
        return "\(Int(delta / 86_400))d"
    }
}

private extension OverallStatus {
    /// SF Symbol used for the prominent header dot at the top of the
    /// menu panel (different from the menu-bar icon, which always uses
    /// ``circle.fill`` so the silhouette stays stable).
    var headerSymbol: String {
        switch self {
        case .green: return "checkmark.circle.fill"
        case .amber: return "exclamationmark.triangle.fill"
        case .red:   return "xmark.octagon.fill"
        case .grey:  return "questionmark.circle.fill"
        }
    }
}
