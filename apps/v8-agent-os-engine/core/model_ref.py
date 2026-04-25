from __future__ import annotations

from typing import Optional, Tuple
from urllib.parse import quote, unquote


MODEL_REF_SEPARATOR = "::"


def make_model_ref(provider_id: str, model_id: str) -> str:
    provider = str(provider_id or "").strip()
    model = str(model_id or "").strip()
    if not provider or not model:
        return ""
    return f"{provider}{MODEL_REF_SEPARATOR}{quote(model, safe='')}"


def parse_model_ref(value: str) -> Optional[Tuple[str, str]]:
    raw = str(value or "").strip()
    if MODEL_REF_SEPARATOR not in raw:
        return None
    provider_id, encoded_model_id = raw.split(MODEL_REF_SEPARATOR, 1)
    provider_id = provider_id.strip()
    model_id = unquote(encoded_model_id.strip())
    if not provider_id or not model_id:
        return None
    return provider_id, model_id


def is_model_ref(value: str) -> bool:
    return parse_model_ref(value) is not None
