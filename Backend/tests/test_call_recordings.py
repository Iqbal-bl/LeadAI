"""Call recordings come back in the inbox call data as links that play.

Before: the call responses carried no recording at all (the inbox `calls[]`), or a path to a
route that did not exist (`/api/leadai/voice/recordings/{sid}`), so a player always got a 404.
Now each call carries a signed, time-limited link to its MinIO audio. Signing is local, so
this needs no network. Runs on in-memory SQLite. Run: python tests/test_call_recordings.py
"""
import types
import uuid

import conftest_stub  # noqa: F401  — installs core.base/core.database/core.auth stubs first
from conftest_stub import Base, SessionLocalAdmin, engine

from fastapi import HTTPException  # noqa: E402

from domain.models import Client, Recordings  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.config import settings as real_settings  # noqa: E402
from LeadAI.rbac import Principal  # noqa: E402
from LeadAI.routers import voice  # noqa: E402
from LeadAI.serializers import call_conversation_detail, conversation_detail  # noqa: E402
from LeadAI.services import recordings  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001 — known duplicate index name on the domain `batchinfo` table
        pass


class _Minio:
    """The real settings with MinIO configured (public and internal addresses differ, as in prod)."""
    minio_endpoint = "https://192.168.2.100:9000"
    minio_public_endpoint = "https://minio.example.com"
    minio_access_key = "test-access-key"
    minio_secret_key = "test-secret-key"
    minio_region = "us-east-1"
    minio_presign_seconds = 900
    minio_enabled = True

    def __getattr__(self, name):
        return getattr(real_settings, name)


recordings.settings = _Minio()
voice.settings = _Minio()

KEY = "recordings/2026/09/25/{sid}.mp3"
BUCKET = "twiliorecordingsdata"


def make(with_recording=True, url=None):
    db = SessionLocalAdmin()
    client = Client(Name="Kestrel Homes")
    db.add(client)
    db.flush()
    customer = models.LeadCustomer(ClientId=client.Id, PublicRef="Customer #1")
    db.add(customer)
    db.flush()
    conv = models.LeadConversation(ClientId=client.Id, CustomerId=customer.Id, Channel="voice")
    db.add(conv)
    db.flush()
    sid = "CA" + uuid.uuid4().hex[:30]
    call = models.LeadCall(ClientId=client.Id, ConversationId=conv.Id, CallSid=sid, Status="completed",
                           DurationSec=34)
    db.add(call)
    if with_recording:
        db.add(Recordings(CallSid=sid, RecordingUrl=url or f"https://minio.example.com/{BUCKET}/{KEY.format(sid=sid)}"))
    db.commit()
    return db, client, conv, call, sid


def principal(client, perms=("lead.read.all", "call.read")):
    return Principal(email="a@x.test", role="company_admin", client_id=client.Id, permissions=set(perms))


# --------------------------------------------------------------------------- #
def test_a_stored_minio_url_becomes_a_signed_expiring_link_on_the_public_address():
    url = recordings.playable_url(f"https://minio.example.com/{BUCKET}/recordings/2026/09/25/CA1.mp3")
    assert url.startswith(f"https://minio.example.com/{BUCKET}/recordings/2026/09/25/CA1.mp3?")
    assert "X-Amz-Signature=" in url and "X-Amz-Expires=900" in url


def test_a_url_stored_with_the_internal_address_is_signed_for_the_public_one():
    url = recordings.playable_url(f"https://192.168.2.100:9000/{BUCKET}/recordings/2026/09/25/CA1.mp3")
    assert url.startswith("https://minio.example.com/") and "X-Amz-Signature=" in url   # a browser can reach it


def test_other_urls_and_missing_values_pass_through_untouched():
    twilio = "https://api.twilio.com/2010-04-01/Accounts/AC1/Recordings/RE1"
    assert recordings.playable_url(twilio) == twilio
    assert recordings.playable_url(None) is None and recordings.playable_url("") is None
    saved = recordings.settings

    class _Off(_Minio):
        minio_enabled = False

    recordings.settings = _Off()
    try:
        stored = f"https://minio.example.com/{BUCKET}/recordings/x.mp3"
        assert recordings.playable_url(stored) == stored          # never returns less than what was stored
    finally:
        recordings.settings = saved


def test_the_inbox_conversation_detail_includes_each_calls_recording():
    db, client, conv, call, sid = make()
    detail = conversation_detail(db, conv, principal(client))
    assert len(detail.calls) == 1
    assert detail.calls[0].recording_url.startswith("https://minio.example.com/")
    assert sid in detail.calls[0].recording_url and "X-Amz-Signature=" in detail.calls[0].recording_url


def test_a_call_without_a_recording_gets_null_not_a_dead_link():
    db, client, conv, call, sid = make(with_recording=False)
    assert conversation_detail(db, conv, principal(client)).calls[0].recording_url is None
    assert call_conversation_detail(db, conv, principal(client)).calls[0].recording_url is None


def test_roles_that_may_not_read_calls_get_no_recording():
    db, client, conv, call, sid = make()
    assert conversation_detail(db, conv, principal(client, perms=("lead.read.all",))).calls[0].recording_url is None


def test_the_call_transcript_view_uses_the_real_link_too():
    db, client, conv, call, sid = make()
    out = call_conversation_detail(db, conv, principal(client)).calls[0]
    assert out.recording_url.startswith("https://minio.example.com/") and "/voice/recordings/" not in out.recording_url


def test_the_recording_endpoint_returns_a_fresh_link_and_its_lifetime():
    db, client, conv, call, sid = make()
    out = voice.get_recording(sid, principal(client), db)
    assert out.call_sid == sid and "X-Amz-Signature=" in out.url and out.expires_in_seconds == 900


def test_the_recording_endpoint_is_404_for_missing_recordings_and_other_companies():
    db, client, conv, call, sid = make()
    other_db, other_client, *_ = make()      # keep its session alive
    for who, target in [(principal(client), "CA-does-not-exist"), (principal(other_client), sid)]:
        try:
            voice.get_recording(target, who, db)
            raise AssertionError("expected a 404")
        except HTTPException as exc:
            assert exc.status_code == 404
    db2, client2, _, _, sid2 = make(with_recording=False)
    try:
        voice.get_recording(sid2, principal(client2), db2)
        raise AssertionError("expected a 404 for a call with no recording")
    except HTTPException as exc:
        assert exc.status_code == 404 and "no recording" in exc.detail.lower()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
