"""Immutable read observations, not a machine-selected list of permissible claims."""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from core.tools.research_source_identity import canonical_source_url


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_citation_tokens(value: Any) -> str:
    """Normalize explicit source-marker typography, never source identity or prose."""
    text = str(value or "").strip()
    text = re.sub(r"(?:\[|【)\s*(S\d+)\s*:\s*E\d+\s*(?:\]|】)",
                  lambda match: f"[{match.group(1).upper()}]", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:\[|【)\s*(S\d+(?:\s*[,，;；、/]\s*S\d+)*)\s*(?:\]|】)",
                  lambda match: "".join(f"[{key.upper()}]" for key in re.findall(r"S\d+", match.group(1), re.IGNORECASE)),
                  text, flags=re.IGNORECASE)

    def labeled(match: re.Match[str]) -> str:
        body = re.sub(r"(?<![A-Za-z0-9_])(S\d+)(?![A-Za-z0-9_])", lambda key: f"[{key.group(1).upper()}]",
                      match.group(2), flags=re.IGNORECASE)
        return f"（{match.group(1)}：{body}）"

    return re.sub(r"[\[【]\s*(来源|sources?)\s*[:：]\s*([^\[\]【】]{1,1500})[\]】]",
                  labeled, text, flags=re.IGNORECASE)


class EvidenceReferenceError(ValueError):
    def __init__(self, code: str, **details: Any):
        super().__init__(code)
        self.details = details


class EvidenceStore:
    def __init__(self, *, max_sources: int = 64):
        self.sources: dict[str, dict[str, Any]] = {}
        self.identities: dict[tuple[str, str], str] = {}
        self.read_refs: dict[str, tuple[str, int, int]] = {}
        self.max_sources = max_sources

    def add(self, observations: list[dict[str, Any]]) -> list[str]:
        added: list[str] = []
        for value in observations:
            body = str(value.get("text") or value.get("markdown") or "").strip()
            url = canonical_source_url(value.get("finalUrl") or value.get("url"))
            if value.get("ok") is not True or not body or not url:
                continue
            if urlsplit(url).scheme not in {"http", "https"}:
                continue
            identity = (url, digest(body))
            if identity in self.identities or len(self.sources) >= self.max_sources:
                continue
            key = f"S{len(self.sources) + 1}"
            retrieved = str(value.get("retrievedAt") or datetime.now(timezone.utc).isoformat())
            row = {
                "sourceId": str(value.get("sourceId") or f"src_{digest(url)[:16]}"),
                "citationKey": key,
                "url": url,
                "title": str(value.get("title") or url),
                "host": urlsplit(url).hostname or "",
                "text": body,
                "contentChars": len(body),
                "originalContentChars": value.get("originalContentChars") or len(body),
                "omittedChars": value.get("omittedChars") or 0,
                "retrievedAt": retrieved,
                "publishedAt": value.get("publishedAt"),
                "updatedAt": value.get("updatedAt"),
                "version": value.get("version"),
                "sourceRole": value.get("sourceRole") or "unknown",
                "sourceKind": value.get("sourceKind") or "document",
                "acquisitionState": value.get("acquisitionState") or "captured",
                "links": [dict(link) for link in value.get("links") or []
                          if isinstance(link, dict) and urlsplit(str(link.get("url") or "")).scheme in {"http", "https"}],
                "selectedForEvidence": True,
                "readEvidence": {
                    "verified": True, "contentChars": len(body),
                    "contentSha256": identity[1], "retrievedAt": retrieved,
                },
            }
            self.sources[key] = row
            self.identities[identity] = key
            added.append(key)
        return added

    def index(self) -> list[dict[str, Any]]:
        return [
            {key: deepcopy(row[key]) for key in (
                "citationKey", "title", "url", "contentChars", "omittedChars",
                "retrievedAt", "publishedAt", "updatedAt", "version", "sourceRole", "sourceKind", "acquisitionState",
            )}
            for row in self.sources.values()
        ]

    def restore(self, snapshots: list[dict[str, Any]]) -> None:
        """Restore saved observations only when their original read hashes agree."""
        for row in snapshots:
            receipt = row.get("readEvidence") or {}
            body = str(row.get("text") or "").strip()
            if not body or not receipt.get("verified") or digest(body) != receipt.get("contentSha256"):
                raise ValueError("research_saved_source_digest_mismatch")
            keys = self.add([{**row, "ok": True}])
            if keys != [row.get("citationKey")]:
                raise ValueError("research_saved_source_identity_mismatch")

    def read(self, key: str, *, start: int = 0, max_chars: int = 6000, find: str = "", link_start: int = 0) -> dict[str, Any]:
        row = self.sources.get(key)
        if row is None:
            raise ValueError("unknown_source_key")
        body = row["text"]
        if find:
            match = body.casefold().find(find.casefold(), start)
            if match < 0:
                return {"citationKey": key, "found": False, "contentChars": len(body)}
            start = max(0, match - min(500, max_chars // 4))
        if start >= len(body):
            raise ValueError("source_offset_out_of_range")
        end = min(len(body), start + max_chars)
        reference = f"{key}:R{len(self.read_refs) + 1}"
        self.read_refs[reference] = (key, start, end)
        return {
            "citationKey": key, "url": row["url"], "title": row["title"],
            "contentSha256": row["readEvidence"]["contentSha256"],
            "start": start, "end": end, "contentChars": len(body),
            "nextOffset": end if end < len(body) else None,
            "evidenceRef": reference,
            "text": body[start:end],
            "links": deepcopy(row["links"][link_start:link_start + 30]),
            "nextLinkStart": link_start + 30 if link_start + 30 < len(row["links"]) else None,
            "linkCount": len(row["links"]),
            "linksAreReadEvidence": False,
        }

    def bind_read_refs(self, references: list[str], answer: str) -> list[dict[str, Any]]:
        used = set(re.findall(r"\[(S\d+)\]", answer))
        if not used or used.difference(self.sources):
            raise EvidenceReferenceError("answer_requires_known_source_citations",
                                         unknownSourceKeys=sorted(used.difference(self.sources)),
                                         nextAction="Use citation keys from the source index; keep supported text and correct only the bad references.")
        rows: list[dict[str, Any]] = []
        covered: set[str] = set()
        for reference in dict.fromkeys(references):
            observation = self.read_refs.get(reference)
            if observation is None:
                raise ValueError("unknown_evidence_ref_use_the_ref_returned_by_read_research_source")
            key, start, end = observation
            if key not in used:
                raise ValueError("evidence_ref_must_support_a_cited_source")
            source = self.sources[key]
            excerpt = source["text"][start:end]
            covered.add(key)
            rows.append({
                "claimId": f"read_{reference}", "claimType": "source_excerpt",
                "claim": f"Read observation [{key}] ({start}:{end})",
                "supportingSources": [{
                    field: source[field] for field in ("sourceId", "citationKey", "url", "title", "sourceRole", "sourceKind", "acquisitionState")
                }],
                "evidenceExcerptKey": reference, "evidenceExcerpt": excerpt,
                "evidenceExcerptSha256": digest(normalized(excerpt).lower()),
                "evidenceVerified": True, "verificationKind": "read_snapshot_ref_only",
                "sourceContentSha256": source["readEvidence"]["contentSha256"],
            })
        if covered != used:
            raise EvidenceReferenceError("every_answer_citation_needs_observed_evidence",
                                         unreadSourceKeys=sorted(used - covered),
                                         nextAction="Read only these source keys, then resubmit the same sectionIds without rewriting saved text. If a source does not support the claim, correct only that section.")
        return rows

    def citation_gaps(self, answer: str) -> dict[str, list[str]]:
        cited = set(re.findall(r"\[(S\d+)\]", answer))
        observed = {key for key, _, _ in self.read_refs.values()}
        return {"unknownSourceKeys": sorted(cited.difference(self.sources)),
                "unreadSourceKeys": sorted(cited.intersection(self.sources) - observed)}

    def bind_answer(self, answer: str) -> list[dict[str, Any]]:
        used = set(re.findall(r"\[(S\d+)\]", answer))
        refs = [ref for ref, (key, _, _) in self.read_refs.items() if key in used]
        return self.bind_read_refs(refs, answer)

    def review_passages(self, answer: str, *, max_chars: int = 18000) -> list[dict[str, Any]]:
        rows = []
        for claim in self.bind_answer(answer):
            key = claim["supportingSources"][0]["citationKey"]
            _, start, end = self.read_refs[claim["evidenceExcerptKey"]]
            body = self.sources[key]["text"][start:end]
            shown = body[:max_chars]
            rows.append({"sourceKey": key, "start": start, "end": start + len(shown),
                         "text": shown, "omittedChars": len(body) - len(shown),
                         "nextOffset": start + len(shown) if len(shown) < len(body) else None})
            max_chars -= len(shown)
            if max_chars <= 0:
                break
        return rows


    def selected(self, claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
        keys = {support["citationKey"] for claim in claims for support in claim["supportingSources"]}
        return [{field: deepcopy(value) for field, value in row.items() if field not in {"text", "links"}}
                for key, row in self.sources.items() if key in keys]
