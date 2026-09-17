"""Portable configuration is an explicit projection, never a config snapshot.

Only scalar model budgets/temperature and built-in role assignments are portable.
Models, credentials, paths, workspace authority and peer grants remain target-local.
The existing Config Broker remains the only writer of configuration.
"""
from __future__ import annotations

import math
from copy import deepcopy

from fastapi import HTTPException

from core.config_broker_service import config_broker_service
from core.model_control_plane import DEFAULT_GOVERNANCE, DEFAULT_ROLE_MAP, model_control_plane

BUDGET_KEYS = frozenset({"enabled", "globalDailyCostLimit", "globalDailyTokenLimit", "runMaxCost",
                         "runMaxTokens", "defaultProjectDailyCostLimit", "defaultProjectDailyTokenLimit"})
ROLE_KEYS = frozenset(DEFAULT_ROLE_MAP)
PATH_POLICY = "target_local_only"


def reject(code: str, status: int = 422):
    raise HTTPException(status, code)


def role_items(keys):
    definitions = model_control_plane.get_role_definitions(model_control_plane.get_storage_safe_config())
    return [{"id": key, "label": str(definitions.get(key, {}).get("label") or key)} for key in sorted(keys)]


def templates():
    config = model_control_plane.get_storage_safe_config()
    budgets = {**DEFAULT_GOVERNANCE["budgets"], **dict(config.get("governance", {}).get("budgets") or {})}
    parameters = {key: {"temperature": value.get("temperature")} for key, value in
                  dict(config.get("roleParameters") or {}).items() if key in ROLE_KEYS and isinstance(value, dict)}
    roles = {key: "" for key, value in dict(config.get("roles") or {}).items() if key in ROLE_KEYS and value}
    return [
        {"id": "model-policy", "label": "模型预算与参数", "description": "分发预算和温度；不包含项目路径、凭据或授权。",
         "values": {"governance": {"budgets": {key: budgets[key] for key in sorted(BUDGET_KEYS)}},
                    "roleParameters": parameters}, "roles": role_items(parameters)},
        {"id": "model-roles", "label": "模型角色映射", "description": "为源设备的角色选择每台目标设备本地已配置的兼容模型。",
         "values": {"roles": roles}, "roles": role_items(roles)},
    ]


def validate(template_id, values, mapping, *, allow_unmapped=False):
    if not isinstance(values, dict) or not isinstance(mapping, dict) or set(mapping) - {"roles", "models"}:
        reject("distribution_template_invalid")
    roles = mapping.get("roles", {})
    models = mapping.get("models", {})
    if not isinstance(roles, dict) or not isinstance(models, dict):
        reject("distribution_mapping_invalid")
    if template_id == "model-policy":
        if set(values) != {"governance", "roleParameters"} or not isinstance(values["governance"], dict) or set(values["governance"]) != {"budgets"}:
            reject("distribution_policy_fields_forbidden")
        budgets = values["governance"]["budgets"]
        params = values["roleParameters"]
        if not isinstance(budgets, dict) or set(budgets) - BUDGET_KEYS or not isinstance(params, dict) or set(params) - ROLE_KEYS:
            reject("distribution_policy_fields_forbidden")
        for key, value in budgets.items():
            if key == "enabled":
                valid = isinstance(value, bool)
            else:
                valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
            if not valid:
                reject("distribution_budget_invalid")
        for value in params.values():
            if not isinstance(value, dict) or set(value) != {"temperature"}:
                reject("distribution_parameters_invalid")
            temperature = value["temperature"]
            if temperature is not None and (type(temperature) not in (int, float) or not math.isfinite(temperature) or not 0 <= temperature <= 2):
                reject("distribution_parameters_invalid")
        source_roles = set(params)
        if models:
            reject("distribution_mapping_invalid")
    elif template_id == "model-roles":
        if set(values) != {"roles"} or not isinstance(values["roles"], dict) or not values["roles"] or set(values["roles"]) - ROLE_KEYS:
            reject("distribution_roles_invalid")
        # The source transports role identities only, never a source provider record.
        if any(value != "" for value in values["roles"].values()):
            reject("distribution_source_models_forbidden")
        source_roles = set(values["roles"])
        if set(models) - source_roles or any(not isinstance(value, str) or not value or len(value) > 256 for value in models.values()):
            reject("distribution_mapping_invalid")
        if set(models) != source_roles and not allow_unmapped:
            reject("target_model_mapping_required")
    else:
        reject("distribution_template_unsupported")
    if set(roles) - source_roles or any(not isinstance(value, str) or value not in ROLE_KEYS for value in roles.values()):
        reject("distribution_role_mapping_invalid")
    mapped = [roles.get(key, key) for key in source_roles]
    if len(set(mapped)) != len(mapped):
        reject("distribution_role_mapping_duplicate")


def capabilities():
    config = model_control_plane.get_storage_safe_config()
    models = []
    for item in model_control_plane.list_models(config):
        eligibility = item.get("eligibility") or {}
        missing = model_requirements(config, item)
        ready = eligibility.get("selectable") is True and not missing
        models.append({"modelRef": item["modelRef"], "label": str(item.get("name") or item.get("modelId") or item["modelRef"]),
                       "ready": ready, "missingRequirements": missing or ([] if ready else ["target_local_credentials_or_capability_required"])})
    return {"protocolVersion": 1, "roles": role_items(ROLE_KEYS), "models": models, "pathPolicy": PATH_POLICY}


def model_requirements(config, item):
    provider_id = item.get("providerId") or ""
    provider = (config.get("providers", {}).get(provider_id) or {}).get("provider") or {}
    if not provider.get("authContract", {}).get("type") and not (provider.get("credentialRef") or provider.get("credential_ref")):
        return ["target_local_auth_contract_required"]
    readiness = config_broker_service._verify_committed_provider_static(provider_id=provider_id, config=config)
    return [] if readiness.get("ok") else ["target_local_" + readiness["status"]]


def assert_target_ready(template_id, mapping):
    if template_id != "model-roles":
        return
    config = model_control_plane.get_storage_safe_config()
    models = {item["modelRef"]: item for item in model_control_plane.list_models(config)}
    for reference in mapping.get("models", {}).values():
        item = models.get(reference)
        if item is None:
            reject("model_not_found", 404)
        missing = model_requirements(config, item)
        if missing:
            reject(missing[0], 409)


def prepare(template_id, values, mapping, *, owner_id, run_id):
    validate(template_id, values, mapping)
    assert_target_ready(template_id, mapping)
    role_map = mapping.get("roles", {})
    kwargs = {"owner_id": owner_id, "session_id": "", "run_id": run_id}
    if template_id == "model-policy":
        return config_broker_service.prepare_model_policy(governance=values["governance"], routing_policies={},
            role_parameters={role_map.get(key, key): value for key, value in values["roleParameters"].items()}, **kwargs)
    return config_broker_service.prepare_role_bindings(
        updates={role_map.get(key, key): value for key, value in mapping["models"].items()}, **kwargs)


def projection(template_id, values, mapping, config=None):
    config = config if config is not None else model_control_plane.get_storage_safe_config()
    role_map = mapping.get("roles", {})
    if template_id == "model-roles":
        return {"roles." + role_map.get(key, key): config.get("roles", {}).get(role_map.get(key, key), "") for key in values["roles"]}
    result = {"governance.budgets." + key: config.get("governance", {}).get("budgets", {}).get(key, DEFAULT_GOVERNANCE["budgets"][key])
              for key in values["governance"]["budgets"]}
    result.update({"roleParameters." + role_map.get(key, key) + ".temperature":
                   config.get("roleParameters", {}).get(role_map.get(key, key), {}).get("temperature") for key in values["roleParameters"]})
    return deepcopy(result)


def diff_from_transaction(template_id, values, mapping, transaction):
    before = transaction["before"]
    proposed = transaction["proposed"]
    after = deepcopy(before)
    if template_id == "model-roles":
        after["roles"] = {**after.get("roles", {}), **proposed["updates"]}
    else:
        after.update({key: value for key, value in proposed.items() if key in {"governance", "roleParameters"}})
    first = projection(template_id, values, mapping, before)
    last = projection(template_id, values, mapping, after)
    return [{"field": key, "before": first[key], "after": last[key]} for key in sorted(first)]
