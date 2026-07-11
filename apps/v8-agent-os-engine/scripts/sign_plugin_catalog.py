from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def canonical_catalog_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_private_key(args: argparse.Namespace) -> Ed25519PrivateKey:
    encoded = str(os.getenv("V8_PLUGIN_CATALOG_PRIVATE_KEY") or "").strip()
    if args.private_key_file:
        encoded = Path(args.private_key_file).expanduser().read_text(encoding="utf-8").strip()
    if not encoded:
        raise SystemExit("Set V8_PLUGIN_CATALOG_PRIVATE_KEY or pass --private-key-file.")
    raw = base64.b64decode(encoded)
    if len(raw) != 32:
        raise SystemExit("The Ed25519 private key must decode to exactly 32 bytes.")
    return Ed25519PrivateKey.from_private_bytes(raw)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sign the V8OS plugin catalog with an Ed25519 release key.")
    parser.add_argument("catalog", type=Path)
    parser.add_argument("signature", type=Path)
    parser.add_argument("--private-key-file")
    args = parser.parse_args()

    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    signature = load_private_key(args).sign(canonical_catalog_bytes(payload))
    args.signature.parent.mkdir(parents=True, exist_ok=True)
    args.signature.write_text(base64.b64encode(signature).decode("ascii") + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
