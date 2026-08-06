__all__ = ["chat_runtime"]


def __getattr__(name: str):
    if name != "chat_runtime":
        raise AttributeError(name)
    from runtimes.chat.runtime import chat_runtime

    return chat_runtime
