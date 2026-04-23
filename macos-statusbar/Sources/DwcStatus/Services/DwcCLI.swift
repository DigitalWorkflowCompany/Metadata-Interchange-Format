import Foundation

/// Thin subprocess wrapper over the ``dwc`` CLI. Reads the binary path
/// from ``Config`` at call time so a config change takes effect without
/// restarting the app. Every call is synchronous; callers should wrap
/// in ``Task`` to keep the run loop responsive.
enum DwcCLI {
    enum Error: Swift.Error, Equatable {
        case binaryNotConfigured
        case binaryNotExecutable(String)
        case nonZeroExit(code: Int32, stderr: String)
        case decodeFailed(String)
    }

    /// Run ``dwc doctor --quick --json`` and return the parsed report.
    /// Working directory defaults to the watch root so check 10 can find
    /// ``.watch-state.json`` and check 11 the sidecars.
    static func runDoctor(binary: String?,
                          workingDirectory: String? = nil) throws -> DoctorReport {
        guard let binary else { throw Error.binaryNotConfigured }
        guard FileManager.default.isExecutableFile(atPath: binary) else {
            throw Error.binaryNotExecutable(binary)
        }

        let task = Process()
        task.executableURL = URL(fileURLWithPath: binary)
        task.arguments     = ["doctor", "--quick", "--json"]
        if let workingDirectory {
            task.currentDirectoryURL = URL(fileURLWithPath: workingDirectory)
        }

        let stdout = Pipe()
        let stderr = Pipe()
        task.standardOutput = stdout
        task.standardError  = stderr

        try task.run()
        task.waitUntilExit()

        let outData = stdout.fileHandleForReading.readDataToEndOfFile()
        let errData = stderr.fileHandleForReading.readDataToEndOfFile()

        // Doctor exits 0 (all pass/warn) or 1 (any fail). Both emit a JSON
        // body on stdout — decode from stdout regardless of exit status.
        if task.terminationStatus != 0 && task.terminationStatus != 1 {
            let err = String(data: errData, encoding: .utf8) ?? ""
            throw Error.nonZeroExit(code: task.terminationStatus, stderr: err)
        }

        do {
            return try DoctorReport.decode(from: outData)
        } catch {
            throw Error.decodeFailed(error.localizedDescription)
        }
    }
}
