"""Opt-in paid, fixed-evidence comparison; never a production acceptance test.

OPENAI_API_KEY must be process-local. Input is a local JSON list of cases with
case_id, question and Evidence dictionaries. Do not commit private inputs.
Results contain only timings, counts, fixed case IDs and allowlisted outcomes.
At most 12 turns / 24 provider calls; no Snowflake, graph or conversation writes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path
from typing import Any

from openai import OpenAI

from lyme_gap_atlas_api import knowledge_chat as current

ROOT = Path(__file__).resolve().parents[1]


def baseline_module(ref: str) -> types.ModuleType:
    source = subprocess.run(
        ["git", "show", f"{ref}:src/lyme_gap_atlas_api/knowledge_chat.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    module = types.ModuleType("lyme_gap_atlas_api._generation_benchmark_baseline")
    sys.modules[module.__name__] = module
    exec(compile(source, f"{ref}:knowledge_chat.py", "exec"), module.__dict__)
    return module


class MeasuredResponses:
    def __init__(self, client: OpenAI) -> None:
        self.client = client
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        started = time.perf_counter()
        call: dict[str, Any] = {"input_characters": len(kwargs["input"]), "outcome": "error"}
        self.calls.append(call)
        try:
            response = self.client.with_options(max_retries=0).responses.create(**kwargs)
            usage = response.usage
            call.update(
                {
                    "outcome": response.status,
                    "input_token_count": usage.input_tokens if usage else None,
                    "output_token_count": usage.output_tokens if usage else None,
                    "reasoning_token_count": (
                        usage.output_tokens_details.reasoning_tokens if usage else None
                    ),
                    "output_characters": len(response.output_text),
                }
            )
            return response
        finally:
            call["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)


class MeasuredClient:
    def __init__(self, client: OpenAI) -> None:
        self.responses = MeasuredResponses(client)

    def with_options(self, *, max_retries: int) -> MeasuredClient:
        assert max_retries == 0
        return self


def turn(module: Any, answerer: Any, case: dict[str, Any]) -> dict[str, Any]:
    evidence = [module.Evidence(**item) for item in case["evidence"]]
    started = time.monotonic()
    elapsed_start = time.perf_counter()
    deadline = started + 28
    correction: bool | str = False
    row: dict[str, Any] = {
        "outcome": "failed",
        "first_pass_valid": False,
        "attempts": 0,
        "citation_count": 0,
        "validation": [],
    }
    for attempt in (1, 2):
        remaining = deadline - time.monotonic()
        if remaining < 8:
            row["outcome"] = "deadline_exhausted"
            break
        row["attempts"] = attempt
        try:
            generated = answerer.answer(
                case["question"],
                evidence,
                "atlas-generation-benchmark",
                timeout_seconds=min(16, remaining - 5),
                correction=correction,
            )
            claims, citations = module._validate_grounding(generated, evidence)
            module._validate_evidence_state(generated, citations)
        except ValueError as exc:
            reason = module._grounding_reason(exc)
            row["validation"].append(reason)
            correction = True if module is not current else reason
            continue
        except Exception as exc:
            # Never publish provider payloads or unrestricted exception messages.
            row["outcome"] = (
                "generation_timeout"
                if isinstance(exc, TimeoutError) or type(exc).__name__ == "APITimeoutError"
                else "provider_failure"
            )
            row["provider_error_type"] = type(exc).__name__
            row["provider_status"] = getattr(exc, "status_code", None)
            break
        row.update(
            {
                "outcome": "answered",
                "first_pass_valid": attempt == 1,
                "citation_count": len(citations),
                "claim_count": len(claims),
            }
        )
        row["validation"].append("passed")
        break
    row["total_ms"] = round((time.perf_counter() - elapsed_start) * 1000, 2)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--baseline-ref", default="3e95c3091ff3559061ee9a3c09bfe40ff4cbcdd7")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, choices=(1, 2), default=2)
    parser.add_argument("--comparison", choices=("effort", "models", "selected"), default="effort")
    parser.add_argument("--live", action="store_true", help="Authorize bounded paid provider calls")
    args = parser.parse_args()
    raw = args.evidence.read_bytes()
    cases = json.loads(raw)
    if not isinstance(cases, list) or not 1 <= len(cases) <= 2:
        parser.error("Supply one or two fixed evidence cases")
    if not args.live:
        parser.error("This benchmark requires explicit --live; normal tests make no paid calls")
    if not os.environ.get("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY is not configured")
    baseline = baseline_module(args.baseline_ref)
    results: dict[str, Any] = {
        "baseline_ref": args.baseline_ref,
        "evidence_sha256": hashlib.sha256(raw).hexdigest(),
        "scope": "live provider generation on fixed published evidence; not production retrieval",
        "comparison": args.comparison,
        "implementation_sha256": hashlib.sha256(Path(current.__file__).read_bytes()).hexdigest(),
        "rows": [],
    }
    # Share one connection pool across variants; rotate ordering to reduce order bias.
    with OpenAI(max_retries=0) as client:
        variants = [
            ("baseline-low", baseline, "low", "gpt-5.6-luna"),
            ("candidate-low", current, "low", "gpt-5.6-luna"),
            ("candidate-none", current, "none", "gpt-5.6-luna"),
        ]
        if args.comparison == "models":
            variants = [
                ("gpt-6-luna-none", current, "none", "gpt-6-luna"),
                ("gpt-5.6-terra-none", current, "none", "gpt-5.6-terra"),
            ]
        elif args.comparison == "selected":
            variants = [("selected-default", current, "none", "gpt-5.6-luna")]
        for repeat in range(args.repeats):
            for case_index, case in enumerate(cases):
                offset = (repeat + case_index) % len(variants)
                for name, module, effort, model in variants[offset:] + variants[:offset]:
                    measured = MeasuredClient(client)
                    options = {} if module is baseline else {"reasoning_effort": effort}
                    answerer = module.OpenAIAnswerer(measured, model=model, **options)
                    row = turn(module, answerer, case)
                    row.update(
                        {
                            "case_id": case["case_id"],
                            "repeat": repeat + 1,
                            "variant": name,
                            "model": model,
                            "calls": measured.responses.calls,
                        }
                    )
                    results["rows"].append(row)
                    args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
                    print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
