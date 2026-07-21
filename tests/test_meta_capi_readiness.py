"""
Tests for analytics.meta_capi readiness + backfill.

These cover the failure that made CAPI a silent no-op in production: the mapping
could only ever be populated by the live webhook, and the access token lacked the
`leads_retrieval` permission the webhook's own lead fetch depends on — so nothing
was ever sent and nothing ever said so.
"""

import json

import pytest

from pikorua_adflow.analytics import meta_capi


@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    """Point the module's on-disk state at a temp dir."""
    monkeypatch.setattr(meta_capi, "_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(meta_capi, "_MAPPING_PATH", tmp_path / "leadgen_mapping.json")
    monkeypatch.setattr(meta_capi, "_SENT_PATH", tmp_path / "capi_sent.json")
    return tmp_path


def _write_mapping(tmp_state, mapping):
    (tmp_state / "leadgen_mapping.json").write_text(json.dumps(mapping), encoding="utf-8")


# ── check_readiness ───────────────────────────────────────────────────────────

def test_missing_creds_is_a_blocker(tmp_state, monkeypatch):
    monkeypatch.delenv("META_CAPI_TOKEN", raising=False)
    monkeypatch.delenv("META_CAPI_DATASET_ID", raising=False)
    monkeypatch.setenv("META_ACCESS_TOKEN", "tok")
    monkeypatch.setattr(meta_capi, "_token_scopes", lambda t: ["leads_retrieval"])

    result = meta_capi.check_readiness()

    assert result["ready"] is False
    assert any("META_CAPI_TOKEN" in b for b in result["blockers"])


def test_missing_leads_retrieval_scope_is_a_blocker(tmp_state, monkeypatch):
    """The exact production failure: token works for ads but not for reading leads."""
    monkeypatch.setenv("META_CAPI_TOKEN", "t")
    monkeypatch.setenv("META_CAPI_DATASET_ID", "d")
    monkeypatch.setenv("META_ACCESS_TOKEN", "tok")
    monkeypatch.setattr(meta_capi, "_token_scopes",
                        lambda t: ["ads_management", "ads_read", "pages_show_list"])
    _write_mapping(tmp_state, {"ph:abc": {"leadgen_id": "999"}})

    result = meta_capi.check_readiness()

    assert result["ready"] is False
    assert any("leads_retrieval" in b for b in result["blockers"])


def test_test_only_mapping_counts_as_empty(tmp_state, monkeypatch):
    """A mapping holding only test_* rows cannot match a real lead."""
    monkeypatch.setenv("META_CAPI_TOKEN", "t")
    monkeypatch.setenv("META_CAPI_DATASET_ID", "d")
    monkeypatch.setenv("META_ACCESS_TOKEN", "tok")
    monkeypatch.setattr(meta_capi, "_token_scopes", lambda t: ["leads_retrieval"])
    _write_mapping(tmp_state, {"ph:abc": {"leadgen_id": "test_123"}})

    result = meta_capi.check_readiness()

    assert result["ready"] is False
    assert result["real_entries"] == 0
    assert any("no real leads" in b for b in result["blockers"])


def test_fully_configured_is_ready(tmp_state, monkeypatch):
    monkeypatch.setenv("META_CAPI_TOKEN", "t")
    monkeypatch.setenv("META_CAPI_DATASET_ID", "d")
    monkeypatch.setenv("META_ACCESS_TOKEN", "tok")
    monkeypatch.setattr(meta_capi, "_token_scopes", lambda t: ["leads_retrieval"])
    _write_mapping(tmp_state, {"ph:abc": {"leadgen_id": "17771"}})

    result = meta_capi.check_readiness()

    assert result["ready"] is True
    assert result["blockers"] == []
    assert result["real_entries"] == 1


# ── backfill_mapping_from_forms ───────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_backfill_maps_phone_and_email(tmp_state, monkeypatch):
    """One page of leads becomes hashed phone+email keys pointing at the leadgen id."""
    monkeypatch.setenv("META_ACCESS_TOKEN", "tok")
    page = {
        "data": [
            {
                "id": "5551",
                "created_time": "2026-07-01T10:00:00+0000",
                "field_data": [
                    {"name": "phone_number", "values": ["+919876543210"]},
                    {"name": "email", "values": ["Buyer@Example.com"]},
                ],
            }
        ],
        "paging": {},
    }
    monkeypatch.setattr(meta_capi.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(page))

    result = meta_capi.backfill_mapping_from_forms(["form1"])

    assert result["added"] == 2  # one phone key + one email key
    mapping = json.loads((tmp_state / "leadgen_mapping.json").read_text())
    expected_phone = f"ph:{meta_capi._sha256('9876543210')}"
    expected_email = f"em:{meta_capi._sha256('buyer@example.com')}"
    assert mapping[expected_phone]["leadgen_id"] == "5551"
    assert mapping[expected_email]["leadgen_id"] == "5551"


def test_backfill_is_idempotent(tmp_state, monkeypatch):
    """Re-running must not duplicate or clobber — it merges."""
    monkeypatch.setenv("META_ACCESS_TOKEN", "tok")
    page = {
        "data": [{"id": "5551", "created_time": "",
                  "field_data": [{"name": "phone_number", "values": ["9876543210"]}]}],
        "paging": {},
    }
    monkeypatch.setattr(meta_capi.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(page))

    meta_capi.backfill_mapping_from_forms(["form1"])
    second = meta_capi.backfill_mapping_from_forms(["form1"])

    assert second["added"] == 0
    assert second["total_entries"] == 1


def test_backfill_reports_permission_error_per_form(tmp_state, monkeypatch):
    """A 403 for one form is reported, not raised — other forms still run."""
    import urllib.error

    monkeypatch.setenv("META_ACCESS_TOKEN", "tok")

    def _raise(*a, **k):
        raise urllib.error.HTTPError(
            "url", 403, "Forbidden", {},
            __import__("io").BytesIO(
                b'{"error":{"message":"(#200) Requires leads_retrieval permission"}}'
            ),
        )

    monkeypatch.setattr(meta_capi.urllib.request, "urlopen", _raise)

    result = meta_capi.backfill_mapping_from_forms(["form1"])

    assert result["added"] == 0
    assert "leads_retrieval" in result["per_form"]["form1"]


def test_backfill_skips_leads_with_no_contact_fields(tmp_state, monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "tok")
    page = {
        "data": [{"id": "5551", "created_time": "",
                  "field_data": [{"name": "full_name", "values": ["A Buyer"]}]}],
        "paging": {},
    }
    monkeypatch.setattr(meta_capi.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(page))

    result = meta_capi.backfill_mapping_from_forms(["form1"])

    assert result["added"] == 0


# ── handle_status_update (real-time CRM trigger) ──────────────────────────────

def _fake_send(ok=True):
    def _sender(leadgen_id, phone="", email=""):
        return {"ok": ok, "leadgen_id": leadgen_id}
    return _sender


def test_status_update_requires_phone_or_email(tmp_state):
    result = meta_capi.handle_status_update(client_status="warm")
    assert result["ok"] is False
    assert result["sent"] is False


def test_status_update_unclassified_sends_nothing(tmp_state):
    result = meta_capi.handle_status_update(phone="9876543210", client_status="new")
    assert result["sent"] is False
    assert result["category"] == "unclassified"


def test_status_update_no_mapping_is_a_clean_no_op(tmp_state):
    result = meta_capi.handle_status_update(phone="9876543210", client_status="warm")
    assert result["ok"] is True
    assert result["sent"] is False
    assert "leadgen_id" not in result or result.get("leadgen_id") is None
    assert "no Meta leadgen_id mapped" in result["reason"]


def test_status_update_sends_qualified_when_mapped(tmp_state, monkeypatch):
    _write_mapping(tmp_state, {f"ph:{meta_capi._sha256('9876543210')}":
                                {"leadgen_id": "5551"}})
    monkeypatch.setattr(meta_capi, "send_qualified_lead_event", _fake_send(True))

    result = meta_capi.handle_status_update(phone="9876543210", client_status="warm")

    assert result["ok"] is True
    assert result["sent"] is True
    assert result["category"] == "good"
    assert result["leadgen_id"] == "5551"


def test_status_update_sends_disqualified_for_bad_status(tmp_state, monkeypatch):
    _write_mapping(tmp_state, {f"ph:{meta_capi._sha256('9876543210')}":
                                {"leadgen_id": "5551"}})
    monkeypatch.setattr(meta_capi, "send_disqualified_lead_event", _fake_send(True))

    result = meta_capi.handle_status_update(phone="9876543210", client_status="not interested")

    assert result["sent"] is True
    assert result["category"] == "bad"


def test_status_update_is_idempotent_per_direction(tmp_state, monkeypatch):
    _write_mapping(tmp_state, {f"ph:{meta_capi._sha256('9876543210')}":
                                {"leadgen_id": "5551"}})
    monkeypatch.setattr(meta_capi, "send_qualified_lead_event", _fake_send(True))

    first = meta_capi.handle_status_update(phone="9876543210", client_status="warm")
    second = meta_capi.handle_status_update(phone="9876543210", client_status="warm")

    assert first["sent"] is True
    assert second["sent"] is False
    assert "already sent" in second["reason"]


def test_status_update_shares_sent_state_with_daily_pass(tmp_state, monkeypatch):
    """A lead the daily batch already sent must not be re-sent by the real-time path."""
    _write_mapping(tmp_state, {f"ph:{meta_capi._sha256('9876543210')}":
                                {"leadgen_id": "5551"}})
    meta_capi._save_sent({"qualified": {"5551"}, "disqualified": set()})

    result = meta_capi.handle_status_update(phone="9876543210", client_status="warm")

    assert result["sent"] is False
    assert "already sent" in result["reason"]
