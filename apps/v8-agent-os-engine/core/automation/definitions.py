"""Definition identity/version helpers; Storage owns their persistence."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def definition_content(value):
    return {key: item for key, item in value.items() if key not in {"definitionRevision", "definitionUpdatedAt"}}


def version_definitions(items, previous):
    old = {str(item.get("id")): item for item in previous if isinstance(item, dict) and item.get("id")}
    result = []
    for value in items:
        if not isinstance(value, dict):
            result.append(value)
            continue
        value = deepcopy(value)
        if not value.get("id"):
            value["id"] = "definition-" + fingerprint(definition_content(value))[:24]
        prior = old.get(str(value["id"])) or {}
        same = definition_content(prior) == definition_content(value)
        value["definitionRevision"] = prior.get("definitionRevision") if same else None
        value["definitionRevision"] = value["definitionRevision"] or uuid4().hex
        value["definitionUpdatedAt"] = prior.get("definitionUpdatedAt") if same else None
        value["definitionUpdatedAt"] = value["definitionUpdatedAt"] or datetime.now(timezone.utc).isoformat()
        result.append(value)
    return result


def enabled(definition):
    return bool(definition.get("enabled")) and definition.get("status") not in {"paused", "deleted", "disabled"}
