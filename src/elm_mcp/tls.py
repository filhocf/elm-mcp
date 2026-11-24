"""TLS setting for httpx, read from the environment. Imports nothing from config, so it is safe at import time."""

import logging
import os
import ssl

_tls_warned = False  # the "TLS verification disabled" warning is logged once per process
_VERIFY_OFF = {"false", "0", "no", "off"}


def _tls_verify() -> ssl.SSLContext | bool:
    """TLS setting for httpx from the environment; verification is on unless explicitly disabled.

    ELM_VERIFY_TLS in {false,0,no,off} disables it (wins over ELM_CA_BUNDLE); ELM_CA_BUNDLE is a CA file
    path, or "system" for the OS trust store.
    """
    global _tls_warned
    if os.environ.get("ELM_VERIFY_TLS", "").strip().lower() in _VERIFY_OFF:
        if not _tls_warned:
            _tls_warned = True
            logging.getLogger(__name__).warning(
                "TLS certificate verification is DISABLED (ELM_VERIFY_TLS is set to a false value): "
                "connections are open to man-in-the-middle attacks. Prefer ELM_CA_BUNDLE."
            )
        return False
    bundle = os.environ.get("ELM_CA_BUNDLE", "").strip()
    if not bundle:
        return True  # httpx default: verify against the certifi bundle
    if bundle.lower() == "system":
        return ssl.create_default_context()
    if not os.path.isfile(bundle):
        raise ValueError(f"ELM_CA_BUNDLE points to a file that does not exist: {bundle}")
    try:
        return ssl.create_default_context(cafile=bundle)
    except (ssl.SSLError, OSError) as exc:
        raise ValueError(f"ELM_CA_BUNDLE points to a file that is not a valid PEM CA bundle: {bundle}") from exc
