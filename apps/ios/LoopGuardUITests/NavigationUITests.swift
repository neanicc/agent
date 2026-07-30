import XCTest

@MainActor
final class NavigationUITests: XCTestCase {
    func testCoreCapabilityTabsNavigateToRealData() throws {
        let app = launchApp()
        XCTAssertTrue(app.buttons["Runs"].firstMatch.waitForExistence(timeout: 5))
        for destination in ["Inbox", "Runs", "Changes", "Settings"] {
            XCTAssertTrue(app.buttons[destination].firstMatch.exists)
        }
        XCTAssertFalse(app.buttons["Repairs"].firstMatch.exists)

        app.buttons["Runs"].firstMatch.tap()
        XCTAssertTrue(app.navigationBars["Runs"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Harden authentication migration"].exists)
        app.staticTexts["Harden authentication migration"].tap()
        XCTAssertTrue(app.navigationBars["Run detail"].waitForExistence(timeout: 5))
        attachScreenshot(of: app, named: "Runs — list and selected detail")

        app.buttons["Changes"].firstMatch.tap()
        XCTAssertTrue(app.navigationBars["Changes"].waitForExistence(timeout: 5))
        app.staticTexts["Rotate access tokens without interrupting active runs"].tap()
        XCTAssertTrue(app.navigationBars["Change detail"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Verification proof"].exists)
        app.swipeUp()
        XCTAssertTrue(app.staticTexts["Observed diff"].waitForExistence(timeout: 3))
        attachScreenshot(of: app, named: "Change detail — provenance proof and diff")
    }

    func testSettingsExposeSecurityConnectionsPrivacyAndVersion() throws {
        let app = launchApp()
        XCTAssertTrue(app.buttons["Settings"].firstMatch.waitForExistence(timeout: 5))
        app.buttons["Settings"].firstMatch.tap()
        XCTAssertTrue(app.navigationBars["Settings"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Signed in as"].exists)
        XCTAssertTrue(app.staticTexts["Paired devices"].exists)
        XCTAssertTrue(app.staticTexts["Hosts & integrations"].exists)

        app.staticTexts["Paired devices"].tap()
        XCTAssertTrue(app.navigationBars["Paired devices"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Developer’s iPhone"].exists)

        app.navigationBars.buttons["Settings"].tap()
        app.swipeUp()
        XCTAssertTrue(app.switches["Keep observations on this device"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.staticTexts["Version"].exists)
        attachScreenshot(of: app, named: "Settings — privacy and version")
    }

    private func launchApp() -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["LOOPGUARD_UI_TEST_MODE"] = "1"
        app.launchArguments += [
            "-UIPreferredContentSizeCategoryName",
            "UICTContentSizeCategoryL",
        ]
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
