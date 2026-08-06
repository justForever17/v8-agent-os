__all__ = [
    "ExtensionRouteBundle",
    "ExtensionsRuntime",
    "ExtensionsRuntimeService",
    "extensions_runtime",
    "extensions_runtime_service",
]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(name)
    from runtimes.extensions import runtime

    return getattr(runtime, name)
