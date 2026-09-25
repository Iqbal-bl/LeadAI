"""Every voice/batch route must return something, and the Twilio TwiML route must return XML.

Why this exists: a merge once pasted a helper into the middle of `outbound_twiml`, cutting
off its `return`. The handler then answered with an empty body and no Content-Type, and
Twilio rejected every call (error 12300, "an application error has occurred"). Nothing
failed loudly on our side, so it went unnoticed for weeks. This reads the source, so it
needs no database or network. Run: python tests/test_route_handlers_return.py
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
VOICE_FILES = ["outbound/app.py", "outbound/batching.py"]
ROUTE_METHODS = {"get", "post", "put", "delete", "patch", "api_route"}


def _handlers(path):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(isinstance(d, ast.Call) and getattr(d.func, "attr", "") in ROUTE_METHODS
                   for d in fn.decorator_list):
                yield fn


def test_every_http_route_in_the_voice_app_returns_a_value():
    missing = []
    for path in VOICE_FILES:
        for fn in _handlers(path):
            if not any(isinstance(n, ast.Return) and n.value is not None for n in ast.walk(fn)):
                missing.append(f"{path}:{fn.lineno} {fn.name}")
    assert not missing, f"route handlers that never return a value: {missing}"


def test_the_twilio_twiml_route_returns_xml():
    fn = next(f for f in _handlers("outbound/app.py") if f.name == "outbound_twiml")
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return) and n.value is not None]
    assert returns, "outbound_twiml has no return"
    assert any("application/xml" in ast.unparse(r.value) or "text/xml" in ast.unparse(r.value)
               for r in returns), "outbound_twiml must label its reply as XML for Twilio"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
