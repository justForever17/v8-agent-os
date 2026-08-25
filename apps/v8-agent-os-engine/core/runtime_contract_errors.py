from __future__ import annotations


class SupervisorRuntimeRouteContractError(RuntimeError):
    code = "supervisor_runtime_route_contract_invalid"

    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "invalid_runtime_route").strip()[:320]
        super().__init__(self.reason)
