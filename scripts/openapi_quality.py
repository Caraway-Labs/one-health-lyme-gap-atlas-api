"""Offline OpenAPI validation and format-independent review artifacts."""

import argparse
import difflib
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from openapi_schema_validator import OAS31Validator
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parents[1]
FILES = ("openapi.json", "first-party-openapi.json")
METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}


def canonical(document: dict[str, Any]) -> str:
    return json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def semantic_diff(before: dict[str, Any], after: dict[str, Any], name: str) -> str:
    return "".join(
        difflib.unified_diff(
            canonical(before).splitlines(keepends=True),
            canonical(after).splitlines(keepends=True),
            fromfile=f"base/{name}",
            tofile=f"candidate/{name}",
        )
    )


def validate_contract(document: dict[str, Any], *, public: bool) -> None:
    # Reject remote refs before validation: this gate must never fetch infrastructure.
    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if "$ref" in value:
                ref = value["$ref"]
                if not isinstance(ref, str) or not ref.startswith("#/"):
                    raise ValueError("Only local OpenAPI references are supported")
                target: Any = document
                for segment in ref[2:].split("/"):
                    target = target[segment.replace("~1", "/").replace("~0", "~")]
            for item in value.values():
                visit(item)
            if "schema" in value and ("examples" in value or "example" in value):
                wrapper = {
                    "$ref": "#/$defs/example",
                    "$defs": {"example": value["schema"]},
                    "components": document.get("components", {}),
                }
                examples = [
                    item["value"] for item in value.get("examples", {}).values() if "value" in item
                ]
                if "example" in value:
                    examples.append(value["example"])
                for example in examples:
                    OAS31Validator(wrapper).validate(example)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(document)
    validate(document)
    ids: set[str] = set()
    tags = {tag["name"] for tag in document.get("tags", [])}
    if public:
        expected = json.loads((ROOT / "tests/fixtures/public-operation-ids.json").read_text())
        if set(document["paths"]) != set(expected):
            raise ValueError("Public path set differs from approved contract")
        for field in ("title", "version", "summary", "description", "contact"):
            if not document["info"].get(field):
                raise ValueError(f"Missing public info.{field}")
        if document.get("security") or document.get("components", {}).get("securitySchemes"):
            raise ValueError("Anonymous public contract must not require security")
    for path, item in document["paths"].items():
        for method, operation in item.items():
            if method not in METHODS:
                continue
            operation_id = operation.get("operationId", "")
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", operation_id) or operation_id in ids:
                raise ValueError(f"Missing, invalid or duplicate operationId at {method} {path}")
            ids.add(operation_id)
            if public:
                if method != "get" or operation_id != expected[path]:
                    raise ValueError(f"Unapproved public operation at {method} {path}")
                for field in ("summary", "description", "tags"):
                    if not operation.get(field):
                        raise ValueError(f"Missing {field} at {path}")
                if not set(operation["tags"]) <= tags or operation.get("security"):
                    raise ValueError(f"Invalid public tags/security at {path}")
                errors = [
                    response
                    for code, response in operation["responses"].items()
                    if code.startswith(("4", "5"))
                ]
                if not errors or any(not error.get("description") for error in errors):
                    raise ValueError(f"Missing documented errors at {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", help="Reviewed base commit for deterministic semantic diff")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "build/openapi")
    args = parser.parse_args()
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name in FILES:
        candidate = json.loads((ROOT / name).read_text())
        validate_contract(candidate, public=name == "openapi.json")
        committed = json.loads(
            subprocess.check_output(
                ["git", "show", f"HEAD:{name}"],
                cwd=ROOT,
                text=True,
            )
        )
        drift = semantic_diff(committed, candidate, name)
        if drift:
            raise ValueError(f"Regenerated contract drift; update {name}:\n{drift}")
        content = canonical(candidate)
        (output / name).write_text(content, encoding="utf-8", newline="\n")
        hashes[name] = hashlib.sha256(content.encode()).hexdigest()
        if args.base_ref:
            base = json.loads(
                subprocess.check_output(
                    ["git", "show", f"{args.base_ref}:{name}"],
                    cwd=ROOT,
                    text=True,
                )
            )
            diff = semantic_diff(base, candidate, name)
            (output / f"{name}.diff").write_text(diff or "No semantic change.\n", encoding="utf-8")
    manifest = {
        "sha256": hashes,
        "base_ref": args.base_ref,
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
        ).strip(),
    }
    (output / "manifest.json").write_text(canonical(manifest), encoding="utf-8")
    print("Validated public and first-party OpenAPI; semantic drift checks passed.")


if __name__ == "__main__":
    main()
