"""The certificate that lets a phone show a real notification.

Why this exists at all: a browser only offers "Allow notifications?" — and only
allows a service worker, which is the *only* way an Android phone can put
anything in its notification shade — on a page it considers a **secure
context**. `http://192.168.1.9:8000` is not one. No setting changes that; it is
the browser's rule. So the application has to speak HTTPS on the local network,
and HTTPS on a local network means making a certificate, because no public
authority will ever issue one for `192.168.1.9`.

Two certificates, not one, and the reason matters:

* A **certificate authority**, generated once and installed on the phone. This
  is the thing the user trusts, and it is deliberately long-lived so it is
  installed once and never again.
* A **server certificate**, signed by that authority, naming every address this
  machine currently answers on. Home routers hand out different addresses over
  time, and when this machine's address changes the server certificate is
  reissued automatically — without the phone having to trust anything new,
  which is the whole point of having an authority in the middle.

The private keys never leave the data folder. What the user copies to the phone
is the authority's *certificate*, which contains no secret.
"""

from __future__ import annotations

import datetime as _datetime
import ipaddress
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

CA_NAME = "MedTracker Local CA"
SERVER_NAME = "MedTracker"

CA_YEARS = 10
SERVER_DAYS = 825          # the longest a leaf certificate is widely accepted


@dataclass
class Bundle:
    """Everything the server and the phone need, as files."""

    ca_certificate: Path
    ca_key: Path
    certificate: Path
    key: Path
    hosts: list[str]

    def exists(self) -> bool:
        return all(
            path.is_file()
            for path in (self.ca_certificate, self.ca_key, self.certificate, self.key)
        )


def paths() -> Bundle:
    from app.config import DATA_DIR

    folder = DATA_DIR / "certs"
    return Bundle(
        ca_certificate=folder / "medtracker-ca.crt",
        ca_key=folder / "medtracker-ca.key",
        certificate=folder / "server.crt",
        key=folder / "server.key",
        hosts=[],
    )


def _names() -> list[str]:
    """Every address this machine can be reached at, right now."""
    import socket

    from app.desktop.network import local_addresses

    names = ["localhost", "127.0.0.1"]
    for address in local_addresses():
        if address not in names:
            names.append(address)
    try:
        hostname = socket.gethostname()
        if hostname and hostname not in names:
            names.append(hostname)
    except OSError:  # pragma: no cover
        pass
    return names


def _san(names: list[str]):
    from cryptography import x509

    entries = []
    for name in names:
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            entries.append(x509.DNSName(name))
    return x509.SubjectAlternativeName(entries)


def _write(path: Path, data: bytes, private: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if private:
        try:
            path.chmod(0o600)
        except OSError:  # pragma: no cover - Windows does it by ACL, not mode
            pass


def _now():
    return _datetime.datetime.now(_datetime.timezone.utc)


def create_authority(bundle: Bundle) -> None:
    """The certificate the user installs on the phone. Made once."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, CA_NAME),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "MedTracker"),
    ])
    now = _now()
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _datetime.timedelta(days=1))
        .not_valid_after(now + _datetime.timedelta(days=365 * CA_YEARS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
        .sign(key, hashes.SHA256())
    )

    _write(
        bundle.ca_key,
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        private=True,
    )
    _write(
        bundle.ca_certificate,
        certificate.public_bytes(serialization.Encoding.PEM),
        private=False,
    )
    logger.info("Created the local certificate authority at %s", bundle.ca_certificate)


def create_server_certificate(bundle: Bundle, names: list[str]) -> None:
    """Signed by the authority, naming the addresses this machine answers on."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    ca_certificate = x509.load_pem_x509_certificate(bundle.ca_certificate.read_bytes())
    ca_key = serialization.load_pem_private_key(bundle.ca_key.read_bytes(), password=None)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = _now()
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, SERVER_NAME)]))
        .issuer_name(ca_certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _datetime.timedelta(days=1))
        .not_valid_after(now + _datetime.timedelta(days=SERVER_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(_san(names), critical=False)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )

    _write(
        bundle.key,
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        private=True,
    )
    _write(bundle.certificate, certificate.public_bytes(serialization.Encoding.PEM),
           private=False)
    logger.info("Issued a server certificate for %s", ", ".join(names))


def certificate_names(path: Path) -> list[str]:
    """The addresses an existing server certificate is valid for."""
    from cryptography import x509

    try:
        certificate = x509.load_pem_x509_certificate(path.read_bytes())
        san = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
    except Exception:  # noqa: BLE001 - an unreadable certificate is simply reissued
        return []
    return [str(entry) for entry in san.get_values_for_type(x509.DNSName)] + [
        str(entry) for entry in san.get_values_for_type(x509.IPAddress)
    ]


def expires_soon(path: Path, within_days: int = 30) -> bool:
    from cryptography import x509

    try:
        certificate = x509.load_pem_x509_certificate(path.read_bytes())
        expiry = certificate.not_valid_after_utc
    except Exception:  # noqa: BLE001
        return True
    return expiry - _now() < _datetime.timedelta(days=within_days)


def ensure(force: bool = False) -> Bundle:
    """Whatever is missing or out of date, made. Returns the bundle to serve.

    Called before the port is bound. The authority is created once and kept —
    reissuing it would mean asking the user to trust something on their phone
    again — while the server certificate is reissued whenever this machine's
    address has changed, which on a home router happens by itself.
    """
    bundle = paths()
    names = _names()
    bundle.hosts = names

    if force or not bundle.ca_certificate.is_file() or not bundle.ca_key.is_file():
        create_authority(bundle)

    reissue = (
        force
        or not bundle.certificate.is_file()
        or not bundle.key.is_file()
        or expires_soon(bundle.certificate)
        or not set(names) <= set(certificate_names(bundle.certificate))
    )
    if reissue:
        create_server_certificate(bundle, names)
    return bundle
