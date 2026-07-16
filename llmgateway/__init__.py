"""llm-gateway — a multi-provider LLM gateway built on FastAPI."""
__version__ = "0.1.0"

__all__ = ["__version__", "create_app", "Config"]


def __getattr__(name):  # lazy re-export to avoid importing FastAPI at package import
    if name in ("create_app", "Config"):
        from .app import Config, create_app

        return {"create_app": create_app, "Config": Config}[name]
    raise AttributeError(name)
