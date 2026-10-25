"""Offline OpenAPI validation and format-independent review artifacts."""

import argparse
import difflib
import hashlib
import json
import re
import subprocess
import tomllib
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from openapi_schema_validator import OAS31Validator
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parents[1]
FILES = ("openapi.json", "first-party-openapi.json")
RULES_FILE = ROOT / "scripts/openapi_quality_rules.toml"
STYLE_GUIDE = "docs/api-writing-style.md"
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
    embedded_examples: list[tuple[dict[str, Any], Any]] = []

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
                    embedded_examples.append((wrapper, example))
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(document)
    validate(document)
    for wrapper, example in embedded_examples:
        OAS31Validator(wrapper).validate(example)
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


# --- Documentation-quality rules -------------------------------------------------
# Each rule is a small function returning Violations. Budgets, banned terms,
# exceptions and the operation-ID policy live in RULES_FILE. See
# docs/openapi-quality.md for the rule list and how to add an exception.

STYLE_LINKS = {
    "parameter-description": "#parameter-description",
    "schema-description": "#schema-description",
    "response-200-example": "#example",
    "error-description": "#error-description",
    "error-example": "#example",
    "description-length": "#sentence-rules",
    "paragraph-length": "#sentence-rules",
    "summary-length": "#operation-summary",
    "banned-term": "#banned-and-replace-list",
}
NON_PROSE_KEYS = {"value", "example", "default", "enum", "const", "x-codeSamples"}


@dataclass(frozen=True)
class Violation:
    rule: str
    location: str
    message: str

    def render(self) -> str:
        link = f"{STYLE_GUIDE}{STYLE_LINKS.get(self.rule, '')}"
        return f"[{self.rule}] {self.location}: {self.message} (see {link})"


def load_rules(path: Path = RULES_FILE) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _resolve(document: dict[str, Any], node: Any) -> Any:
    for _ in range(10):
        if not (isinstance(node, dict) and "$ref" in node):
            break
        target: Any = document
        for segment in node["$ref"][2:].split("/"):
            target = target[segment.replace("~1", "/").replace("~0", "~")]
        node = target
    return node


def _operations(document: dict[str, Any]) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    found = []
    for path, item in document["paths"].items():
        for method, operation in item.items():
            if method in METHODS:
                found.append((f"{method.upper()} {path}", operation, item))
    return found


def check_parameter_descriptions(document: dict[str, Any]) -> list[Violation]:
    out = []
    for label, operation, item in _operations(document):
        for raw in [*item.get("parameters", []), *operation.get("parameters", [])]:
            parameter = _resolve(document, raw)
            if not str(parameter.get("description", "")).strip():
                name = f"{parameter.get('in')} parameter {parameter.get('name')}"
                out.append(Violation("parameter-description", label, f"{name} has no description"))
    return out


def check_schema_descriptions(document: dict[str, Any]) -> list[Violation]:
    out = []
    for name, schema in document.get("components", {}).get("schemas", {}).items():
        if not str(schema.get("description", "")).strip():
            out.append(Violation("schema-description", f"schema {name}", "no description"))
        for prop, raw in schema.get("properties", {}).items():
            described = str(raw.get("description", "")).strip() or (
                "$ref" in raw and str(_resolve(document, raw).get("description", "")).strip()
            )
            if not described:
                out.append(
                    Violation("schema-description", f"schema {name}.{prop}", "no description")
                )
    return out


def _has_example(response: dict[str, Any]) -> bool:
    content = response.get("content", {})
    return any("examples" in media or "example" in media for media in content.values())


def check_response_200_examples(document: dict[str, Any]) -> list[Violation]:
    return [
        Violation("response-200-example", label, "response 200 has no example")
        for label, operation, _ in _operations(document)
        if not _has_example(operation["responses"].get("200", {}))
    ]


def check_error_responses(document: dict[str, Any]) -> list[Violation]:
    out = []
    for label, operation, _ in _operations(document):
        for code, raw in operation["responses"].items():
            if code.startswith("2"):
                continue
            response = _resolve(document, raw)
            where = f"{label} response {code}"
            if not str(response.get("description", "")).strip():
                out.append(Violation("error-description", where, "no description"))
            # A body-less response (for example 304 Not Modified) has nothing to show.
            if response.get("content") and not _has_example(response):
                out.append(Violation("error-example", where, "no example"))
    return out


_PROSE_SKIP = ("|", "#", "-", "*", "```")


def _paragraphs(text: str) -> list[str]:
    prose = []
    for block in re.split(r"\n\s*\n", text):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if lines and not lines[0].startswith(_PROSE_SKIP) and not re.match(r"\d+\.", lines[0]):
            prose.append(" ".join(lines))
    return prose


def count_sentences(paragraph: str) -> int:
    flat = re.sub(r"\b(e\.g|i\.e|etc|vs)\.", r"\1", paragraph)
    flat = re.sub(r"`[^`]*`", "X", flat)
    return len(re.findall(r"[.!?](?=\s|$)", flat))


def check_description_budgets(document: dict[str, Any], budgets: dict[str, int]) -> list[Violation]:
    out = []
    for label, operation, _ in _operations(document):
        summary = operation.get("summary", "")
        if len(summary) > budgets["max_summary_chars"]:
            out.append(
                Violation(
                    "summary-length",
                    label,
                    f"summary is {len(summary)} characters; budget {budgets['max_summary_chars']}",
                )
            )
        text = operation.get("description", "")
        if len(text) > budgets["max_description_chars"]:
            out.append(
                Violation(
                    "description-length",
                    label,
                    f"description is {len(text)} characters; "
                    f"budget {budgets['max_description_chars']}",
                )
            )
        for paragraph in _paragraphs(text):
            count = count_sentences(paragraph)
            if count > budgets["max_paragraph_sentences"]:
                out.append(
                    Violation(
                        "paragraph-length",
                        label,
                        f"paragraph has {count} sentences; budget "
                        f"{budgets['max_paragraph_sentences']}: {paragraph[:50]}...",
                    )
                )
    return out


def public_text(document: dict[str, Any]) -> list[tuple[str, str]]:
    """Return (location, text) for every summary/description string in the spec."""
    found: list[tuple[str, str]] = []

    def walk(node: Any, location: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if key in NON_PROSE_KEYS:
                    continue
                if key in {"summary", "description"} and isinstance(child, str):
                    found.append((location, child))
                else:
                    walk(child, f"{location}.{key}" if location else key)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{location}[{index}]")

    walk(document, "")
    return found


def check_banned_terms(document: dict[str, Any], banned: list[dict[str, str]]) -> list[Violation]:
    out = []
    for location, text in public_text(document):
        # Code font marks an exact contract value (a header or enum), which the
        # style guide keeps verbatim; only the surrounding prose is checked.
        text = re.sub(r"`[^`]*`", "", text)
        for term in banned:
            match = re.search(term["pattern"], text, re.IGNORECASE)
            if match:
                out.append(
                    Violation(
                        "banned-term",
                        location,
                        f"banned term {match.group(0).strip()!r}; write: {term['replace']}",
                    )
                )
    return out


def check_operation_id_fixture(
    document: dict[str, Any], fixture: dict[str, Any], name: str
) -> list[Violation]:
    """Operation IDs must equal the reviewed fixture; renames need a recorded decision."""
    actual = {
        path: {m: op["operationId"] for m, op in item.items() if m in METHODS}
        for path, item in document["paths"].items()
    }
    expected = {
        path: methods if isinstance(methods, dict) else {"get": methods}
        for path, methods in fixture.items()
    }
    if actual == expected:
        return []
    return [Violation("operation-id-fixture", name, "operation IDs differ from reviewed fixture")]


def check_operation_id_naming(document: dict[str, Any], pattern: str) -> list[Violation]:
    return [
        Violation(
            "operation-id-naming",
            label,
            f"operationId {operation['operationId']!r} does not match {pattern}",
        )
        for label, operation, _ in _operations(document)
        if not re.fullmatch(pattern, operation["operationId"])
    ]


def apply_exceptions(
    violations: list[Violation], exceptions: list[dict[str, str]]
) -> list[Violation]:
    """Drop excepted findings; reject unjustified or stale exceptions."""
    remaining = list(violations)
    problems: list[Violation] = []
    for exception in exceptions:
        if not str(exception.get("justification", "")).strip():
            problems.append(
                Violation("exception", str(exception.get("location")), "exception has no reason")
            )
            continue
        matched = [
            v
            for v in remaining
            if v.rule == exception["rule"] and fnmatchcase(v.location, exception["location"])
        ]
        if not matched:
            problems.append(
                Violation(
                    "exception",
                    exception["location"],
                    f"exception for {exception['rule']} matches nothing; remove it",
                )
            )
        remaining = [v for v in remaining if v not in matched]
    return [*remaining, *problems]


def check_documentation_quality(
    document: dict[str, Any], rules: dict[str, Any]
) -> tuple[list[Violation], list[Violation]]:
    """Run every public documentation rule; return (failures, advisories)."""
    found = [
        *check_parameter_descriptions(document),
        *check_schema_descriptions(document),
        *check_response_200_examples(document),
        *check_error_responses(document),
        *check_description_budgets(document, rules["budgets"]),
        *check_banned_terms(document, rules.get("banned_terms", [])),
    ]
    ids = rules["operation_ids"]
    fixture = json.loads((ROOT / "tests/fixtures/public-operation-ids.json").read_text())
    found += check_operation_id_fixture(document, fixture, "openapi.json")
    advisories: list[Violation] = []
    mode = ids["naming_convention"]
    if mode in {"advisory", "enforce"}:
        naming = check_operation_id_naming(document, ids["naming_pattern"])
        if mode == "enforce":
            found += naming
        else:
            advisories = naming
    return apply_exceptions(found, rules.get("exceptions", [])), advisories


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
        rules = load_rules()
        if name == "first-party-openapi.json":
            ids_file = ROOT / "tests/fixtures/first-party-operation-ids.json"
            stale = check_operation_id_fixture(candidate, json.loads(ids_file.read_text()), name)
            if stale:
                raise ValueError("\n".join(v.render() for v in stale))
            id_rules = rules["operation_ids"]
            if id_rules["naming_convention"] == "enforce":
                naming = check_operation_id_naming(
                    candidate, id_rules["first_party_naming_pattern"]
                )
                if naming:
                    raise ValueError("\n".join(v.render() for v in naming))
        if name == "openapi.json":
            failures, advisories = check_documentation_quality(candidate, rules)
            for advisory in advisories:
                print(f"advisory {advisory.render()}")
            if failures:
                raise ValueError(
                    "Documentation quality failures:\n" + "\n".join(f.render() for f in failures)
                )
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
