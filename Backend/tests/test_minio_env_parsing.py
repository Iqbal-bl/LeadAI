"""A real production incident: MINIO_VERIFY_SSL=false was set in .env with a
trailing explanatory comment on the same line (the convention this whole
.env file uses). python-dotenv strips that inline comment, so testing with
it looked correct — but Docker Compose's `env_file:` directive does NOT
strip it, so the container's actual env var was the raw string
"false     # set true once MinIO has a certificate signed by a real CA".
core/storage.py's `_verify_setting()` did a strict `== "false"` check, which
never matched that string, so TLS verification silently stayed ON and every
call-recording archive to the self-signed-cert MinIO endpoint failed with
SSLCertVerificationError — confirmed via `docker exec ... printenv
MINIO_VERIFY_SSL` showing exactly that raw, uncommented value.

core/storage.py now has its own `_env()` that strips a trailing inline
comment before comparing, independent of whichever tool parsed the .env file.

Run: python tests/test_minio_env_parsing.py
"""
import os

from core import storage


def _with_env(**kv):
    """Set env vars for the duration of `fn()`, restoring whatever was there before."""
    def decorator(fn):
        def wrapper():
            saved = {k: os.environ.get(k) for k in kv}
            os.environ.update(kv)
            try:
                fn()
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
        return wrapper
    return decorator


@_with_env(MINIO_VERIFY_SSL="false     # set true once MinIO has a certificate signed by a real CA")
def test_verify_setting_is_false_even_with_dockers_unstripped_inline_comment():
    assert storage._verify_setting() is False


@_with_env(MINIO_VERIFY_SSL="true      # set true once MinIO has a certificate signed by a real CA")
def test_verify_setting_is_true_when_the_unstripped_value_says_true():
    assert storage._verify_setting() is True


def test_verify_setting_defaults_true_when_unset():
    saved = os.environ.pop("MINIO_VERIFY_SSL", None)
    try:
        assert storage._verify_setting() is True
    finally:
        if saved is not None:
            os.environ["MINIO_VERIFY_SSL"] = saved


@_with_env(
    MINIO_PUBLIC_ENDPOINT="https://minio.bharatlogicllp.com   # what a browser download link actually uses",
    MINIO_BUCKET_RECORDINGS="twiliorecordingsdata",
)
def test_public_url_has_no_leftover_comment_text_in_it():
    url = storage._public_url("recordings/2026/10/07/CAtest.mp3")
    assert url == "https://minio.bharatlogicllp.com/twiliorecordingsdata/recordings/2026/10/07/CAtest.mp3"
    assert "#" not in url and "browser" not in url


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
