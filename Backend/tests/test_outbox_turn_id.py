"""A long channel message id must not break the turn's event row.

Instagram message ids are ~170 characters; leadai_events.TurnId is 80. MySQL rejected the
insert ("Data too long for column 'TurnId'") and the whole customer turn rolled back.
Run: python tests/test_outbox_turn_id.py
"""
import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine

from LeadAI import models, models_ext  # noqa: E402,F401
from LeadAI.engine import outbox  # noqa: E402
from LeadAI.engine.events import TurnEvent  # noqa: E402

IG_ID = "aWdfZAG1faXRlbToxOklHTWVzc2FnZAUlEOjE3ODQxNDA1" + "x" * 130


def test_short_ids_are_stored_unchanged():
    assert outbox.column_turn_id("mid.123") == "mid.123" and outbox.column_turn_id(None) is None


def test_a_long_id_fits_the_column_and_is_stable():
    a = outbox.column_turn_id(IG_ID)
    assert len(a) <= outbox.MAX_TURN_ID_CHARS and a == outbox.column_turn_id(IG_ID)
    assert a != outbox.column_turn_id(IG_ID + "y")
    assert models_ext.LeadEvent.TurnId.type.length >= outbox.MAX_TURN_ID_CHARS


def test_emit_stores_the_shortened_id_and_keeps_the_full_one_in_the_payload():
    outbox.enabled = lambda: True
    Base.metadata.create_all(bind=engine, tables=[models_ext.LeadEvent.__table__])
    db = SessionLocalAdmin()
    ev = TurnEvent(type="turn.received", client_id="1", conversation_id="1", channel="instagram",
                   turn_id=IG_ID, speaker="customer", text="hi")
    assert outbox.emit(db, ev)
    db.commit()
    row = db.query(models_ext.LeadEvent).one()
    assert len(row.TurnId) <= 80 and row.PayloadJson["turn_id"] == IG_ID


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
