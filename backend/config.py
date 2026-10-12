"""Runtime model configuration for the five operational agents (Problem 5, Stage 5).

The ONE place the operational model is defined. All five agents use gpt-6-luna through Portkey.

Settings (process environment first, then hw5/.env, then the course root .env, which is where
PORTKEY_API_KEY was kept in Homework 3 and 4 per AGENTS.md):
  PORTKEY_API_KEY    required for live runs; never printed, logged, or stored in the audit trail
  PORTKEY_BASE_URL   default https://api.portkey.ai/v1 (Portkey docs; also used in Homework 3/4)
  PORTKEY_MODEL      default gpt-6-luna; ONLY gpt-6-luna or @<slug>/gpt-6-luna is accepted

Building the model object makes no network call. Nothing here runs a model.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

HW5_ROOT = Path(__file__).resolve().parent.parent
ENV_FILES = (HW5_ROOT / ".env", HW5_ROOT.parent / ".env")
REQUIRED_MODEL = "gpt-6-luna"
DEFAULT_BASE_URL = "https://api.portkey.ai/v1"
MODEL_PATTERN = re.compile(r"^(@[A-Za-z0-9_.-]+/)?gpt-6-luna$")

# Problem 9 fix (failed live run 53e4f001-d845-4c50-a3a6-1f09ebee50d6): gpt-6-luna reasons by default, and the
# provider (gpt-6-luna-global via Portkey) refuses function tools combined with reasoning on /v1/chat/completions,
# answering "set reasoning_effort to 'none'". The agents act only through function tools (MCP tools, delegate,
# the structured report), so reasoning is turned off explicitly on every request from this model.
MODEL_SETTINGS = {"openai_reasoning_effort": "none"}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ModelConfig:
    model_name: str
    base_url: str
    api_key: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return (f"ModelConfig(model_name={self.model_name!r}, base_url={self.base_url!r}, "
                f"api_key={'<set>' if self.api_key else '<missing>'})")


def _setting(name: str, env: Mapping[str, str], files) -> str | None:
    if env.get(name):
        return env[name]
    for f in files:
        if Path(f).is_file():
            value = dotenv_values(f).get(name)
            if value:
                return value
    return None


def load_config(env: Mapping[str, str] | None = None, env_files=ENV_FILES) -> ModelConfig:
    env = os.environ if env is None else env
    model = (_setting("PORTKEY_MODEL", env, env_files) or REQUIRED_MODEL).strip()
    if not MODEL_PATTERN.match(model):
        raise ConfigError(f"PORTKEY_MODEL must be {REQUIRED_MODEL!r} (optionally '@<slug>/{REQUIRED_MODEL}'); "
                          f"got {model!r}. No other operational model is authorized.")
    base_url = (_setting("PORTKEY_BASE_URL", env, env_files) or DEFAULT_BASE_URL).strip()
    api_key = _setting("PORTKEY_API_KEY", env, env_files)
    _register_with_audit_guard(api_key)
    return ModelConfig(model_name=model, base_url=base_url, api_key=api_key)


def _register_with_audit_guard(value: str | None) -> None:
    """A key read from a .env file is not in os.environ, so tell the audit trail's credential guard
    about it (remediation m2). The value is held only in memory."""
    import sys
    mcp_dir = str(HW5_ROOT / "mcp_server")
    if mcp_dir not in sys.path:
        sys.path.insert(0, mcp_dir)
    import audit_trail
    audit_trail.register_secret(value)


def build_live_model(cfg: ModelConfig, budget, trace_id: str | None = None):
    """The model for LIVE agent runs, with the budget's HTTP timeout and retry limits (remediation M5)."""
    return build_model(cfg, trace_id, timeout=budget.request_timeout_seconds, max_retries=budget.max_http_retries)


def build_model(cfg: ModelConfig, trace_id: str | None = None, *, timeout: float | None = None,
                max_retries: int | None = None):
    """The shared gpt-6-luna model for all five agents. No network call happens here.
    timeout / max_retries override the OpenAI client's defaults (e.g. max_retries=0 for a single-request check)."""
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    if not cfg.api_key:
        raise ConfigError("PORTKEY_API_KEY is not set (environment, hw5/.env, or the course root .env).")
    headers = {"x-portkey-trace-id": trace_id} if trace_id else None   # ties Portkey logs to the audit run_id
    options = {k: v for k, v in (("timeout", timeout), ("max_retries", max_retries)) if v is not None}
    client = AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url, default_headers=headers, **options)
    return OpenAIChatModel(cfg.model_name, provider=OpenAIProvider(openai_client=client), settings=dict(MODEL_SETTINGS))


def is_required_model(model) -> bool:
    """True only for an OpenAI-compatible chat model named gpt-6-luna (live runs must use it)."""
    from pydantic_ai.models.openai import OpenAIChatModel
    return isinstance(model, OpenAIChatModel) and bool(MODEL_PATTERN.match(model.model_name))


def has_required_settings(model) -> bool:
    """True if the model carries MODEL_SETTINGS (reasoning off), without which the provider rejects tool calls."""
    settings = getattr(model, "settings", None) or {}
    return all(settings.get(k) == v for k, v in MODEL_SETTINGS.items())
