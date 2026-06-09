#!/usr/bin/env python3
"""
Generate a .env file from .env.example with a fresh CREDENTIAL_ENCRYPTION_KEY.

Usage (from repo root):
    python scripts/setup_env.py
"""
import base64
import os
import secrets
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
ENV_EXAMPLE = REPO_ROOT / ".env.example"
ENV_FILE = REPO_ROOT / ".env"


def main() -> None:
    if ENV_FILE.exists():
        print(f".env already exists at {ENV_FILE}")
        print("Delete it first if you want to regenerate.")
        sys.exit(0)

    if not ENV_EXAMPLE.exists():
        print(f"ERROR: .env.example not found at {ENV_EXAMPLE}")
        sys.exit(1)

    content = ENV_EXAMPLE.read_text()

    # Generate a fresh 32-byte encryption key
    key = base64.b64encode(secrets.token_bytes(32)).decode()
    content = content.replace("REPLACE_WITH_BASE64_32_BYTES", key)

    ENV_FILE.write_text(content)
    print(f"Created {ENV_FILE}")
    print()
    print("IMPORTANT: Edit .env and fill in:")
    print("  OPENAI_API_KEY=sk-...")
    print("  ANTHROPIC_API_KEY=sk-ant-...")
    print("  POSTGRES_PASSWORD=your-password")
    print("  NEO4J_PASSWORD=your-password")
    print()
    print("For local dev (non-Docker), also update:")
    print("  POSTGRES_HOST=localhost")
    print("  NEO4J_URI=bolt://localhost:7687")
    print("  MINIO_ENDPOINT=localhost:9000")
    print("  CELERY_BROKER_URL=redis://localhost:6379/0")


if __name__ == "__main__":
    main()
