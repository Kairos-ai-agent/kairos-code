"""The extended provider registry: entries a client can actually be pointed at.

One entry here is a self-hosted router rather than a vendor, and it is the reason
this file exists: the router publishes its own live catalogue, so an entry that
carried a copy of it would be wrong twice over -- stale within the week, and
carrying exactly the detail we deliberately do not ship.
"""
from __future__ import annotations

from kairos.providers_more import get_provider, list_providers


def test_the_registry_has_entries() -> None:
    providers = list_providers()
    assert len(providers) >= 8
    assert all(p.id and p.base_url for p in providers), "an entry without an id or URL is unusable"


def test_a_self_hosted_router_entry_carries_no_catalogue() -> None:
    p = get_provider("freellmapi")
    assert p is not None, "the self-hosted router entry is missing"
    assert p.base_url == "http://localhost:3001/v1/chat/completions"
    assert p.default_model == "", "models come from the router's live /v1/models"
    assert p.models == [], "a catalogue copied in here would go stale and is not ours to ship"


def test_every_entry_points_somewhere_that_answers() -> None:
    """Base URLs end in a chat-completions path, not a bare host."""
    for p in list_providers():
        assert p.base_url.startswith(("http://", "https://")), p.id
        assert p.base_url.rstrip("/").endswith(("chat/completions", "/v1")), p.id
