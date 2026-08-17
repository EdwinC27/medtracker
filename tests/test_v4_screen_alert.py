"""The reminder a phone can actually be given.

A phone reaches this application at `http://192.168.x.x:8000`, and a plain
http:// origin is not a secure context. Measured in a real browser, over the
real local address: `window.isSecureContext` is false there, `serviceWorker` is
absent, and notification permission cannot be granted. So the phone gets an
on-screen alert instead — and these tests are about that alert being a real
reminder rather than a toast that vanishes.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "app" / "static" / "js"


def read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_the_alert_is_shown_when_the_browser_will_not_show_one():
    """`needed()` is the whole decision, and it has to include the case that
    started this: a secure-context check, not only a permission check. A phone
    on http:// reports permission "default" for ever — asking is refused
    outright — so a permission check alone would conclude "maybe later" and
    show nothing, which is exactly what it used to do."""
    source = read("screenalert.js")
    assert "function needed()" in source
    assert "window.isSecureContext" in source
    assert "Notification.permission !== 'granted'" in source

    notifications = read("notifications.js")
    assert "ScreenAlert.needed()" in notifications
    assert "ScreenAlert.show(item)" in notifications


def test_a_reminder_does_not_remove_itself():
    """The old fallback was a nine-second toast. A dose reminder that disappears
    while the phone is in a pocket has not reminded anybody of anything."""
    source = read("screenalert.js")
    body = source[source.index("function show(item)"):source.index("function clear()")]
    assert "setTimeout" not in body
    assert "card.remove()" in body          # only from the dismiss button


def test_the_same_reminder_is_not_shown_twice():
    source = read("screenalert.js")
    assert "document.getElementById(id)" in source


def test_it_makes_a_noise_and_the_noise_is_not_a_file():
    """Synthesised: nothing to ship, no media element for an autoplay policy to
    block, and it works on a machine with no internet."""
    source = read("screenalert.js")
    assert "createOscillator" in source
    assert "new Audio(" not in source and ".mp3" not in source and ".wav" not in source
    assert "navigator.vibrate" in source


def test_the_sound_is_unlocked_by_a_real_gesture():
    """Browsers stay silent until the user has interacted with the page. Without
    priming, the first reminder of a session — the one that matters most — is
    the silent one."""
    source = read("screenalert.js")
    assert "function primeAudio()" in source
    for event in ("pointerdown", "keydown", "touchstart"):
        assert event in source
    assert "{ once: true, passive: true }" in source


def test_a_reminder_does_not_wait_out_the_poll():
    """The queue is polled every thirty seconds. The change stream already knows
    the moment the scheduler queued something, so it says so and the poll
    happens immediately — measured at about a second instead of about thirty."""
    assert "medtracker:changed" in read("live.js")
    assert "medtracker:changed" in read("notifications.js")


def test_every_string_the_alert_shows_is_translated():
    import json

    source = read("screenalert.js")
    keys = set(re.findall(r"T\.t\('([a-z_.0-9]+)'\)", source))
    assert keys, "the alert renders no translated text at all"

    for language in ("en", "es"):
        catalog = json.loads(
            (ROOT / "app" / "i18n" / f"{language}.json").read_text(encoding="utf-8")
        )
        for key in keys:
            section, _, name = key.partition(".")
            assert name in catalog.get(section, {}), f"missing in {language}.json: {key}"


def test_the_settings_page_explains_why_this_device_gets_the_alert():
    """"The phone does not notify me" and "the phone cannot be notified this
    way, and here is what it does instead" are very different messages."""
    settings = read("settings.js")
    assert "ScreenAlert.needed()" in settings
    assert "settings.screen_alert_note" in settings
    assert "screen-alert-note" in (
        ROOT / "app" / "templates" / "settings.html"
    ).read_text(encoding="utf-8")


def test_the_test_button_shows_what_this_device_really_does():
    """It used to construct a Notification directly, so on a phone it did
    nothing at all and reported success."""
    settings = read("settings.js")
    block = settings[settings.index("test-notification"):]
    block = block[:block.index("});")]
    assert "Notifications.announce(" in block
    assert "new Notification(" not in block


def test_the_alert_is_loaded_before_the_code_that_uses_it():
    base = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
    assert base.index("screenalert.js") < base.index("notifications.js")
