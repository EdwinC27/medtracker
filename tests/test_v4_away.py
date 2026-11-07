"""Reaching the application from a network whose router is not yours.

Away from home there is no DHCP reservation to make and no static address worth
setting, so the machine's own name is the only stable way in. Three things have
to hold for that to be more than a nice idea, and each has its own failure that
looks exactly like "the app is broken":

* Windows must not have the network marked **Public**, or the firewall drops
  every incoming connection silently.
* Something must be able to say whether another device actually got in, and by
  which address — otherwise every diagnosis is a guess.
* None of it may cost the user their privacy or their data: this is read-only
  reporting, kept in memory.
"""

from __future__ import annotations

import json

import pytest

from app.services.settings_service import get_settings


# --------------------------------------------------------------------------- #
# Public or Private
# --------------------------------------------------------------------------- #
def test_powershell_gives_one_object_for_one_network_and_a_list_for_several():
    """`ConvertTo-Json` does not wrap a single result in a list, which is the
    classic way this kind of parsing breaks on the one-network machine that is
    the normal case."""
    from app.desktop import netprofile

    one = netprofile.parse(json.dumps(
        {"Name": "Infinitum1234", "NetworkCategory": "Private", "IPv4Connectivity": "Internet"}
    ))
    assert [profile.to_dict() for profile in one] == [
        {"name": "Infinitum1234", "category": "private", "connected": True}
    ]

    several = netprofile.parse(json.dumps([
        {"Name": "Cafe", "NetworkCategory": "Public", "IPv4Connectivity": "Internet"},
        {"Name": "Ethernet", "NetworkCategory": "DomainAuthenticated", "IPv4Connectivity": "LocalNetwork"},
    ]))
    assert [profile.category for profile in several] == ["public", "domain"]


def test_the_shape_powershell_actually_returns_is_understood():
    """`NetworkCategory` and `IPv4Connectivity` are enumerations, and
    `ConvertTo-Json` writes an enumeration as its *number*. The query asks for
    the string form, but a PowerShell that ignores the cast must not silently
    turn the warning off — which is what "unknown" would do."""
    from app.desktop import netprofile

    numbers = netprofile.parse(json.dumps([
        {"Name": "Hotel", "NetworkCategory": 0, "IPv4Connectivity": 4},     # public
        {"Name": "Casa", "NetworkCategory": 1, "IPv4Connectivity": 4},      # private
        {"Name": "Oficina", "NetworkCategory": 2, "IPv4Connectivity": 3},   # domain
        {"Name": "WSL", "NetworkCategory": 0, "IPv4Connectivity": 1},       # no traffic
        {"Name": "Cable", "NetworkCategory": 0, "IPv4Connectivity": 0},     # unplugged
    ]))
    assert [(p.category, p.connected) for p in numbers] == [
        ("public", True), ("private", True), ("domain", True),
        ("public", False), ("public", False),
    ]


def test_the_query_asks_windows_for_words_rather_than_numbers():
    from app.desktop import netprofile

    assert "[string]$_.NetworkCategory" in netprofile._COMMAND
    assert "[string]$_.IPv4Connectivity" in netprofile._COMMAND


@pytest.mark.parametrize("raw", ["", "   ", "not json at all", "null", "42"])
def test_an_unreadable_answer_is_no_answer_rather_than_a_crash(raw):
    """PowerShell can be restricted by policy, or absent. The status page must
    still open, and must not claim the network is fine."""
    from app.desktop import netprofile

    assert netprofile.parse(raw) == []


def test_a_disconnected_adapter_is_not_a_network_the_user_is_on():
    """Virtual adapters — VirtualBox, WSL — sit there with no traffic. Warning
    about one of those would be a warning about nothing."""
    from app.desktop import netprofile

    profiles = netprofile.parse(json.dumps([
        {"Name": "VirtualBox Host-Only", "NetworkCategory": "Public", "IPv4Connectivity": "NoTraffic"},
    ]))
    assert profiles[0].connected is False


def test_only_a_machine_with_no_private_network_is_warned_about(monkeypatch):
    from app.desktop import netprofile

    def answer(entries):
        netprofile.forget()
        monkeypatch.setattr(netprofile, "current", lambda force=False: netprofile.parse(
            json.dumps(entries)
        ))

    answer([{"Name": "Cafe", "NetworkCategory": "Public", "IPv4Connectivity": "Internet"}])
    blocking = netprofile.blocks_other_devices()
    assert blocking is not None and blocking.name == "Cafe"

    # On a public Wi-Fi *and* a private LAN there is still a way in.
    answer([
        {"Name": "Cafe", "NetworkCategory": "Public", "IPv4Connectivity": "Internet"},
        {"Name": "Casa", "NetworkCategory": "Private", "IPv4Connectivity": "Internet"},
    ])
    assert netprofile.blocks_other_devices() is None

    answer([])
    assert netprofile.blocks_other_devices() is None


def test_nothing_is_asked_of_a_machine_that_is_not_windows(monkeypatch):
    from app.desktop import netprofile

    netprofile.forget()
    monkeypatch.setattr(netprofile, "supported", lambda: False)

    def explode(*_args, **_kwargs):  # pragma: no cover - the point is it is not called
        raise AssertionError("PowerShell was run on a machine that has none")

    monkeypatch.setattr("subprocess.run", explode)
    assert netprofile.current() == []


def test_the_answer_is_remembered_so_the_page_does_not_pay_for_it_twice(monkeypatch):
    """PowerShell costs about a second to start. Opening the page must not."""
    from app.desktop import netprofile

    netprofile.forget()
    calls = []

    monkeypatch.setattr(netprofile, "supported", lambda: True)
    monkeypatch.setattr(netprofile, "_ask_windows", lambda: calls.append(1) or [])

    netprofile.current()
    netprofile.current()
    netprofile.current()
    assert len(calls) == 1

    netprofile.current(force=True)
    assert len(calls) == 2
    netprofile.forget()


def test_no_console_window_is_flashed_at_the_user(monkeypatch):
    """The application runs windowed from the tray. A bare subprocess call on
    Windows pops a black box open for as long as PowerShell takes."""
    from app.desktop import netprofile

    netprofile.forget()
    seen = {}

    class Done:
        returncode = 0
        stdout = "{}"
        stderr = ""

    def fake_run(command, **kwargs):
        seen.update(kwargs)
        seen["command"] = command
        return Done()

    monkeypatch.setattr(netprofile, "supported", lambda: True)
    monkeypatch.setattr("subprocess.run", fake_run)
    netprofile.current(force=True)
    netprofile.forget()

    assert "creationflags" in seen
    assert seen["timeout"] <= 10
    assert "-NonInteractive" in seen["command"]
    assert "Get-NetConnectionProfile" in " ".join(seen["command"])


def test_a_slow_or_broken_powershell_never_takes_the_status_page_with_it(monkeypatch):
    import subprocess

    from app.desktop import netprofile

    netprofile.forget()
    monkeypatch.setattr(netprofile, "supported", lambda: True)

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="powershell", timeout=8)

    monkeypatch.setattr("subprocess.run", timeout)
    assert netprofile.current(force=True) == []
    netprofile.forget()


# --------------------------------------------------------------------------- #
# Answering to the machine's own name — and to no more than that
# --------------------------------------------------------------------------- #
def test_the_bare_computer_name_is_never_accepted(monkeypatch):
    """`edwin-castillo` with no suffix is resolved on Windows by LLMNR and
    NetBIOS — unauthenticated broadcasts that any device on a café Wi-Fi can
    answer. Whoever answers becomes that origin in the browser, and an origin
    this application accepts can read every row and call `/api/import`. The
    application never advertises the bare name, so accepting it would be a hole
    bought for nothing."""
    from app.routes import origin

    monkeypatch.setattr("app.desktop.network.https_enabled", lambda: True)
    monkeypatch.setattr(
        "app.services.certificates.host_names",
        lambda: ["Edwin-Castillo", "Edwin-Castillo.local"],
    )
    origin.forget_tls()

    assert origin.is_acceptable_host("Edwin-Castillo.local:8000")
    assert not origin.is_acceptable_host("Edwin-Castillo:8000")
    assert not origin.is_acceptable_host("edwin-castillo")


def test_the_name_is_accepted_only_when_tls_is_on(monkeypatch):
    """An impostor can claim the name; it cannot present a certificate this
    machine's authority signed. Under plain http there is no such barrier — and
    no notifications either, so the name buys nothing there."""
    from app.routes import origin

    monkeypatch.setattr(
        "app.services.certificates.host_names",
        lambda: ["Edwin-Castillo", "Edwin-Castillo.local"],
    )

    monkeypatch.setattr("app.desktop.network.https_enabled", lambda: False)
    origin.forget_tls()
    assert not origin.is_acceptable_host("Edwin-Castillo.local:8000")

    monkeypatch.setattr("app.desktop.network.https_enabled", lambda: True)
    origin.forget_tls()
    assert origin.is_acceptable_host("Edwin-Castillo.local:8000")


def test_an_unreadable_database_refuses_the_name_rather_than_guessing(monkeypatch):
    from app.routes import origin

    def broken():
        raise OSError("the database is not readable")

    monkeypatch.setattr("app.desktop.network.https_enabled", broken)
    monkeypatch.setattr(
        "app.services.certificates.host_names", lambda: ["pc", "pc.local"]
    )
    origin.forget_tls()
    assert not origin.is_acceptable_host("pc.local:8000")


def test_the_answer_about_tls_is_not_read_from_disk_on_every_request(monkeypatch):
    """This runs on every request the phone makes, and `https_enabled()` opens
    the database. The certificate is chosen before the port is bound, so the
    answer cannot change while the process runs."""
    from app.routes import origin

    calls = []
    monkeypatch.setattr(
        "app.desktop.network.https_enabled", lambda: calls.append(1) or True
    )
    monkeypatch.setattr(
        "app.services.certificates.host_names", lambda: ["pc", "pc.local"]
    )
    origin.forget_tls()

    for _ in range(5):
        origin.is_acceptable_host("pc.local:8000")
    assert len(calls) == 1


def test_an_absolute_name_with_a_trailing_dot_is_the_same_name(monkeypatch):
    from app.routes import origin

    monkeypatch.setattr("app.desktop.network.https_enabled", lambda: True)
    monkeypatch.setattr(
        "app.services.certificates.host_names", lambda: ["pc", "pc.local"]
    )
    origin.forget_tls()

    assert origin.is_acceptable_host("pc.local.:8000")
    assert origin.is_acceptable_host("PC.LOCAL.")


@pytest.mark.parametrize("header", ["//[evil", "http://[::1", "[", "]:8000", "://"])
def test_a_malformed_header_is_refused_and_not_a_crash(header):
    """`urlsplit` raises on an unterminated bracket. A guard that raises turns
    every scan on the network into a 500 and a traceback in the log."""
    from app.routes import origin

    assert origin._hostname(header) in ("", "[", "[]", "]", "://")
    assert origin.is_acceptable_host(header) is False


def test_a_malformed_header_does_not_reach_the_error_handler(client):
    for header in ("//[evil", "http://[::1"):
        response = client.get("/api/settings", headers={"host": header})
        assert response.status_code == 403, response.text


# --------------------------------------------------------------------------- #
# A name a certificate cannot carry
# --------------------------------------------------------------------------- #
def test_a_computer_name_with_an_accent_does_not_stop_the_application(monkeypatch):
    """Windows allows `PC-Muñoz`; `x509.DNSName` does not, and this is called
    while the certificate is being built, before the port is bound. Unencoded,
    a Spanish computer name would mean the application simply never starts."""
    from app.services import certificates

    monkeypatch.setattr("socket.gethostname", lambda: "PC-Muñoz")
    names = certificates.host_names()
    assert names == ["xn--pc-muoz-8za", "xn--pc-muoz-8za.local"]

    from cryptography import x509

    for name in names:
        x509.DNSName(name)          # would raise on a non-ASCII label


def test_a_name_that_cannot_be_encoded_at_all_is_simply_left_out(monkeypatch):
    from app.services import certificates

    monkeypatch.setattr("socket.gethostname", lambda: "\udcff\udcfe")
    assert certificates.host_names() == []


def test_a_certificate_that_cannot_be_built_costs_notifications_not_the_app(monkeypatch):
    """Plain HTTP with a line in the log is recoverable. Refusing to start is
    not, and everything behind that door is medication."""
    import app.main as main

    monkeypatch.setattr("app.desktop.network.https_enabled", lambda: True)
    monkeypatch.setattr(
        "app.services.certificates.ensure",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no space left on device")),
    )
    assert main._tls_options() == {}


def test_both_ways_of_starting_agree_about_tls():
    """The .exe and `start.bat` must not differ on whether TLS is served or on
    what happens when the certificate cannot be made."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    launcher = (root / "app" / "desktop" / "launcher.py").read_text(encoding="utf-8")
    assert "_tls_options()" in launcher
    assert "ssl_certfile" not in launcher, "a second, divergent copy of the decision"


# --------------------------------------------------------------------------- #
# Did another device actually get in?
# --------------------------------------------------------------------------- #
def test_this_computer_looking_at_itself_is_not_another_device(monkeypatch):
    from app.services import reach

    reach.forget()
    reach.note("127.0.0.1", "127.0.0.1:8000")
    reach.note("::1", "localhost:8000")
    reach.note("::ffff:127.0.0.1", "localhost:8000")
    assert reach.last() is None


def test_the_pc_visiting_by_its_own_name_is_still_the_pc(monkeypatch):
    """Opening `https://Edwin-Castillo.local:8000` in Edge *on the computer*
    does not arrive from 127.0.0.1 — the kernel picks the LAN address, because
    that is what the name resolved to. Counting it would make the page announce
    "the name works, another device reached it" when nothing did, and he would
    stop testing on the phone."""
    from app.services import reach

    monkeypatch.setattr("app.desktop.network.local_addresses", lambda: ["192.168.1.20"])
    reach.forget()

    reach.note("192.168.1.20", "Edwin-Castillo.local:8000")
    assert reach.last() is None, "that was this computer"

    reach.note("192.168.1.34", "Edwin-Castillo.local:8000")
    assert reach.last() is not None, "that one was the phone"


def test_this_machines_addresses_are_not_looked_up_on_every_request(monkeypatch):
    """A UDP socket and a `getaddrinfo` per request, on the path every poll
    from the phone takes."""
    from app.services import reach

    calls = []
    monkeypatch.setattr(
        "app.desktop.network.local_addresses", lambda: calls.append(1) or ["192.168.1.20"]
    )
    reach.forget()

    for _ in range(10):
        reach.note("192.168.1.34", "pc.local:8000")
    assert len(calls) == 1


def test_a_phone_is_recorded_with_the_address_it_used():
    from app.services import reach

    reach.forget()
    reach.note("192.168.1.34", "Edwin-Castillo.local:8000")
    visit = reach.last()
    assert visit is not None
    assert visit.address == "192.168.1.34"
    assert visit.host == "Edwin-Castillo.local"
    assert visit.by_name is True

    reach.note("192.168.1.34", "192.168.1.20:8000")
    assert reach.last().by_name is False, "a number is not a name"
    reach.forget()


def test_the_record_travels_through_a_real_request(client, monkeypatch):
    """Not the function in isolation — the guard's accept path, in the app."""
    from app.routes import origin
    from app.services import reach

    monkeypatch.setattr("app.desktop.network.https_enabled", lambda: True)
    origin.forget_tls()
    monkeypatch.setattr(
        "app.services.certificates.host_names",
        lambda: ["Edwin-Castillo", "Edwin-Castillo.local"],
    )
    reach.forget()
    response = client.get(
        "/api/settings",
        headers={"host": "Edwin-Castillo.local:8000"},
    )
    assert response.status_code == 200
    visit = reach.last()
    assert visit is not None and visit.host == "Edwin-Castillo.local"
    reach.forget()


def test_a_refused_cross_site_request_is_not_a_device_that_got_in(client):
    """Otherwise the page would report a hostile page in another tab as proof
    that the phone works."""
    from app.services import reach

    reach.forget()
    response = client.post(
        "/api/settings",
        json={"language": "es"},
        headers={"host": "evil.example:8000", "origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert reach.last() is None


def test_nothing_medical_is_kept(client, monkeypatch):
    """This is a diagnostic, and diagnostics have a way of growing. What it may
    hold is an address and a time — never a path, a payload or a name."""
    from app.routes import origin
    from app.services import reach

    monkeypatch.setattr("app.desktop.network.https_enabled", lambda: True)
    origin.forget_tls()
    monkeypatch.setattr(
        "app.services.certificates.host_names",
        lambda: ["Edwin-Castillo", "Edwin-Castillo.local"],
    )
    reach.forget()
    client.get("/api/medications", headers={"host": "Edwin-Castillo.local:8000"})
    stored = reach.last().to_dict()
    assert set(stored) == {"address", "host", "by_name", "at"}
    assert "medication" not in json.dumps(stored).lower()
    reach.forget()


# --------------------------------------------------------------------------- #
# What the page says about all of it
# --------------------------------------------------------------------------- #
@pytest.fixture()
def open_network(db, monkeypatch):
    from app.services import system_status

    settings = get_settings(db)
    settings.network_access = True
    db.flush()
    monkeypatch.setattr(system_status, "_bound_host", "0.0.0.0")
    monkeypatch.setattr("app.desktop.network.local_addresses", lambda: ["192.168.1.20"])
    monkeypatch.setattr("app.services.certificates.host_names", lambda: [])
    return settings


def test_a_public_network_is_a_warning_and_not_a_green_light(open_network, monkeypatch):
    from app.desktop import netprofile
    from app.services import system_status

    netprofile.forget()
    monkeypatch.setattr(
        netprofile, "current",
        lambda force=False: netprofile.parse(json.dumps(
            {"Name": "Hotel", "NetworkCategory": "Public", "IPv4Connectivity": "Internet"}
        )),
    )

    row = system_status._network(open_network)
    assert row["level"] == "warning"
    assert row["detail_key"] == "status.network_public_profile"
    assert row["profile"] == {"name": "Hotel", "category": "public", "connected": True}


def test_a_phone_that_just_connected_beats_the_warning(open_network, monkeypatch):
    """Windows asks once whether to allow the program on public networks too.
    Somebody who said yes has a public profile *and* a working phone — telling
    him it cannot connect while it is connecting is how a status page loses the
    right to be believed."""
    from app.desktop import netprofile
    from app.services import reach, system_status

    monkeypatch.setattr(
        netprofile, "current",
        lambda force=False: netprofile.parse(json.dumps(
            {"Name": "Casa", "NetworkCategory": 0, "IPv4Connectivity": 4}
        )),
    )
    monkeypatch.setattr("app.desktop.network.local_addresses", lambda: ["192.168.1.20"])

    reach.note("192.168.1.34", "Edwin-Castillo.local:8000")
    row = system_status._network(open_network)
    assert row["level"] == "ok"
    assert row["profile"]["category"] == "public", "still said out loud"


def test_an_old_visit_does_not_vouch_for_the_network_he_is_on_now(open_network, monkeypatch):
    from datetime import timedelta

    from app.desktop import netprofile
    from app.services import reach, system_status
    from app.utils.timeutil import now_local

    monkeypatch.setattr(
        netprofile, "current",
        lambda force=False: netprofile.parse(json.dumps(
            {"Name": "Hotel", "NetworkCategory": 0, "IPv4Connectivity": 4}
        )),
    )
    monkeypatch.setattr("app.desktop.network.local_addresses", lambda: ["192.168.1.20"])

    reach.note("192.168.1.34", "Edwin-Castillo.local:8000")
    stale = now_local() - timedelta(minutes=system_status.RECENT_VISIT_MINUTES + 1)
    reach.last().at = stale

    assert system_status._network(open_network)["level"] == "warning"


def test_a_private_network_is_reported_without_alarming_anybody(open_network, monkeypatch):
    from app.desktop import netprofile
    from app.services import system_status

    netprofile.forget()
    monkeypatch.setattr(
        netprofile, "current",
        lambda force=False: netprofile.parse(json.dumps(
            {"Name": "Casa", "NetworkCategory": "Private", "IPv4Connectivity": "Internet"}
        )),
    )

    row = system_status._network(open_network)
    assert row["level"] == "ok"
    assert row["profile"]["category"] == "private"


def test_a_machine_that_cannot_be_asked_is_reported_as_not_known(open_network, monkeypatch):
    from app.desktop import netprofile
    from app.services import system_status

    netprofile.forget()
    monkeypatch.setattr(netprofile, "current", lambda force=False: [])

    row = system_status._network(open_network)
    assert row["profile"] is None
    assert row["level"] == "ok", "silence is not a fault"


def test_the_page_carries_the_last_visit(open_network, monkeypatch):
    from app.desktop import netprofile
    from app.services import reach, system_status

    netprofile.forget()
    monkeypatch.setattr(netprofile, "current", lambda force=False: [])

    reach.forget()
    assert system_status._network(open_network)["last_visit"] is None

    reach.note("192.168.1.34", "Edwin-Castillo.local:8000")
    row = system_status._network(open_network)
    assert row["last_visit"]["host"] == "Edwin-Castillo.local"
    assert row["last_visit"]["by_name"] is True
    reach.forget()


def test_none_of_this_is_reported_when_the_network_is_closed(db, monkeypatch):
    """With the setting off, nothing outside can reach the app, so a profile
    warning would be noise about a problem the user does not have."""
    from app.services import reach, system_status

    settings = get_settings(db)
    settings.network_access = False
    db.flush()
    monkeypatch.setattr(system_status, "_bound_host", "127.0.0.1")

    reach.note("192.168.1.34", "Edwin-Castillo.local:8000")
    row = system_status._network(settings)
    assert row["profile"] is None
    assert row["last_visit"] is None
    reach.forget()


# --------------------------------------------------------------------------- #
# The page that shows it
# --------------------------------------------------------------------------- #
def test_every_key_the_page_asks_for_exists_in_both_languages():
    """A missing key does not crash anything — it prints the key itself on the
    screen, in the middle of the sentence that was supposed to help."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    source = (root / "app" / "static" / "js" / "settings_v4.js").read_text(encoding="utf-8")
    used = {
        key for key in re.findall(r"T\.t\(\s*'(status\.[a-z0-9_]+)'", source)
        # A key ending in `_` is half of a name the code completes at runtime.
        if not key.endswith("_")
    }
    # Those runtime halves, spelled out, since they cannot be read literally.
    used |= {
        f"status.network_profile_{name}" for name in
        ("public", "private", "domain", "unknown")
    }
    used |= {f"status.overall_{level}" for level in ("ok", "warning", "error")}

    for language in ("en", "es"):
        data = json.loads((root / "app" / "i18n" / f"{language}.json").read_text("utf-8"))
        missing = [key for key in sorted(used) if key.split(".", 1)[1] not in data["status"]]
        assert not missing, f"{language} is missing: {missing}"


def test_the_copy_button_is_not_offered_where_it_cannot_work():
    """`navigator.clipboard` is undefined outside a secure context, and calling
    it there throws — on exactly the plain-http LAN setup this app supports."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    source = (root / "app" / "static" / "js" / "settings_v4.js").read_text(encoding="utf-8")
    assert "navigator.clipboard && window.isSecureContext" in source
