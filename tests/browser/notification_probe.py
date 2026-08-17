"""Drive a real browser at a real address and report what it managed to do.

Run as its own process, under a virtual display, because the thing being
measured — whether a browser will grant notification permission and show a
notification — is precisely what a headless browser refuses to do. Prints one
line the test asserts on.
"""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright


def main(url: str) -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=False, args=["--ignore-certificate-errors"]
        )
        try:
            context = browser.new_context(
                viewport={"width": 390, "height": 844}, is_mobile=True,
                has_touch=True, ignore_https_errors=True,
            )
            context.grant_permissions(["notifications"], origin=url)
            page = context.new_page()
            page.goto(url + "/", wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

            secure = page.evaluate("() => window.isSecureContext")
            registration = page.evaluate("async () => !!(await Notifications.registerWorker())")
            page.wait_for_timeout(500)

            shown = page.evaluate("""async () => {
              await Notifications.announce({
                id: 1, type: 'dose',
                title: 'Recordatorio de medicamento',
                body: 'En 5 minutos: 1 capsula',
              });
              const registration = await navigator.serviceWorker.getRegistration('/');
              if (!registration) return 0;
              return (await registration.getNotifications()).length;
            }""")
            print(f"secure={secure} worker={registration} shown={shown}")
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
