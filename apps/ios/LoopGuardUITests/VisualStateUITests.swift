import XCTest

@MainActor
final class VisualStateUITests: XCTestCase {
    func testInboxLightReference() {
        let app = launchApp(interfaceStyle: "Light")
        XCTAssertTrue(app.navigationBars["Inbox"].waitForExistence(timeout: 5))
        attachScreenshot(of: app, named: "Inbox — light reference")
    }

    func testInboxDarkReference() {
        let app = launchApp(interfaceStyle: "Dark")
        XCTAssertTrue(app.navigationBars["Inbox"].waitForExistence(timeout: 5))
        attachScreenshot(of: app, named: "Inbox — dark reference")
    }

    func testHighContrastReducedMotionReference() {
        let app = launchApp(
            interfaceStyle: "Dark",
            additionalArguments: [
                "-UIAccessibilityDarkerSystemColorsEnabled", "YES",
                "-UIAccessibilityReduceMotionEnabled", "YES",
            ]
        )
        XCTAssertTrue(app.navigationBars["Inbox"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Regression needs review"].isHittable)
        attachScreenshot(of: app, named: "Inbox — high contrast and reduced motion")
    }

    func testLoadingEmptyErrorOfflineAndSuccessReferencesAreExplicit() {
        assertState("loading", text: "Loading")
        assertState("empty", text: "Nothing needs attention")
        assertState("error", text: "Couldn’t load this view")
        assertState("stale", text: "Showing stale data")

        let success = launchApp(interfaceStyle: "Light")
        XCTAssertTrue(success.staticTexts["Quiet completions"].waitForExistence(timeout: 5))
    }

    private func assertState(_ state: String, text: String) {
        let app = launchApp(interfaceStyle: "Light", state: state)
        XCTAssertTrue(app.staticTexts[text].waitForExistence(timeout: 5), "\(state) state should be explicit")
    }

    private func launchApp(
        interfaceStyle: String,
        state: String? = nil,
        additionalArguments: [String] = []
    ) -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["LOOPGUARD_UI_TEST_MODE"] = "1"
        if let state {
            app.launchEnvironment["LOOPGUARD_UI_TEST_STATE"] = state
        }
        app.launchArguments += [
            "-AppleInterfaceStyle", interfaceStyle,
            "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryL",
        ]
        app.launchArguments += additionalArguments
        app.launch()
        return app
    }

    private func attachScreenshot(of app: XCUIApplication, named name: String) {
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = name
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }
}
