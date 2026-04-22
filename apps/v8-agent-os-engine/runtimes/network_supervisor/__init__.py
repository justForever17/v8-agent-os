__all__ = ["network_supervisor_runtime", "network_supervisor_service"]


def __getattr__(name: str):
    if name == "network_supervisor_runtime":
        from runtimes.network_supervisor.runtime import network_supervisor_runtime

        return network_supervisor_runtime
    if name == "network_supervisor_service":
        from runtimes.network_supervisor.service import network_supervisor_service

        return network_supervisor_service
    raise AttributeError(name)
