// swift-tools-version: 5.9
// DwcStatus — read-only menu-bar status app for dwc-sidecar (plan §3).
//
// Shipped as a SwiftPM package so CI can `swift build` + `swift test`
// without Xcode project plumbing. Scripts/make_app.sh wraps the release
// binary in a .app bundle with LSUIElement=true for distribution.
import PackageDescription

let package = Package(
    name: "DwcStatus",
    platforms: [.macOS(.v13)],  // MenuBarExtra requires macOS 13+
    products: [
        .executable(name: "DwcStatus", targets: ["DwcStatus"]),
    ],
    targets: [
        .executableTarget(
            name: "DwcStatus",
            path: "Sources/DwcStatus"
        ),
        .testTarget(
            name: "DwcStatusTests",
            dependencies: ["DwcStatus"],
            path: "Tests/DwcStatusTests",
            resources: [.copy("Fixtures")]
        ),
    ]
)
