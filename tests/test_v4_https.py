"""HTTPS on the local network, and the notifications it unlocks.

The reason all of this exists, in one sentence: a browser only offers "Allow
notifications?" — and only allows a service worker, which is the only way an
Android phone can put anything in its notification shade — on a page it
considers a secure context, and `http://192.168.1.9:8000` is not one.

Measured, not assumed. The browser test at the end of this file registers a
service worker on a real TLS server over this machine's own network address and
shows a real notification through it; the same test on plain http could not,
which is what sent this application looking for a certificate in the first
place.
"""

from __future__ import annotations

import os
import shutil
import socket
import ssl
import sqlite3
import subprocess
from pathlib import Path

import pytest

from app.services.settings_service import get_settings


# --------------------------------------------------------------------------- #
# The certificates
# --------------------------------------------------------------------------- #
@pytest.fixture()
def certs(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.DATA_DIR", tmp_path)
    return tmp_path / "certs"


def test_an_authority_and_a_certificate_are_made_once(certs, monkeypatch):
    from app.services import certificates

    monkeypatch.setattr(
        "app.desktop.network.local_addresses", lambda: ["192.168.1.9"]
    )
    bundle = certificates.ensure()

    assert bundle.exists()
    assert bundle.ca_certificate.parent == certs
    names = certificates.certificate_names(bundle.certificate)
    assert "127.0.0.1" in names and "localhost" in names and "192.168.1.9" in names

    # Running again changes nothing: reissuing the authority would mean asking
    # the user to trust something on their phone all over again.
    before = bundle.ca_certificate.read_bytes(), bundle.certificate.read_bytes()
    certificates.ensure()
    assert (bundle.ca_certificate.read_bytes(), bundle.certificate.read_bytes()) == before


def test_a_new_address_reissues_the_certificate_but_not_the_authority(certs, monkeypatch):
    """Home routers hand out different addresses over time. The phone must not
    have to trust anything new when that happens."""
    from app.services import certificates

    monkeypatch.setattr("app.desktop.network.local_addresses", lambda: ["192.168.1.9"])
    bundle = certificates.ensure()
    authority = bundle.ca_certificate.read_bytes()
    first = bundle.certificate.read_bytes()

    monkeypatch.setattr("app.desktop.network.local_addresses", lambda: ["192.168.1.44"])
    certificates.ensure()

    assert bundle.ca_certificate.read_bytes() == authority, "the phone would need re-trusting"
    assert bundle.certificate.read_bytes() != first
    assert "192.168.1.44" in certificates.certificate_names(bundle.certificate)


def test_the_authority_is_a_certificate_authority_and_the_server_one_is_not(certs):
    """Android will only install the first as a CA, and browsers refuse to use a
    CA certificate as a server certificate."""
    from cryptography import x509

    from app.services import certificates

    bundle = certificates.ensure()
    authority = x509.load_pem_x509_certificate(bundle.ca_certificate.read_bytes())
    server = x509.load_pem_x509_certificate(bundle.certificate.read_bytes())

    assert authority.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert not server.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert server.issuer == authority.subject
    usage = server.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert x509.ObjectIdentifier("1.3.6.1.5.5.7.3.1") in usage      # server auth


def test_the_private_keys_are_never_served(client, certs):
    """The phone is given the authority's *certificate*. Everything else stays
    on the disk it was made on."""
    from app.services import certificates

    certificates.ensure()
    assert client.get("/api/certificate").status_code == 200

    for path in ("/api/certificate/../server.key", "/static/certs/server.key"):
        assert client.get(path).status_code in (404, 403, 400)

    body = client.get("/api/certificate").content
    assert b"BEGIN CERTIFICATE" in body
    assert b"PRIVATE KEY" not in body


def test_the_certificate_is_made_the_moment_it_is_asked_for(client, certs):
    """It used to be created only when HTTPS was switched on, so downloading it
    first — which is the order that avoids doing everything else behind a
    browser warning — answered 404, and the phone reported a failed download
    with no explanation at all."""
    assert not (certs / "medtracker-ca.crt").exists()

    response = client.get("/api/certificate")

    assert response.status_code == 200
    assert b"BEGIN CERTIFICATE" in response.content
    assert (certs / "medtracker-ca.crt").is_file()

    # And it is the same one the server will use, not a throwaway.
    from app.services import certificates

    bundle = certificates.ensure()
    assert bundle.ca_certificate.read_bytes() == response.content


def test_the_page_says_when_the_other_half_is_still_undone(client, certs, monkeypatch):
    """Installing a certificate and seeing nothing change afterwards is the
    worst possible outcome of a four-step procedure."""
    monkeypatch.setattr("app.desktop.network.https_enabled", lambda: False)
    assert "certificate.https_still_off" in client.get("/certificate").text

    monkeypatch.setattr("app.desktop.network.https_enabled", lambda: True)
    assert "certificate.https_still_off" not in client.get("/certificate").text


# --------------------------------------------------------------------------- #
# The setting
# --------------------------------------------------------------------------- #
def make_settings_db(path: Path, network: int, https: int) -> None:
    connection = sqlite3.connect(str(path))
    connection.execute(
        "CREATE TABLE settings (id INTEGER PRIMARY KEY,"
        " network_access BOOLEAN NOT NULL DEFAULT 0,"
        " https_enabled BOOLEAN NOT NULL DEFAULT 0)"
    )
    connection.execute(
        "INSERT INTO settings (id, network_access, https_enabled) VALUES (1, ?, ?)",
        (network, https),
    )
    connection.commit()
    connection.close()


@pytest.mark.parametrize(
    ("network", "https", "expected"),
    [
        (0, 0, "http"),
        (1, 0, "http"),
        (0, 1, "http"),      # nothing to secure: only this computer can reach it
        (1, 1, "https"),
    ],
)
def test_tls_is_used_only_where_it_buys_something(monkeypatch, tmp_path, network, https, expected):
    from app.desktop import network as net

    path = tmp_path / "medtracker.db"
    monkeypatch.setattr("app.config.DB_PATH", path)
    make_settings_db(path, network, https)

    assert net.scheme() == expected


def test_an_unreadable_database_never_turns_tls_on(monkeypatch, tmp_path):
    from app.desktop import network as net

    path = tmp_path / "medtracker.db"
    monkeypatch.setattr("app.config.DB_PATH", path)
    path.write_bytes(b"not a database")
    assert net.https_enabled() is False


def test_upgrading_does_not_turn_https_on(tmp_path):
    from app.database.migrations import _migrate_7_to_8

    path = tmp_path / "v7.db"
    connection = sqlite3.connect(str(path))
    connection.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY)")
    connection.execute("INSERT INTO settings (id) VALUES (1)")
    connection.commit()

    _migrate_7_to_8(connection)
    _migrate_7_to_8(connection)          # idempotent

    assert connection.execute("SELECT https_enabled FROM settings").fetchone() == (0,)
    connection.close()


def test_the_switch_round_trips_through_the_api(client):
    assert client.get("/api/settings").json()["https_enabled"] is False
    assert client.put("/api/settings", json={"https_enabled": True}).status_code == 200
    assert client.get("/api/settings").json()["https_enabled"] is True


def test_the_status_page_says_whether_the_phone_can_be_notified(db, monkeypatch):
    from app.services import system_status

    settings = get_settings(db)
    settings.network_access = True
    db.flush()
    monkeypatch.setattr(system_status, "_bound_host", "0.0.0.0")
    monkeypatch.setattr("app.desktop.network.local_addresses", lambda: ["192.168.1.9"])

    monkeypatch.setattr("app.desktop.network.https_enabled", lambda: False)
    row = system_status._network(settings)
    assert row["https"] is False
    assert row["addresses"] == ["http://192.168.1.9:8000"]

    monkeypatch.setattr("app.desktop.network.https_enabled", lambda: True)
    row = system_status._network(settings)
    assert row["https"] is True
    assert row["addresses"] == ["https://192.168.1.9:8000"]


# --------------------------------------------------------------------------- #
# The service worker
# --------------------------------------------------------------------------- #
def test_the_worker_is_served_from_the_root(client):
    """From `/static/sw.js` a worker could do nothing for `/` or `/medications`
    — a worker only ever acts for pages inside its own path."""
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert response.headers.get("service-worker-allowed") == "/"


def test_the_worker_caches_nothing():
    """A service worker that starts answering from a cache would serve stale
    medical data. This one exists only so notifications have somewhere to live."""
    import re

    source = (
        Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "sw.js"
    ).read_text(encoding="utf-8")
    # The comments explain at length why it caches nothing, so they are removed
    # before looking for the thing they are about.
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    code = re.sub(r"//.*", "", code)

    assert "caches" not in code
    assert "fetch" not in code
    assert "notificationclick" in code


def test_the_notification_path_prefers_the_worker_then_falls_back():
    source = (
        Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "notifications.js"
    ).read_text(encoding="utf-8")
    block = source[source.index("async function announce"):]
    block = block[:block.index("\n  }")]
    assert block.index("showViaWorker") < block.index("show(item)")
    assert block.index("show(item)") < block.index("ScreenAlert.show")


# --------------------------------------------------------------------------- #
# The whole chain, in a real browser
# --------------------------------------------------------------------------- #
def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def lan_address() -> str | None:
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 9))
            return probe.getsockname()[0]
        finally:
            probe.close()
    except OSError:
        return None


@pytest.mark.skipif(
    shutil.which("xvfb-run") is None, reason="needs a display to show a notification"
)
def test_a_browser_on_the_network_address_can_show_a_real_notification(tmp_path):
    """The claim this whole feature rests on, checked end to end.

    Headed, under a virtual display, because headless Chromium has nowhere to
    put a notification and refuses the permission outright — which would make a
    headless "it works" mean nothing at all.
    """
    pytest.importorskip("playwright")
    address = lan_address()
    if not address or address.startswith("127."):
        pytest.skip("no address on a local network to test against")

    port = free_port()
    data = tmp_path / "data"
    data.mkdir()

    environment = dict(os.environ)
    environment.update({
        "MEDTRACKER_DATA_DIR": str(data),
        "MEDTRACKER_HOST": "0.0.0.0",
        "MEDTRACKER_PORT": str(port),
        "MEDTRACKER_DISABLE_SCHEDULER": "1",
    })
    root = Path(__file__).resolve().parent.parent

    subprocess.run(
        [os.sys.executable, "-c",
         "from app.database.db import init_db, session_scope; init_db();"
         "from app.services.settings_service import get_settings;"
         "s=session_scope().__enter__();"
         "settings=get_settings(s); settings.network_access=True;"
         "settings.https_enabled=True; s.commit()"],
        cwd=root, env=environment, check=True, capture_output=True, timeout=120,
    )

    server = subprocess.Popen(
        [os.sys.executable, "-m", "app.main"],
        cwd=root, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    script = root / "tests" / "browser" / "notification_probe.py"
    try:
        import time
        import urllib.request

        context = ssl._create_unverified_context()
        for _ in range(60):
            time.sleep(0.5)
            try:
                urllib.request.urlopen(
                    f"https://127.0.0.1:{port}/api/health", timeout=2, context=context
                )
                break
            except Exception:  # noqa: BLE001
                continue
        else:
            pytest.fail("the TLS server never came up")

        probe = subprocess.run(
            ["xvfb-run", "-a", os.sys.executable, str(script),
             f"https://{address}:{port}"],
            cwd=root, capture_output=True, text=True, timeout=300,
            env={**environment, "no_proxy": f"{os.environ.get('no_proxy', '')},{address}"},
        )
    finally:
        server.terminate()
        server.wait(timeout=30)

    assert probe.returncode == 0, probe.stdout + probe.stderr
    report = probe.stdout.strip().splitlines()[-1]
    assert "secure=True" in report, report
    assert "worker=True" in report, report
    assert "shown=1" in report, report


def test_the_certificate_page_works_before_the_pin(client, certs):
    """Ordering matters: on a phone the certificate has to be installed
    *before* anything else can work, and requiring the PIN first would mean
    typing it into a page the browser is still calling untrusted."""
    from tests.test_v4_review import enable_lock

    from app.services import certificates

    certificates.ensure()
    enable_lock(client)
    client.post("/api/lock/lock")

    assert client.get("/api/today").status_code == 423        # still locked

    page = client.get("/certificate")
    assert page.status_code == 200
    assert b"certificate.step_1" in page.content or b"certificate" in page.content

    download = client.get("/api/certificate")
    assert download.status_code == 200
    assert b"BEGIN CERTIFICATE" in download.content
    assert b"PRIVATE KEY" not in download.content


def test_the_certificate_page_shows_nothing_medical(client, certs):
    from app.services import certificates

    certificates.ensure()
    body = client.get("/certificate").text
    for forbidden in ("main-nav", "topsearch", "bell-count", "medications"):
        assert forbidden not in body, forbidden


def test_the_certificate_page_warns_about_the_download_prompt():
    """Chrome refuses to call a download secure when the origin's certificate is
    not trusted — which is precisely the situation, since the file being
    downloaded is the thing that would make it trusted. It offers Discard or
    Keep, and a page that does not mention that reads as a failure."""
    import json

    root = Path(__file__).resolve().parent.parent
    for language, keep in (("en", "Keep"), ("es", "Conservar")):
        catalog = json.loads(
            (root / "app" / "i18n" / f"{language}.json").read_text(encoding="utf-8")
        )
        step = catalog["certificate"]["step_1"]
        assert keep in step, f"{language} does not tell the user to tap {keep}"
