__all__ = ["creative_media_runtime"]


def __getattr__(name: str):
    if name != "creative_media_runtime":
        raise AttributeError(name)
    from runtimes.creative_media.runtime import creative_media_runtime

    return creative_media_runtime
