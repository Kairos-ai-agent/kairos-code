"""Network egress safety helpers (SSRF guard).

A small, dependency-free module used by the web-fetch tool and the LLM
config endpoints. Its job is to answer one question: **may this URL be
reached by an outbound request the backend issues on behalf of an
agent / user?** We reject anything that could reach the host itself,
an internal/private network, or a cloud-metadata endpoint.

This is the *defense-in-depth* half of the SSRF fix. The *authoritative*
half is authentication on the API (see api/auth.py), which stops an
unauthenticated remote caller from ever reaching these endpoints in the
first place.

Design notes:
  - ``@``-style userinfo and weird ports are stripped by urlparse before
    we look at the host.
  - IP-literal hosts are checked directly.
  - Hostnames are resolved via ``getaddrinfo`` and every returned address
    must be public; if DNS fails we **fail closed** (unsafe), because we
    would rather block a legitimate URL than accidentally allow a
    metadata endpoint.
  - The local (loopback) ``ollama`` preset is *not* routed through this
    check — that URL is a server-side constant set by Kairos, not a
    value supplied by an attacker.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# Reserved/metadata networks that an outbound fetch must never target.
PRIVATE_NETS = (
    "0.0.0.0/8",        # "this network"
    "10.0.0.0/8",       # private
    "127.0.0.0/8",      # loopback
    "169.254.0.0/16",   # link-local (incl. cloud metadata 169.254.169.254)
    "172.16.0.0/12",    # private
    "192.168.0.0/16",   # private
    "100.64.0.0/10",    # CGNAT
)
PRIVATE_NETS_V6 = (
    "::1/128",          # loopback v6
    "fc00::/7",         # unique-local v6
    "fe80::/10",        # link-local v6
)

_NETWORKS = [ipaddress.ip_network(s) for s in PRIVATE_NETS]
_NETWORKS_V6 = [ipaddress.ip_network(s) for s in PRIVATE_NETS_V6]

LOCAL_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata",
    "metadata.google.internal",
}


def _ip_internal(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    nets = _NETWORKS if ip.version == 4 else _NETWORKS_V6
    return any(ip in n for n in nets)


def _host_internal(host: str) -> bool:
    """True if ``host`` is internal / metadata / link-local, or resolves
    to any such address. Unknown/unresolvable => True (fail closed)."""
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        return True
    if host in LOCAL_HOSTNAMES or host.endswith(".localhost") or host.endswith(".local"):
        return True
    # IP-literal host?
    try:
        ip = ipaddress.ip_address(host)
        return _ip_internal(ip)
    except ValueError:
        pass
    # Hostname: every resolved address must be public.
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return True  # fail closed — could not verify it's public
    got_public = False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if _ip_internal(ip):
            return True
        got_public = True
    return not got_public


def is_public_url(url: str) -> bool:
    """Return True iff ``url`` is a public http(s) URL and safe to fetch."""
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    return not _host_internal(parsed.hostname or "")


def validate_public_url(url: str, *, what: str = "url") -> None:
    """Raise ``ValueError`` unless ``url`` is a safe public http(s) URL."""
    if not is_public_url(url):
        raise ValueError(
            f"{what} must be a public http(s) URL; host is internal, "
            f"loopback, private, or a cloud-metadata endpoint"
        )


def is_safe_config_url(url: str) -> bool:
    """Config-endpoint SSRF guard (lenient by design).

    We intentionally allow loopback and private-LAN hosts here: after the
    API is authenticated, a *local user* legitimately points Kairos at an
    internal / local LLM server (e.g. Ollama on 127.0.0.1, or a
    LM-Studio-style proxy on 192.168.x). The thing we must never fetch is
    the **link-local** range 169.254.0.0/16 — that includes the cloud
    metadata endpoint ``169.254.169.254`` — and unresolvable hosts (fail
    closed). Webfetch, by contrast, uses the strict ``validate_public_url``.
    """
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        return False
    _LINK_LOCAL = ipaddress.ip_network("169.254.0.0/16")
    # IP-literal host.
    try:
        ip = ipaddress.ip_address(host)
        if ip.version == 4:
            return ip not in _LINK_LOCAL
        return True
    except ValueError:
        pass
    # Hostname: resolve; block only if it lands on the link-local range.
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return False  # fail closed
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.version == 4 and ip in _LINK_LOCAL:
            return False
    return True


def validate_config_url(url: str, *, what: str = "url") -> None:
    """Raise ``ValueError`` unless ``url`` passes the config endpoint guard."""
    if not is_safe_config_url(url):
        raise ValueError(
            f"{what} resolves to a link-local / cloud-metadata host and "
            f"cannot be reached"
        )
