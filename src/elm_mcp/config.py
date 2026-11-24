"""Configuration for ELM connection."""

import base64
import binascii
import json
import os

from pydantic import PrivateAttr
from pydantic_settings import BaseSettings


def _load_creds_file() -> dict:
    """Load credentials from ~/.elm_creds.json if it exists."""
    path = os.path.expanduser("~/.elm_creds.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def _decode_password(value: str) -> str:
    """Decode the base64 password stored in ~/.elm_creds.json (always base64, written by ``cli.login``)."""
    if not value:
        return value
    try:
        return base64.b64decode(value, validate=True).decode("utf-8")
    except (binascii.Error, ValueError) as exc:  # UnicodeDecodeError is a ValueError
        raise ValueError(
            "Invalid 'password' in ~/.elm_creds.json: expected base64 of a UTF-8 string. Run 'elm-mcp login' again."
        ) from exc


class Settings(BaseSettings):
    elm_url: str = ""
    elm_user: str = ""
    elm_password: str = ""

    model_config = {"env_prefix": "", "env_file": ".env"}

    # Recorded instead of raised: an invalid credential must not break the import (``elm-mcp login`` is
    # the fix); auth._authenticate raises it when a login is attempted.
    _credentials_error: str = PrivateAttr(default="")

    @property
    def credentials_error(self) -> str:
        return self._credentials_error

    def model_post_init(self, context, /) -> None:
        # Load from ~/.elm_creds.json as fallback
        if not self.elm_user:
            creds = _load_creds_file()
            if creds:
                object.__setattr__(self, "elm_user", creds.get("username", ""))
                try:
                    object.__setattr__(self, "elm_password", _decode_password(creds.get("password", "")))
                except ValueError as exc:
                    self._credentials_error = str(exc)
                if not self.elm_url:
                    url = creds.get("url")
                    if url:
                        object.__setattr__(self, "elm_url", url)
        elif self.elm_password.startswith("b64:"):
            # ELM_PASSWORD is used as is; base64 only with the explicit "b64:" prefix.
            # "b64:b64:x" escapes the prefix: the literal "b64:x".
            if self.elm_password.startswith("b64:b64:"):
                object.__setattr__(self, "elm_password", self.elm_password[4:])
                return
            try:
                decoded = base64.b64decode(self.elm_password[4:].strip(), validate=True).decode("utf-8")
            except (binascii.Error, ValueError):
                self._credentials_error = "Invalid ELM_PASSWORD: 'b64:' must be followed by base64 of a UTF-8 string."
                object.__setattr__(self, "elm_password", "")
                return
            object.__setattr__(self, "elm_password", decoded)


settings = Settings()
