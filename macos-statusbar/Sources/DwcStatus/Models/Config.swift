import Foundation

/// ``~/Library/Application Support/DwcStatus/config.json`` — the app's
/// persisted preferences. Per plan §3.6, first launch opens a folder
/// chooser for ``watchRoot`` and auto-detects ``dwcBinary`` via
/// ``which dwc`` unless already set.
struct Config: Codable, Equatable {
    var watchRoot:             String?
    var dwcBinary:             String?
    var pollDoctorSeconds:     Int
    var pollWatchStateSeconds: Int

    static let defaultPollDoctor     = 60
    static let defaultPollWatchState = 5

    static let `default` = Config(
        watchRoot:             nil,
        dwcBinary:             nil,
        pollDoctorSeconds:     defaultPollDoctor,
        pollWatchStateSeconds: defaultPollWatchState
    )

    static var fileURL: URL {
        let appSupport = FileManager.default.urls(
            for: .applicationSupportDirectory, in: .userDomainMask).first!
        return appSupport
            .appendingPathComponent("DwcStatus", isDirectory: true)
            .appendingPathComponent("config.json")
    }

    static func load() -> Config {
        guard let data = try? Data(contentsOf: fileURL),
              let cfg  = try? JSONDecoder().decode(Config.self, from: data)
        else { return .default }
        return cfg
    }

    func save() throws {
        let url = Self.fileURL
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(self).write(to: url)
    }
}

extension Config {
    /// Best-effort discovery of the ``dwc`` CLI on PATH. Returns ``nil``
    /// if the binary can't be located — the UI surfaces that as the
    /// grey icon state per plan §3.4.
    static func discoverDwcBinary() -> String? {
        let candidates = [
            "/opt/homebrew/bin/dwc",      // Apple Silicon Homebrew
            "/usr/local/bin/dwc",         // Intel Homebrew / stock
            "/opt/local/bin/dwc",         // MacPorts
        ]
        for path in candidates where FileManager.default.isExecutableFile(atPath: path) {
            return path
        }
        // Fall through to PATH lookup via /usr/bin/env
        let task = Process()
        task.launchPath = "/usr/bin/env"
        task.arguments  = ["which", "dwc"]
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError  = Pipe()
        try? task.run()
        task.waitUntilExit()
        if task.terminationStatus == 0 {
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            let out  = String(data: data, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if let out, !out.isEmpty, FileManager.default.isExecutableFile(atPath: out) {
                return out
            }
        }
        return nil
    }
}
