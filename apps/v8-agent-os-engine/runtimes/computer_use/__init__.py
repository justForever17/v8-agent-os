from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime import ComputerUseRuntime, computer_use_runtime

__all__ = ["computer_use_runtime", "ComputerUseRuntime"]


def __getattr__(name: str):
    if name in {"computer_use_runtime", "ComputerUseRuntime"}:
        from .runtime import ComputerUseRuntime, computer_use_runtime

        exports = {
            "computer_use_runtime": computer_use_runtime,
            "ComputerUseRuntime": ComputerUseRuntime,
        }
        return exports[name]
    raise AttributeError(name)
