"""
Checks what your OpenAI API key can actually do — which models it can
see, whether it can run chat completions, and whether it can run
embeddings. Doesn't touch any other part of this project.

    python check_openai_key.py
"""

import os

from dotenv import load_dotenv
from openai import OpenAI, AuthenticationError, PermissionDeniedError, NotFoundError

load_dotenv()

# Models this project actually uses/considered — checked individually so
# you know exactly which ones work, not just "something works."
CHAT_MODELS_TO_TEST = ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"]
EMBEDDING_MODELS_TO_TEST = ["text-embedding-3-small", "text-embedding-3-large"]


def main():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY is not set in .env — nothing to check.")
        return

    client = OpenAI(api_key=api_key)

    print("=" * 60)
    print("1. Listing all models this key can see (GET /v1/models)")
    print("=" * 60)
    try:
        models = client.models.list()
        model_ids = sorted(m.id for m in models.data)
        print(f"Key can see {len(model_ids)} models.")
        relevant = [m for m in model_ids if "gpt" in m or "embedding" in m]
        print("Relevant ones (gpt*/embedding*):")
        for m in relevant:
            print(f"  - {m}")
    except AuthenticationError as e:
        print(f"AUTH FAILED — key itself is invalid/revoked: {e}")
        return
    except Exception as e:
        print(f"Could not list models: {e}")

    print()
    print("=" * 60)
    print("2. Testing chat completion access (model.request scope)")
    print("=" * 60)
    for model in CHAT_MODELS_TO_TEST:
        try:
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Say OK"}],
                max_tokens=5,
            )
            print(f"  [OK]      {model} — chat completions work")
        except PermissionDeniedError as e:
            print(f"  [BLOCKED] {model} — permission denied: {e}")
        except NotFoundError as e:
            print(f"  [MISSING] {model} — not available to this project: {e}")
        except Exception as e:
            print(f"  [ERROR]   {model} — {e}")

    print()
    print("=" * 60)
    print("3. Testing embedding access")
    print("=" * 60)
    for model in EMBEDDING_MODELS_TO_TEST:
        try:
            client.embeddings.create(model=model, input="test")
            print(f"  [OK]      {model} — embeddings work")
        except PermissionDeniedError as e:
            print(f"  [BLOCKED] {model} — permission denied: {e}")
        except NotFoundError as e:
            print(f"  [MISSING] {model} — not available to this project: {e}")
        except Exception as e:
            print(f"  [ERROR]   {model} — {e}")

    print()
    print("Done. [BLOCKED] = key/project lacks that permission or model access.")
    print("[MISSING] = model doesn't exist or isn't enabled for this project.")


if __name__ == "__main__":
    main()