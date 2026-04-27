from __future__ import annotations

import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable

from core.storage import storage

from .catalog import (
    load_audio_music_recipe_library,
    load_video_recipe_library,
    load_visual_recipe_library,
)


RECIPE_STORE_FILE = "creative_media/recipes.json"
ASSET_LEDGER_FILE = "creative_media/asset_ledger.json"
CHARACTER_BIBLE_STORE_FILE = "creative_media/character_bibles.json"
KEYFRAME_STORE_FILE = "creative_media/keyframes.json"
SUPPORTED_RECIPE_MODALITIES = {"image", "video", "voice", "music"}
SUPPORTED_MUSIC_KINDS = {"cue_sheet", "score_brief", "music_reference", "future_generation"}
PROTECTED_REFERENCE_REWRITES = [
    {
        "kind": "copyright_character",
        "risk": "medium",
        "names": ["钢铁侠", "iron man", "ironman", "tony stark", "托尼史塔克", "托尼·史塔克"],
        "replacement": (
            "an original hyper-realistic cinematic high-tech powered exoskeleton suit for an adult male silhouette, "
            "matte deep red armor with bright gold mechanical highlights, streamlined aerodynamic plating, fine brushed metal texture, "
            "a cool blue-white circular energy core embedded in the chest, a fully enclosed helmet with a liftable gold faceplate, "
            "narrow white glowing rectangular eye slits, segmented modular shoulder, arm, and leg armor, exposed hydraulic joints and servo motors, "
            "round palm-mounted directional thrusters, vector flight engines under both feet, subtle tactical indicator lights and sensor arrays, "
            "dynamic combat pose, dusk city skyline, backlight, volumetric lighting, natural metallic reflections, sharp subject and soft background"
        ),
    },
    {
        "kind": "copyright_character",
        "risk": "medium",
        "names": ["蜘蛛侠", "spider-man", "spiderman", "彼得帕克", "彼得·帕克"],
        "replacement": (
            "an original agile urban acrobat hero in a red and midnight-blue technical fabric suit, abstract web-like seam geometry, "
            "large expressive white eye lenses, dynamic wall-running pose, no logos, no named franchise symbols"
        ),
    },
    {
        "kind": "copyright_character",
        "risk": "medium",
        "names": ["蝙蝠侠", "batman", "bruce wayne", "布鲁斯韦恩", "布鲁斯·韦恩"],
        "replacement": (
            "an original nocturnal armored vigilante silhouette with a matte black tactical suit, angular protective cowl, cape-like glider fabric, "
            "industrial city rooftop at night, no bat emblem, no franchise insignia"
        ),
    },
    {
        "kind": "brand_logo",
        "risk": "low",
        "names": ["迪士尼", "disney", "marvel", "漫威", "dc comics"],
        "replacement": "an original entertainment-inspired cinematic style without any brand logo, franchise name, or protected character identity",
    },
]

ENGLISH_KEYWORD_MAP = [
    ("超写实", "hyper-realistic"),
    ("电影级", "cinematic"),
    ("高清", "high-definition"),
    ("海报", "poster design"),
    ("电商", "e-commerce product visual"),
    ("产品", "product"),
    ("耳机", "headphones"),
    ("兔子", "rabbit"),
    ("玻璃", "glass"),
    ("金属", "metallic"),
    ("蓝色", "blue"),
    ("红色", "red"),
    ("金色", "gold"),
    ("白色", "white"),
    ("黑色", "black"),
    ("未来", "futuristic"),
    ("城市", "city"),
    ("黄昏", "dusk"),
    ("逆光", "backlit"),
    ("体积光", "volumetric lighting"),
    ("旋转", "rotating"),
    ("推进", "slow camera push-in"),
    ("运镜", "camera movement"),
    ("分镜", "storyboard"),
    ("角色", "character"),
    ("一致性", "consistency"),
    ("字幕", "subtitles"),
    ("旁白", "voiceover"),
    ("温柔", "gentle"),
    ("轻快", "upbeat"),
    ("背景音乐", "background music"),
    ("音乐", "music"),
    ("口型", "lip synchronization"),
    ("动作迁移", "action transfer"),
    ("数字人", "digital avatar"),
    ("换人", "character replacement"),
    ("局部编辑", "localized edit"),
    ("保留", "preserve"),
    ("替换", "replace"),
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _list_of_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = [item.strip() for item in re.split(r"[,，;\n]", value)]
    else:
        raw = [_clean_str(item) for item in list(value or [])]
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _scope_fields(request: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, str]:
    previous = previous or {}
    return {
        "projectId": _clean_str(request.get("projectId") or request.get("project_id") or previous.get("projectId")),
        "workspaceId": _clean_str(request.get("workspaceId") or request.get("workspace_id") or previous.get("workspaceId")),
        "workspacePath": _clean_str(request.get("workspacePath") or request.get("workspace_path") or previous.get("workspacePath")),
    }


def _extract_quoted_text(prompt: str) -> list[str]:
    patterns = [
        r"「([^」]{1,80})」",
        r"『([^』]{1,80})』",
        r"“([^”]{1,80})”",
        r'"([^"]{1,80})"',
        r"'([^']{1,80})'",
    ]
    result: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for match in re.findall(pattern, prompt):
            value = _clean_str(match)
            if value and value not in seen:
                seen.add(value)
                result.append(value)
    return result


def _contains_cjk(value: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", value or ""))


def _remove_preserved_text(value: str, preserved: list[str]) -> str:
    result = value
    for token in preserved:
        if token:
            result = result.replace(token, "")
    return result


def _apply_safety_transform(prompt: str) -> dict[str, Any]:
    sanitized = prompt
    matches: list[dict[str, Any]] = []
    lowered = sanitized.lower()
    for rule in PROTECTED_REFERENCE_REWRITES:
        matched_names = [name for name in rule["names"] if name.lower() in lowered]
        if not matched_names:
            continue
        for name in matched_names:
            sanitized = re.sub(re.escape(name), rule["replacement"], sanitized, flags=re.IGNORECASE)
        lowered = sanitized.lower()
        matches.append(
            {
                "kind": rule["kind"],
                "risk": rule["risk"],
                "matched": matched_names,
                "replacementSummary": rule["replacement"][:240],
                "action": "descriptive_substitute",
            }
        )
    identity_patterns = [
        (r"(克隆|模仿|复刻).{0,12}(声音|嗓音|声线)", "protected_voice_request"),
        (r"(换脸|复刻真人|真人肖像|明星脸)", "sensitive_identity_request"),
    ]
    for pattern, kind in identity_patterns:
        if re.search(pattern, prompt, flags=re.IGNORECASE):
            matches.append(
                {
                    "kind": kind,
                    "risk": "high",
                    "matched": [kind],
                    "replacementSummary": "Use an original fictional presenter or anonymized consented identity instead of a real person or protected voice.",
                    "action": "human_review_gate_if_user_requires_identity_replication",
                }
            )
            sanitized = re.sub(pattern, "an original fictional presenter with consent-safe identity", sanitized, flags=re.IGNORECASE)
    return {
        "applied": bool(matches),
        "rawPrompt": prompt,
        "sanitizedPrompt": sanitized,
        "events": matches,
        "policy": "copyright_ip_avoidance_by_default",
    }


def _english_prompt_from_keywords(prompt: str, *, modality: str, preserved_tokens: list[str]) -> str:
    working = _remove_preserved_text(prompt, preserved_tokens)
    if not _contains_cjk(working):
        return working.strip() or prompt.strip()
    ascii_phrases = [
        item.strip(" ,.;:，。；：")
        for item in re.findall(r"[A-Za-z][A-Za-z0-9 ,;:'\"()\-]{24,}", working)
        if item.strip()
    ]
    features: list[str] = []
    for chinese, english in ENGLISH_KEYWORD_MAP:
        if chinese in working and english not in features:
            features.append(english)
    seconds = re.findall(r"(\d{1,3})\s*秒", working)
    duration = f"{seconds[0]} seconds, " if seconds else ""
    if modality == "video":
        base = f"{duration}a clean single-focus cinematic video shot"
        if features:
            base += " featuring " + ", ".join(features)
        base += ", coherent subject identity, stable camera motion, avoid overloading too many actions in one clip"
    elif modality == "music":
        base = f"{duration}a structured music cue brief"
        if features:
            base += " with " + ", ".join(features)
        base += ", clear mood, tempo, structure, and licensing-safe original composition intent"
    elif modality == "voice":
        base = "spoken script with clear delivery, natural pacing, and subtitle-friendly phrasing"
        if features:
            base += ", " + ", ".join(features)
    else:
        base = "a high-quality visual generation prompt"
        if features:
            base += " featuring " + ", ".join(features)
        base += ", detailed composition, lighting, material, and style constraints"
    if ascii_phrases:
        base += ". Descriptive substitute details: " + "; ".join(ascii_phrases[:3])
    return base


def prepare_provider_prompt_policy(prompt: str, *, modality: str, preserved_tokens: list[str] | None = None) -> dict[str, Any]:
    preserved = list(preserved_tokens or _extract_quoted_text(prompt))
    safety = _apply_safety_transform(prompt)
    translated = _english_prompt_from_keywords(
        str(safety.get("sanitizedPrompt") or prompt),
        modality=modality,
        preserved_tokens=preserved,
    )
    if preserved:
        translated = f"{translated}\nPreserve these exact on-canvas text tokens verbatim: " + " | ".join(preserved)
    return {
        "rawUserRequest": prompt,
        "translatedPrompt": translated.strip(),
        "preservedTextTokens": preserved,
        "mustPreserveOriginalText": bool(preserved),
        "providerPromptLanguage": "en",
        "safetyTransform": safety,
    }


def _safe_int(value: Any, default: int, *, minimum: int = 1, maximum: int = 600) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _library_templates(library: dict[str, Any]) -> dict[str, Any]:
    return dict(library.get("templates") or {})


def _select_template(library: dict[str, Any], prompt: str, fallback: str) -> tuple[str, dict[str, Any]]:
    normalized_prompt = prompt.lower()
    templates = _library_templates(library)
    for key, payload in templates.items():
        keywords = [str(item).lower() for item in list((payload or {}).get("keywords") or [])]
        matched = False
        for keyword in keywords:
            if not keyword:
                continue
            if keyword.isascii() and keyword.replace("_", "").isalnum():
                if re.search(rf"(?<![a-z0-9_]){re.escape(keyword)}(?![a-z0-9_])", normalized_prompt):
                    matched = True
                    break
            elif keyword in normalized_prompt:
                matched = True
                break
        if matched:
            return key, dict(payload or {})
    return fallback, dict(templates.get(fallback) or {})


def _request_prompt(request: dict[str, Any]) -> str:
    return (
        _clean_str(request.get("prompt"))
        or _clean_str(request.get("userRequest"))
        or _clean_str(request.get("user_request"))
        or _clean_str(request.get("brief"))
        or _clean_str(request.get("text"))
    )


def _normalize_modality(value: Any) -> str:
    modality = _clean_str(value).lower()
    aliases = {
        "img": "image",
        "picture": "image",
        "photo": "image",
        "speech": "voice",
        "tts": "voice",
        "audio": "voice",
        "song": "music",
        "bgm": "music",
    }
    return aliases.get(modality, modality)


def _asset_symbol(modality: str, index: int) -> str:
    if modality == "video":
        return f"@video{index}"
    if modality == "voice":
        return f"@voice{index}"
    if modality == "music":
        return f"@music{index}"
    return f"@image{index}"


def _normalize_music_kind(value: Any, fallback: str = "cue_sheet") -> str:
    normalized = _clean_str(value).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "cue": "cue_sheet",
        "sheet": "cue_sheet",
        "bgm": "score_brief",
        "score": "score_brief",
        "background_score": "score_brief",
        "reference": "music_reference",
        "ref": "music_reference",
        "generation": "future_generation",
        "generate": "future_generation",
    }
    normalized = aliases.get(normalized, normalized)
    fallback_value = fallback if fallback in SUPPORTED_MUSIC_KINDS else "cue_sheet"
    return normalized if normalized in SUPPORTED_MUSIC_KINDS else fallback_value


def _normalize_asset(item: dict[str, Any], *, index: int) -> dict[str, Any]:
    role = _clean_str(item.get("role") or item.get("assetRole") or item.get("purpose") or "reference")
    modality = _normalize_modality(item.get("modality") or item.get("kind") or "image")
    return {
        "assetId": _clean_str(item.get("assetId") or item.get("id")),
        "role": role,
        "modality": modality,
        "symbol": _clean_str(item.get("symbol")) or _asset_symbol(modality, index),
        "artifactId": _clean_str(item.get("artifactId") or item.get("artifact_id")),
        "sourcePath": _clean_str(item.get("sourcePath") or item.get("source_path")),
        "workspacePath": _clean_str(item.get("workspacePath") or item.get("workspace_path")),
        "title": _clean_str(item.get("title") or item.get("name")) or f"asset-{index}",
        "metadata": dict(item.get("metadata") or {}),
    }


def _asset_summary(assets: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for asset in assets:
        ref = asset.get("symbol") or asset.get("assetId") or asset.get("artifactId") or asset.get("title")
        role = asset.get("role") or "reference"
        if ref:
            result.append(f"{ref} as {role}")
    return result


def _character_bible_summary(character_bibles: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for bible in character_bibles:
        anchors = [*list(bible.get("identityAnchors") or []), *list(bible.get("visualAnchors") or [])]
        summary = "；".join(str(item) for item in anchors[:4] if str(item).strip())
        name = bible.get("name") or bible.get("characterBibleId")
        if name:
            result.append(f"{name}: {summary}" if summary else str(name))
    return result


def _keyframe_summary(keyframes: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for keyframe in keyframes:
        ref = keyframe.get("artifactId") or keyframe.get("sourcePath") or keyframe.get("workspacePath") or keyframe.get("keyframeId")
        role = keyframe.get("role") or "reference"
        title = keyframe.get("title") or keyframe.get("shotId") or keyframe.get("keyframeId")
        if ref:
            result.append(f"{ref} as {role} keyframe ({title})")
    return result


def _read_store(filename: str, key: str) -> dict[str, Any]:
    payload = storage.read_json(filename)
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return {"version": 1, key: {}}
    payload.setdefault(key, {})
    return payload


def _write_store(filename: str, key: str, values: dict[str, Any]) -> None:
    storage.write_json(filename, {"version": 1, key: dict(values or {})})


class CreativeRecipeCompiler:
    """Deterministic P2a compiler. It does not call an LLM or media provider."""

    def compile_recipe(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = dict(request or {})
        modality = _normalize_modality(payload.get("modality") or "image")
        if modality not in SUPPORTED_RECIPE_MODALITIES:
            raise ValueError(f"Unsupported creative media recipe modality: {modality or 'missing'}")
        prompt = _request_prompt(payload)
        if not prompt:
            raise ValueError("creative media recipe compilation requires prompt/userRequest/text")

        if modality == "image":
            recipe = self._compile_image(payload, prompt)
        elif modality == "video":
            recipe = self._compile_video(payload, prompt)
        elif modality == "voice":
            recipe = self._compile_voice(payload, prompt)
        else:
            recipe = self._compile_music(payload, prompt)
        return self._save_recipe(recipe)

    def get_recipe(self, recipe_id: str) -> dict[str, Any] | None:
        return dict((_read_store(RECIPE_STORE_FILE, "recipes").get("recipes") or {}).get(str(recipe_id)) or {}) or None

    def list_recipes(self, *, modality: str | None = None, recipe_kind: str | None = None) -> list[dict[str, Any]]:
        recipes = list((_read_store(RECIPE_STORE_FILE, "recipes").get("recipes") or {}).values())
        normalized_modality = _normalize_modality(modality)
        normalized_kind = _clean_str(recipe_kind).lower()
        result: list[dict[str, Any]] = []
        for recipe in recipes:
            if normalized_modality and _normalize_modality(recipe.get("modality")) != normalized_modality:
                continue
            if normalized_kind and _clean_str(recipe.get("recipeKind")).lower() != normalized_kind:
                continue
            result.append(dict(recipe))
        result.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        return result

    def create_character_bible(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = dict(payload or {})
        bible_id = (
            _clean_str(request.get("characterBibleId") or request.get("character_bible_id") or request.get("id"))
            or f"cm_character_{uuid.uuid4().hex}"
        )
        store = _read_store(CHARACTER_BIBLE_STORE_FILE, "characterBibles")
        bibles = dict(store.get("characterBibles") or {})
        previous = dict(bibles.get(bible_id) or {})
        now = utc_now_iso()
        bible = {
            "characterBibleId": bible_id,
            **_scope_fields(request, previous),
            "name": _clean_str(request.get("name") or previous.get("name")) or bible_id,
            "description": _clean_str(request.get("description") or previous.get("description")),
            "identityAnchors": _list_of_strings(request.get("identityAnchors") or request.get("identity_anchors") or previous.get("identityAnchors")),
            "visualAnchors": _list_of_strings(request.get("visualAnchors") or request.get("visual_anchors") or previous.get("visualAnchors")),
            "voiceAnchors": _list_of_strings(request.get("voiceAnchors") or request.get("voice_anchors") or previous.get("voiceAnchors")),
            "wardrobe": _list_of_strings(request.get("wardrobe") or previous.get("wardrobe")),
            "props": _list_of_strings(request.get("props") or previous.get("props")),
            "negativeConstraints": _list_of_strings(request.get("negativeConstraints") or request.get("negative_constraints") or previous.get("negativeConstraints")),
            "sourceRefs": _list_of_strings(request.get("sourceRefs") or request.get("source_refs") or previous.get("sourceRefs")),
            "assetIds": _list_of_strings(request.get("assetIds") or request.get("asset_ids") or previous.get("assetIds")),
            "lineage": self._lineage_from_request(request, previous=previous.get("lineage")),
            "version": int(previous.get("version") or 0) + 1,
            "metadata": {**dict(previous.get("metadata") or {}), **dict(request.get("metadata") or {})},
            "createdAt": previous.get("createdAt") or now,
            "updatedAt": now,
        }
        bibles[bible_id] = bible
        _write_store(CHARACTER_BIBLE_STORE_FILE, "characterBibles", bibles)
        return deepcopy(bible)

    def get_character_bible(self, bible_id: str) -> dict[str, Any] | None:
        return dict((_read_store(CHARACTER_BIBLE_STORE_FILE, "characterBibles").get("characterBibles") or {}).get(str(bible_id)) or {}) or None

    def list_character_bibles(self) -> list[dict[str, Any]]:
        bibles = list((_read_store(CHARACTER_BIBLE_STORE_FILE, "characterBibles").get("characterBibles") or {}).values())
        bibles.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        return [dict(item) for item in bibles]

    def register_keyframe(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = dict(payload or {})
        keyframe_id = _clean_str(request.get("keyframeId") or request.get("keyframe_id") or request.get("id")) or f"cm_keyframe_{uuid.uuid4().hex}"
        store = _read_store(KEYFRAME_STORE_FILE, "keyframes")
        keyframes = dict(store.get("keyframes") or {})
        previous = dict(keyframes.get(keyframe_id) or {})
        now = utc_now_iso()
        keyframe = {
            "keyframeId": keyframe_id,
            **_scope_fields(request, previous),
            "recipeId": _clean_str(request.get("recipeId") or request.get("recipe_id") or previous.get("recipeId")),
            "shotId": _clean_str(request.get("shotId") or request.get("shot_id") or previous.get("shotId")),
            "role": _clean_str(request.get("role") or previous.get("role") or "reference"),
            "modality": _normalize_modality(request.get("modality") or previous.get("modality") or "image"),
            "artifactId": _clean_str(request.get("artifactId") or previous.get("artifactId")),
            "sourcePath": _clean_str(request.get("sourcePath") or previous.get("sourcePath")),
            "workspacePath": _clean_str(request.get("workspacePath") or previous.get("workspacePath")),
            "title": _clean_str(request.get("title") or previous.get("title")) or keyframe_id,
            "characterBibleIds": _list_of_strings(request.get("characterBibleIds") or request.get("character_bible_ids") or previous.get("characterBibleIds")),
            "sourceRefs": _list_of_strings(request.get("sourceRefs") or request.get("source_refs") or previous.get("sourceRefs")),
            "lineage": self._lineage_from_request(request, previous=previous.get("lineage")),
            "version": int(previous.get("version") or 0) + 1,
            "metadata": {**dict(previous.get("metadata") or {}), **dict(request.get("metadata") or {})},
            "createdAt": previous.get("createdAt") or now,
            "updatedAt": now,
        }
        if not any(keyframe.get(key) for key in ("artifactId", "sourcePath", "workspacePath")):
            raise ValueError("creative media keyframe requires artifactId, sourcePath, or workspacePath")
        keyframes[keyframe_id] = keyframe
        _write_store(KEYFRAME_STORE_FILE, "keyframes", keyframes)
        return deepcopy(keyframe)

    def get_keyframe(self, keyframe_id: str) -> dict[str, Any] | None:
        return dict((_read_store(KEYFRAME_STORE_FILE, "keyframes").get("keyframes") or {}).get(str(keyframe_id)) or {}) or None

    def list_keyframes(
        self,
        *,
        recipe_id: str | None = None,
        role: str | None = None,
        character_bible_id: str | None = None,
    ) -> list[dict[str, Any]]:
        keyframes = list((_read_store(KEYFRAME_STORE_FILE, "keyframes").get("keyframes") or {}).values())
        normalized_recipe_id = _clean_str(recipe_id)
        normalized_role = _clean_str(role).lower()
        normalized_bible_id = _clean_str(character_bible_id)
        result: list[dict[str, Any]] = []
        for keyframe in keyframes:
            if normalized_recipe_id and _clean_str(keyframe.get("recipeId")) != normalized_recipe_id:
                continue
            if normalized_role and _clean_str(keyframe.get("role")).lower() != normalized_role:
                continue
            if normalized_bible_id and normalized_bible_id not in _list_of_strings(keyframe.get("characterBibleIds")):
                continue
            result.append(dict(keyframe))
        result.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        return result

    def register_asset(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = dict(payload or {})
        asset_id = _clean_str(request.get("assetId") or request.get("id")) or f"cm_asset_{uuid.uuid4().hex}"
        ledger = _read_store(ASSET_LEDGER_FILE, "assets")
        assets = dict(ledger.get("assets") or {})
        previous = dict(assets.get(asset_id) or {})
        now = utc_now_iso()
        asset = {
            "assetId": asset_id,
            **_scope_fields(request, previous),
            "role": _clean_str(request.get("role") or previous.get("role") or "reference"),
            "modality": _normalize_modality(request.get("modality") or previous.get("modality") or "image"),
            "assetPlane": "creative_media_asset",
            "artifactId": _clean_str(request.get("artifactId") or previous.get("artifactId")),
            "sourcePath": _clean_str(request.get("sourcePath") or previous.get("sourcePath")),
            "workspacePath": _clean_str(request.get("workspacePath") or previous.get("workspacePath")),
            "title": _clean_str(request.get("title") or previous.get("title")) or asset_id,
            "sourceRefs": list(request.get("sourceRefs") or previous.get("sourceRefs") or []),
            "lineage": dict(request.get("lineage") or previous.get("lineage") or {}),
            "version": int(previous.get("version") or 0) + 1,
            "metadata": {**dict(previous.get("metadata") or {}), **dict(request.get("metadata") or {})},
            "createdAt": previous.get("createdAt") or now,
            "updatedAt": now,
        }
        if asset["modality"] == "music":
            asset["musicKind"] = _normalize_music_kind(
                request.get("musicKind") or request.get("music_kind") or previous.get("musicKind"),
                fallback="music_reference",
            )
        if not any(asset.get(key) for key in ("artifactId", "sourcePath", "workspacePath")):
            raise ValueError("creative media asset requires artifactId, sourcePath, or workspacePath")
        assets[asset_id] = asset
        _write_store(ASSET_LEDGER_FILE, "assets", assets)
        return deepcopy(asset)

    def list_assets(self, *, modality: str | None = None, role: str | None = None) -> list[dict[str, Any]]:
        assets = list((_read_store(ASSET_LEDGER_FILE, "assets").get("assets") or {}).values())
        normalized_modality = _normalize_modality(modality)
        normalized_role = _clean_str(role).lower()
        result: list[dict[str, Any]] = []
        for asset in assets:
            if normalized_modality and _normalize_modality(asset.get("modality")) != normalized_modality:
                continue
            if normalized_role and _clean_str(asset.get("role")).lower() != normalized_role:
                continue
            result.append(dict(asset))
        result.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        return result

    def _lineage_from_request(self, request: dict[str, Any], *, previous: Any = None) -> dict[str, Any]:
        lineage = {**dict(previous or {}), **dict(request.get("lineage") or {})}
        parent_id = _clean_str(request.get("parentRecipeId") or request.get("parent_recipe_id") or request.get("parentId"))
        supersedes_id = _clean_str(request.get("supersedesRecipeId") or request.get("supersedes_recipe_id") or request.get("supersedesId"))
        tombstone_of = _clean_str(request.get("tombstoneOf") or request.get("tombstone_of"))
        if parent_id:
            lineage["parentRecipeId"] = parent_id
        if supersedes_id:
            lineage["supersedesRecipeId"] = supersedes_id
        if tombstone_of:
            lineage["tombstoneOf"] = tombstone_of
        return lineage

    def _save_recipe(self, recipe: dict[str, Any]) -> dict[str, Any]:
        store = _read_store(RECIPE_STORE_FILE, "recipes")
        recipes = dict(store.get("recipes") or {})
        recipe_id = str(recipe["recipeId"])
        previous = dict(recipes.get(recipe_id) or {})
        recipe["version"] = int(previous.get("version") or 0) + 1
        recipe["updatedAt"] = utc_now_iso()
        recipes[recipe_id] = recipe
        _write_store(RECIPE_STORE_FILE, "recipes", recipes)
        return deepcopy(recipe)

    def _normalize_assets(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        assets: list[dict[str, Any]] = []
        for raw in list(request.get("assets") or request.get("inputAssets") or request.get("input_assets") or []):
            if isinstance(raw, dict):
                assets.append(_normalize_asset(raw, index=len(assets) + 1))
        for asset_id in _list_of_strings(request.get("assetIds") or request.get("asset_ids")):
            existing = self.list_assets()
            match = next((item for item in existing if item.get("assetId") == asset_id), None)
            if match:
                assets.append(_normalize_asset(match, index=len(assets) + 1))
        return assets

    def _base_recipe(self, request: dict[str, Any], prompt: str, modality: str, recipe_kind: str) -> dict[str, Any]:
        now = utc_now_iso()
        ratio = _clean_str(request.get("ratio") or request.get("aspectRatio") or request.get("aspect_ratio"))
        duration = request.get("durationSeconds") or request.get("duration_seconds") or request.get("duration")
        assets = self._normalize_assets(request)
        character_bible_ids = _list_of_strings(request.get("characterBibleIds") or request.get("character_bible_ids"))
        keyframe_ids = _list_of_strings(request.get("keyframeIds") or request.get("keyframe_ids"))
        character_bibles = [bible for bible_id in character_bible_ids if (bible := self.get_character_bible(bible_id))]
        keyframes = [keyframe for keyframe_id in keyframe_ids if (keyframe := self.get_keyframe(keyframe_id))]
        source_refs = [
            ref
            for ref in [
                *_list_of_strings(request.get("sourceRefs") or request.get("source_refs")),
                *[asset.get("assetId") or asset.get("artifactId") for asset in assets],
                *character_bible_ids,
                *keyframe_ids,
                *[keyframe.get("artifactId") for keyframe in keyframes],
            ]
            if ref
        ]
        hard_requirement_values = _list_of_strings(request.get("hardRequirements") or request.get("hard_requirements"))
        text_tokens = _extract_quoted_text(prompt)
        prompt_policy = prepare_provider_prompt_policy(prompt, modality=modality, preserved_tokens=text_tokens)
        hard_requirements = {
            "rawUserRequest": prompt,
            "translatedPrompt": prompt_policy["translatedPrompt"],
            "preservedTextTokens": prompt_policy["preservedTextTokens"],
            "mustPreserveOriginalText": prompt_policy["mustPreserveOriginalText"],
            "providerPromptLanguage": prompt_policy["providerPromptLanguage"],
            "safetyTransform": prompt_policy["safetyTransform"],
            "mustPreserve": hard_requirement_values or [prompt],
            "textTokens": text_tokens,
            "ratio": ratio,
            "durationSeconds": _safe_int(duration, 0, minimum=0, maximum=600) if duration is not None else None,
            "negativeConstraints": _list_of_strings(request.get("negativeConstraints") or request.get("negative_constraints") or request.get("negative")),
            "assetRefs": source_refs,
            "characterBibleIds": character_bible_ids,
            "keyframeIds": keyframe_ids,
        }
        return {
            "recipeId": _clean_str(request.get("recipeId") or request.get("id")) or f"cm_recipe_{uuid.uuid4().hex}",
            **_scope_fields(request),
            "version": 1,
            "modality": modality,
            "recipeKind": recipe_kind,
            "prompt": prompt,
            "source": "creative_media_recipe_compiler",
            "executionStatus": "compiled",
            "hardRequirements": hard_requirements,
            "softEnhancements": [],
            "controls": {
                "ratio": ratio,
                "durationSeconds": hard_requirements["durationSeconds"],
                "providerProfile": _clean_str(request.get("providerProfile") or request.get("provider_profile")),
                "seed": request.get("seed"),
            },
            "assets": assets,
            "characterBibles": character_bibles,
            "keyframes": keyframes,
            "sourceRefs": source_refs,
            "lineage": self._lineage_from_request(request),
            "providerNeutralRecipe": {},
            "providerPrompts": {},
            "constraintCheck": {"ok": True, "checks": [], "warnings": []},
            "createdAt": now,
            "updatedAt": now,
        }

    def _compile_image(self, request: dict[str, Any], prompt: str) -> dict[str, Any]:
        library = load_visual_recipe_library()
        recipe_kind, template = _select_template(library, prompt, "narrative_scene")
        recipe = self._base_recipe(request, prompt, "image", recipe_kind)
        ratio = recipe["controls"]["ratio"] or "1:1"
        enhancements = list(template.get("enhancements") or [])
        recipe["softEnhancements"] = enhancements
        structure = list(template.get("structure") or [])
        avoidances = [*list(template.get("avoid") or []), *recipe["hardRequirements"]["negativeConstraints"]]
        asset_lines = _asset_summary(recipe["assets"])
        character_lines = _character_bible_summary(recipe["characterBibles"])
        keyframe_lines = _keyframe_summary(recipe["keyframes"])
        provider_neutral = {
            "type": template.get("label") or recipe_kind,
            "objective": recipe["hardRequirements"]["translatedPrompt"],
            "structure": structure,
            "style": enhancements,
            "layoutControls": {"aspectRatio": ratio},
            "assets": asset_lines,
            "characters": character_lines,
            "keyframes": keyframe_lines,
            "avoid": avoidances,
        }
        recipe["providerNeutralRecipe"] = provider_neutral
        base_prompt = self._render_visual_prompt(provider_neutral, recipe["hardRequirements"])
        recipe["providerPrompts"] = {
            "openai_images": base_prompt,
            "volcengine_seedream": base_prompt,
        }
        recipe["controls"]["ratio"] = ratio
        recipe["constraintCheck"] = self._constraint_check(recipe, modality="image")
        return recipe

    def _compile_video(self, request: dict[str, Any], prompt: str) -> dict[str, Any]:
        library = load_video_recipe_library()
        recipe_kind, template = _select_template(library, prompt, "timed_storyboard")
        if request.get("editIntent") or request.get("edit_intent"):
            recipe_kind = "local_edit"
            template = dict(_library_templates(library).get("local_edit") or template)
        recipe = self._base_recipe(request, prompt, "video", recipe_kind)
        duration = _safe_int(recipe["controls"].get("durationSeconds"), 5, minimum=1, maximum=60)
        ratio = recipe["controls"]["ratio"] or "16:9"
        segments = self._timed_segments(duration, prompt)
        asset_lines = _asset_summary(recipe["assets"])
        character_lines = _character_bible_summary(recipe["characterBibles"])
        keyframe_lines = _keyframe_summary(recipe["keyframes"])
        camera_terms = list(template.get("cameraLanguage") or [])
        avoidances = [*list(template.get("avoid") or []), *recipe["hardRequirements"]["negativeConstraints"]]
        recipe["softEnhancements"] = list(template.get("enhancements") or [])
        recipe["controls"].update({"ratio": ratio, "durationSeconds": duration})
        recipe["providerNeutralRecipe"] = {
            "type": template.get("label") or recipe_kind,
            "objective": recipe["hardRequirements"]["translatedPrompt"],
            "timedSegments": segments,
            "cameraLanguage": camera_terms,
            "assets": asset_lines,
            "characters": character_lines,
            "keyframes": keyframe_lines,
            "avoid": avoidances,
        }
        seedance_prompt = self._render_seedance_prompt(recipe["providerNeutralRecipe"], duration)
        recipe["providerPrompts"] = {
            "volcengine_seedance": seedance_prompt,
            "video_generic": seedance_prompt,
        }
        if recipe_kind == "local_edit":
            recipe["editIntent"] = self._edit_intent(request, prompt, recipe["sourceRefs"])
        recipe["constraintCheck"] = self._constraint_check(recipe, modality="video")
        return recipe

    def _compile_voice(self, request: dict[str, Any], prompt: str) -> dict[str, Any]:
        library = load_audio_music_recipe_library()
        templates = dict((library.get("voice") or {}).get("templates") or {})
        recipe_kind, template = _select_template({"templates": templates}, prompt, "narration")
        recipe = self._base_recipe(request, prompt, "voice", recipe_kind)
        recipe["softEnhancements"] = list(template.get("delivery") or [])
        script = _clean_str(request.get("script")) or prompt
        recipe["providerNeutralRecipe"] = {
            "type": template.get("label") or recipe_kind,
            "script": script,
            "delivery": recipe["softEnhancements"],
            "subtitleAlignment": "short spoken clauses; keep subtitle lines readable",
        }
        recipe["providerPrompts"] = {
            "v8_audio_tts": {
                "text": script,
                "delivery": recipe["softEnhancements"],
                "notes": "Use existing V8 audio/TTS config; recipe compilation does not synthesize audio.",
            }
        }
        recipe["constraintCheck"] = self._constraint_check(recipe, modality="voice")
        return recipe

    def _compile_music(self, request: dict[str, Any], prompt: str) -> dict[str, Any]:
        library = load_audio_music_recipe_library()
        templates = dict((library.get("music") or {}).get("templates") or {})
        recipe_kind, template = _select_template({"templates": templates}, prompt, "background_score")
        recipe = self._base_recipe(request, prompt, "music", recipe_kind)
        music_kind = _normalize_music_kind(
            request.get("musicKind") or request.get("music_kind"),
            fallback="score_brief" if recipe_kind == "background_score" else "cue_sheet",
        )
        duration = _safe_int(recipe["controls"].get("durationSeconds"), 30, minimum=1, maximum=600)
        recipe["executionStatus"] = "catalog_only"
        recipe["musicKind"] = music_kind
        recipe["creativeMediaPlane"] = "creative_music_plan"
        recipe["controls"]["durationSeconds"] = duration
        recipe["controls"]["musicKind"] = music_kind
        recipe["softEnhancements"] = list(template.get("arrangement") or [])
        recipe["providerNeutralRecipe"] = {
            "type": template.get("label") or recipe_kind,
            "musicKind": music_kind,
            "deliveryPlane": "creative_media_asset_ledger",
            "objective": recipe["hardRequirements"]["translatedPrompt"],
            "durationSeconds": duration,
            "cueSheet": self._music_cues(duration, prompt),
            "arrangement": recipe["softEnhancements"],
            "rightsNote": "Music is a Creative Media cue/brief/reference plan here. It is not a legacy MusicTrack URL player entry.",
        }
        recipe["providerPrompts"] = {
            "creative_music_brief": recipe["providerNeutralRecipe"],
        }
        recipe["constraintCheck"] = self._constraint_check(recipe, modality="music")
        recipe["constraintCheck"]["warnings"].append(
            "music recipe is catalog_only; no provider job is created and no legacy MusicTrack is written"
        )
        return recipe

    def _render_visual_prompt(self, provider_neutral: dict[str, Any], hard_requirements: dict[str, Any]) -> str:
        lines = [
            f"Objective: {provider_neutral.get('objective')}",
            f"Visual type: {provider_neutral.get('type')}",
        ]
        if provider_neutral.get("structure"):
            lines.append("Composition structure: " + "; ".join(str(item) for item in provider_neutral["structure"]))
        if provider_neutral.get("style"):
            lines.append("Style enhancements: " + "; ".join(str(item) for item in provider_neutral["style"]))
        if provider_neutral.get("assets"):
            lines.append("Reference assets: " + "; ".join(str(item) for item in provider_neutral["assets"]))
        if provider_neutral.get("characters"):
            lines.append("Character bible: " + "; ".join(str(item) for item in provider_neutral["characters"]))
        if provider_neutral.get("keyframes"):
            lines.append("Keyframes: " + "; ".join(str(item) for item in provider_neutral["keyframes"]))
        if hard_requirements.get("preservedTextTokens"):
            lines.append("Preserve exact on-canvas text: " + " | ".join(hard_requirements["preservedTextTokens"]))
        if hard_requirements.get("ratio"):
            lines.append(f"Aspect ratio: {hard_requirements['ratio']}")
        if provider_neutral.get("avoid"):
            lines.append("Avoid: " + "; ".join(str(item) for item in provider_neutral["avoid"]))
        return "\n".join(line for line in lines if line.strip())

    def _render_seedance_prompt(self, provider_neutral: dict[str, Any], duration: int) -> str:
        lines: list[str] = []
        assets = list(provider_neutral.get("assets") or [])
        if assets:
            lines.append("Asset references: " + "; ".join(str(item) for item in assets))
        characters = list(provider_neutral.get("characters") or [])
        if characters:
            lines.append("Character continuity: " + "; ".join(str(item) for item in characters))
        keyframes = list(provider_neutral.get("keyframes") or [])
        if keyframes:
            lines.append("Keyframe constraints: " + "; ".join(str(item) for item in keyframes))
        lines.append(f"{duration}-second video. Objective: {provider_neutral.get('objective')}")
        for segment in list(provider_neutral.get("timedSegments") or []):
            lines.append(f"{segment['start']}-{segment['end']}s: {segment['description']}")
        camera = provider_neutral.get("cameraLanguage") or []
        if camera:
            lines.append("Camera language: " + ", ".join(str(item) for item in camera[:4]))
        avoid = provider_neutral.get("avoid") or []
        if avoid:
            lines.append("Avoid: " + "; ".join(str(item) for item in avoid))
        return "\n".join(lines)

    def _timed_segments(self, duration: int, prompt: str) -> list[dict[str, Any]]:
        prompt_policy = prepare_provider_prompt_policy(prompt, modality="video")
        objective = prompt_policy["translatedPrompt"]
        if duration <= 5:
            return [{"start": 0, "end": duration, "description": f"One clear action or establishing shot: {objective}"}]
        if duration <= 10:
            midpoint = max(3, duration // 2)
            return [
                {"start": 0, "end": midpoint, "description": f"Establish the subject, scene, and one primary action: {objective}"},
                {"start": midpoint, "end": duration, "description": "Continue the action and settle into an edit-friendly ending; avoid abrupt scene changes."},
            ]
        first = min(5, duration // 3)
        second = min(10, max(first + 3, (duration * 2) // 3))
        return [
            {"start": 0, "end": first, "description": f"Establish subject and spatial relationship: {objective}"},
            {"start": first, "end": second, "description": "Develop one main action or camera movement while keeping character and scene continuity."},
            {"start": second, "end": duration, "description": "End on a stable hold or transition frame for later stitching."},
        ]

    def _music_cues(self, duration: int, prompt: str) -> list[dict[str, Any]]:
        if duration <= 15:
            return [{"start": 0, "end": duration, "cue": f"单段音乐气氛: {prompt}"}]
        midpoint = duration // 2
        return [
            {"start": 0, "end": midpoint, "cue": f"引入主题、节奏和核心情绪: {prompt}"},
            {"start": midpoint, "end": duration, "cue": "发展并收束，保留可循环或剪辑结尾。"},
        ]

    def _edit_intent(self, request: dict[str, Any], prompt: str, source_refs: Iterable[str]) -> dict[str, Any]:
        return {
            "status": "compiled_only",
            "sourceRefs": list(source_refs),
            "preserve": _list_of_strings(request.get("preserve") or request.get("preserveRegions")) or ["Preserve the source subject and all unspecified regions."],
            "modify": _list_of_strings(request.get("modify") or request.get("editTargets")) or [prepare_provider_prompt_policy(prompt, modality="image")["translatedPrompt"]],
            "providerPrompt": (
                f"Localized edit intent: {prepare_provider_prompt_policy(prompt, modality='image')['translatedPrompt']}\n"
                "Preserve all unspecified regions; only modify the explicitly requested object, area, or style."
            ),
            "riskNotes": ["P2a compiles the edit intent only; executable edit adapters are handled by later runtime phases."],
        }

    def _constraint_check(self, recipe: dict[str, Any], *, modality: str) -> dict[str, Any]:
        warnings: list[str] = []
        checks = [
            {"name": "hard_requirements_present", "ok": bool(recipe.get("hardRequirements", {}).get("mustPreserve"))},
            {"name": "provider_neutral_recipe_present", "ok": bool(recipe.get("providerNeutralRecipe"))},
            {"name": "provider_prompt_present", "ok": bool(recipe.get("providerPrompts"))},
        ]
        duration = int(recipe.get("controls", {}).get("durationSeconds") or 0)
        prompt = str(recipe.get("prompt") or "")
        if modality == "video":
            action_markers = len(re.findall(r"[，,、;；]|然后|随后|接着|同时|切换|转场", prompt))
            if duration <= 5 and action_markers >= 4:
                warnings.append("5秒以内内容动作偏多，建议拆成更短单一动作或多段生成。")
            if not recipe.get("assets"):
                warnings.append("没有参考资产时，角色一致性和首尾帧稳定性需要后续人工检查。")
        if modality == "image" and recipe.get("hardRequirements", {}).get("textTokens"):
            warnings.append("图片中文字可读性需生成后检查，compiler 只能保留文字要求。")
        if modality == "voice" and len(prompt) > 240:
            warnings.append("语音脚本偏长，建议后续按字幕句读切分。")
        ok = all(bool(item["ok"]) for item in checks)
        return {
            "ok": ok,
            "hardRequirementsPreserved": True,
            "checks": checks,
            "warnings": warnings,
        }


creative_recipe_compiler = CreativeRecipeCompiler()


__all__ = [
    "ASSET_LEDGER_FILE",
    "CHARACTER_BIBLE_STORE_FILE",
    "KEYFRAME_STORE_FILE",
    "RECIPE_STORE_FILE",
    "SUPPORTED_RECIPE_MODALITIES",
    "SUPPORTED_MUSIC_KINDS",
    "CreativeRecipeCompiler",
    "creative_recipe_compiler",
    "prepare_provider_prompt_policy",
]
