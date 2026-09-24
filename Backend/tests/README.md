# tests/

Run from the `Backend/` folder.

- `test_tenancy.py` â€” checks one company cannot reach another's social accounts
  (runs against in-memory SQLite; `conftest_stub.py` stands in for MySQL and auth)
- `leadai_functional_test.py` â€” end-to-end LeadAI flow (chat, scoring, handoff, RBAC) on SQLite
- `phase_scripts/` â€” older ad-hoc scripts for the billing / batch phases

Both `test_tenancy.py` and `leadai_functional_test.py` need the `razorpay` package installed.
