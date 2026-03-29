from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .compiler import RPATraceCompiler, rpa_trace_compiler
    from .runtime import RPARuntime, rpa_runtime

__all__ = ["rpa_trace_compiler", "RPATraceCompiler", "rpa_runtime", "RPARuntime"]


def __getattr__(name: str):
    if name in {"rpa_trace_compiler", "RPATraceCompiler"}:
        from .compiler import RPATraceCompiler, rpa_trace_compiler

        exports = {
            "rpa_trace_compiler": rpa_trace_compiler,
            "RPATraceCompiler": RPATraceCompiler,
        }
        return exports[name]
    if name in {"rpa_runtime", "RPARuntime"}:
        from .runtime import RPARuntime, rpa_runtime

        exports = {
            "rpa_runtime": rpa_runtime,
            "RPARuntime": RPARuntime,
        }
        return exports[name]
    raise AttributeError(name)
