"""ONE live connectivity request to gpt-6-luna through Portkey (Problem 5; authorized by Dr. Ryan, follow-up 25).

Run from hw5/:  .venv/Scripts/python scripts/live_connectivity_check.py --confirm

Uses the centralized configuration (backend/config.py) and the same PydanticAI OpenAI chat model the
agents use, but sends a single plain model request: no agent, no tools, no MCP, no database, and no
audit-trail event. The OpenAI client's automatic retries are disabled (max_retries=0), the request has a
timeout and a small output-token limit, and nothing is retried on failure. The API key is never printed
or written; the saved report is checked for it before writing.

Writes output/p5_live_connectivity.json.

Exit codes (same convention as scripts/reset_db.py): 0 request succeeded; 1 request failed or no key;
2 dry run, because --confirm was not given, so NOTHING was sent or written (by design, not an error);
3 report not written because it would have contained the key.
"""

import argparse
import asyncio
import hashlib
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

HW5 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HW5 / "backend"))
import os  # noqa: E402

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
import config  # noqa: E402

PROMPT = ("Connectivity test in a software test environment. Do not use tools. Reply with JSON only, exactly "
          'this shape: {"role": "<a short phrase describing your role in this test>", "status": "ok"}')
MAX_TOKENS, TIMEOUT_S = 80, 30
WATCHED = ["data/campus_customs.db", "data/campus_customs_new.db", "output/audit_trail.json", "output/mcp_smoke.json"]


def sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--confirm", action="store_true", help="actually send the single request")
    args = parser.parse_args()
    if not args.confirm:
        print("Nothing sent. Re-run with --confirm to send exactly one request.", file=sys.stderr)
        return 2

    cfg = config.load_config()
    if not cfg.api_key:
        print("PORTKEY_API_KEY is not set; nothing sent.", file=sys.stderr)
        return 1
    check_id = str(uuid.uuid4())
    before = {f: sha(HW5 / f) for f in WATCHED}
    report = {
        "label": "LIVE connectivity check: one request to the operational model. Not an agent run; no tools, "
                 "MCP, database, proposals, drafts, or audit-trail events.",
        "authorization": "Dr. Ryan, AI_prompts.md Problem 5 follow-up 25",
        "check_id": check_id,
        "requested_model": cfg.model_name,
        "gateway_host": urlparse(cfg.base_url).netloc,
        "api_key": "present (value never recorded)",
        "settings": {"max_tokens": MAX_TOKENS, "timeout_seconds": TIMEOUT_S, "client_max_retries": 0,
                     "requests_sent": 1, "portkey_trace_id": check_id},
        "prompt": PROMPT,
    }

    from pydantic_ai.direct import model_request
    from pydantic_ai.messages import ModelRequest

    model = config.build_model(cfg, trace_id=check_id, timeout=TIMEOUT_S, max_retries=0)
    started = time.monotonic()
    report["sent_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        response = asyncio.run(model_request(model, [ModelRequest.user_text_prompt(PROMPT)],
                                             model_settings={"max_tokens": MAX_TOKENS, "timeout": TIMEOUT_S}))
        text = "".join(getattr(p, "content", "") for p in response.parts if type(p).__name__ == "TextPart")
        report["result"] = {
            "gateway_accepted_model": True,
            "category": "success",
            "response_text": text,
            "response_model_name": response.model_name,
            "finish_reason": getattr(response, "finish_reason", None),
            "provider_response_id": getattr(response, "provider_response_id", None),
            "usage": {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens,
                      "source": "provider-reported, via PydanticAI"},
        }
        try:
            report["result"]["response_json_valid"] = isinstance(json.loads(text), dict)
        except (json.JSONDecodeError, TypeError):
            report["result"]["response_json_valid"] = False
        code = 0
    except Exception as exc:                                       # recorded, never retried
        status = getattr(exc, "status_code", None)
        body = getattr(exc, "body", None)
        report["result"] = {
            "gateway_accepted_model": False if status in (400, 404, 422) else None,
            "category": type(exc).__name__,
            "http_status": status,
            "message": str(exc)[:500],
            "error_body": (json.dumps(body)[:800] if body is not None else None),
        }
        code = 1
    report["duration_seconds"] = round(time.monotonic() - started, 2)
    after = {f: sha(HW5 / f) for f in WATCHED}
    report["project_files_unchanged"] = before == after
    report["project_file_hashes"] = after

    text = json.dumps(report, indent=2, ensure_ascii=False)
    if cfg.api_key in text:
        print("Report would contain the API key; refusing to write it.", file=sys.stderr)
        return 3
    (HW5 / "output" / "p5_live_connectivity.json").write_text(text + "\n", encoding="utf-8")
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
