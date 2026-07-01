"""Langfuse tracing setup with graceful degradation when credentials are absent."""

# ============= Standard Library =============
import logging
import os

# ============= Constants =============
logger = logging.getLogger(__name__)


# ============= Public Functions =============

def build_langfuse_callback() -> list:
    """
    Return a list containing a LangfuseCallbackHandler if credentials are set.

    Returns
    -------
    list
        List with one LangfuseCallbackHandler, or empty list if not configured.
    """
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

    if not public_key or not secret_key:
        logger.warning("LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY not set, tracing disabled")
        return []

    try:
        from langfuse.callback import CallbackHandler
        handler = CallbackHandler(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )
        logger.info("langfuse tracing enabled")
        return [handler]
    except ImportError:
        logger.warning("langfuse not installed, tracing disabled")
        return []
    except Exception as exc:
        logger.warning(f"langfuse setup failed: {exc}, tracing disabled")
        return []
