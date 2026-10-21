// swift-tools-version: 6.0
// The swift-tools-version declares the minimum version of Swift required to build this package.

import PackageDescription

let package = Package(
    name: "TrackManiaHub",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "TrackManiaHub", targets: ["TrackManiaHub"]),
    ],
    targets: [
        .executableTarget(name: "TrackManiaHub"),
        .testTarget(
            name: "TrackManiaHubTests",
            dependencies: ["TrackManiaHub"],
        ),
    ]
)
