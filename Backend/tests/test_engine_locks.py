"""Per-conversation lock: order of operations, fail-open, and always released.

A real two-worker race needs MySQL, so this fakes the connection and records what the
lock sends: GET_LOCK, then a commit (fresh read snapshot), then the body, then
RELEASE_LOCK on the same dedicated connection. Run: python tests/test_engine_locks.py
"""
import conftest_stub  # noqa: F401

from LeadAI.engine import locks


class _Conn:
    def __init__(self, log, get_lock_result=1, fail_get=False):
        self.log, self.result, self.fail_get, self.closed = log, get_lock_result, fail_get, False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.log.append(("conn", "GET_LOCK" if "GET_LOCK" in sql else "RELEASE_LOCK", params))
        if "GET_LOCK" in sql:
            if self.fail_get:
                raise RuntimeError("pool exhausted")
            return _Result(self.result)
        return _Result(1)

    def close(self):
        self.closed = True
        self.log.append(("conn", "close", None))


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class _Bind:
    def __init__(self, log, dialect="mysql", **kw):
        self.dialect = type("D", (), {"name": dialect})()
        self.log, self.kw, self.conn = log, kw, None

    def connect(self):
        self.conn = _Conn(self.log, **self.kw)
        return self.conn


class _Db:
    def __init__(self, log, dialect="mysql", **kw):
        self.log, self.bind = log, _Bind(log, dialect, **kw)

    def get_bind(self):
        return self.bind

    def commit(self):
        self.log.append(("db", "commit", None))


class _On:
    conversation_lock = True
    conversation_lock_timeout = 7


def _with_settings(fn):
    saved = locks.settings
    locks.settings = _On()
    try:
        return fn()
    finally:
        locks.settings = saved


class _Off:
    conversation_lock = False


def test_when_switched_off_it_does_nothing():
    # Forced off so the test does not depend on the developer's .env (LEADAI_CONVERSATION_LOCK).
    saved = locks.settings
    locks.settings = _Off()
    try:
        log = []
        with locks.conversation_lock(_Db(log), "c1") as locked:
            assert locked is False
        assert log == []
    finally:
        locks.settings = saved


def test_locks_commits_runs_then_releases_in_order():
    log = []

    def run():
        db = _Db(log)
        with locks.conversation_lock(db, "c1") as locked:
            assert locked is True
            log.append(("body", "run", None))
        return db

    db = _with_settings(run)
    assert [(who, what) for who, what, _ in log] == [
        ("conn", "GET_LOCK"), ("db", "commit"), ("body", "run"), ("conn", "RELEASE_LOCK"), ("conn", "close"),
    ]
    assert log[0][2] == {"n": "leadai:conv:c1", "t": 7}
    assert db.bind.conn.closed


def test_released_even_when_the_turn_raises():
    log = []

    def run():
        try:
            with locks.conversation_lock(_Db(log), "c1"):
                raise ValueError("turn failed")
        except ValueError:
            pass

    _with_settings(run)
    assert ("conn", "RELEASE_LOCK") in [(w, x) for w, x, _ in log] and log[-1][1] == "close"


def test_timeout_runs_unlocked_and_never_blocks_the_customer():
    log = []

    def run():
        with locks.conversation_lock(_Db(log, get_lock_result=0), "c1") as locked:
            assert locked is False
            log.append(("body", "run", None))

    _with_settings(run)
    names = [(w, x) for w, x, _ in log]
    # The wait timed out: no commit, nothing to release, the lock connection is closed
    # before the turn runs, and the turn still runs.
    assert names == [("conn", "GET_LOCK"), ("conn", "close"), ("body", "run")]


def test_connection_failure_runs_unlocked():
    def run():
        with locks.conversation_lock(_Db([], fail_get=True), "c1") as locked:
            assert locked is False

    _with_settings(run)


def test_non_mysql_databases_skip_the_lock():
    log = []

    def run():
        with locks.conversation_lock(_Db(log, dialect="sqlite"), "c1") as locked:
            assert locked is False

    _with_settings(run)
    assert log == []


def test_lock_name_fits_mysqls_64_character_limit():
    assert len(locks._name("x" * 200)) == 64
    assert locks._name("abc") == "leadai:conv:abc"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
