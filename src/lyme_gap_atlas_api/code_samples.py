"""Generate `x-codeSamples` for the public OpenAPI document.

Every sample comes from one operation's path, its parameter examples, and the
`servers[0].url` of the document. Nothing is written per operation. The output
mirrors the HTTP client tabs in the Web documentation (curl, Python with httpx,
TypeScript with fetch, R with httr2).

The samples belong to the public document only. The first-party document is the
Web client's generation input and does not carry them.
"""

from typing import Any
from urllib.parse import quote

# Optional parameters that are still part of the sample. Required parameters
# always are. `year` keeps the observations request identical to the captured
# response example.
OPTIONAL_SAMPLE_PARAMETERS = frozenset({"year"})

# Language tabs, in the order Redoc shows them. `lang` selects syntax highlighting.
LANGUAGES = (("Shell", "curl"), ("Python", "Python"), ("TypeScript", "TypeScript"), ("R", "R"))

QueryPair = tuple[str, str]


def add_code_samples(schema: dict[str, Any]) -> None:
    """Attach `x-codeSamples` to every operation in `schema`, in place."""
    origin = schema["servers"][0]["url"].rstrip("/")
    for path, path_item in schema["paths"].items():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            operation["x-codeSamples"] = operation_samples(path, operation, origin)


def operation_samples(path: str, operation: dict[str, Any], origin: str) -> list[dict[str, str]]:
    """Return the sample list for one operation."""
    path_values, query = _sample_parameters(operation)
    resolved = path
    for name, value in path_values.items():
        resolved = resolved.replace("{" + name + "}", quote(value, safe=""))
    endpoint = origin + resolved
    kind = _response_kind(operation["responses"]["200"].get("content", {}))
    paged = "page_token" in {p["name"] for p in operation.get("parameters", [])}
    if kind == "pdf":
        sources = {"Shell": f"curl -fsS -o report.pdf {_shell(_url(endpoint, query))}"}
    elif paged:
        sources = {
            "Shell": _curl_paging(_url(endpoint, query)),
            "Python": _python_paging(endpoint, query),
            "TypeScript": _typescript_paging(endpoint, query),
            "R": _r_paging(endpoint, query),
        }
    else:
        sources = {
            "Shell": f"curl -fsS {_shell(_url(endpoint, query))}",
            "Python": _python(endpoint, query, kind),
            "TypeScript": _typescript(endpoint, query, kind),
            "R": _r(endpoint, query, kind),
        }
    return [
        {"lang": lang, "label": label, "source": sources[lang]}
        for lang, label in LANGUAGES
        if lang in sources
    ]


def _sample_parameters(operation: dict[str, Any]) -> tuple[dict[str, str], list[QueryPair]]:
    path_values: dict[str, str] = {}
    query: list[QueryPair] = []
    for parameter in operation.get("parameters", []):
        if parameter.get("deprecated") or parameter["schema"].get("deprecated"):
            continue
        if not parameter.get("required") and parameter["name"] not in OPTIONAL_SAMPLE_PARAMETERS:
            continue
        value = _example(parameter)
        if value is None:
            raise ValueError(f"Parameter {parameter['name']} has no example for a code sample")
        if parameter["in"] == "path":
            path_values[parameter["name"]] = str(value)
        elif parameter["in"] == "query":
            values = value if isinstance(value, list) else [value]
            # str() keeps strings as strings, so FIPS leading zeros survive.
            query.extend((parameter["name"], str(item)) for item in values)
    return path_values, query


def _example(parameter: dict[str, Any]) -> Any:
    examples = parameter.get("schema", {}).get("examples") or parameter.get("examples")
    if isinstance(examples, list) and examples:
        return examples[0]
    if isinstance(examples, dict) and examples:
        return next(iter(examples.values())).get("value")
    return None


def _response_kind(content: dict[str, Any]) -> str:
    if "application/pdf" in content:
        return "pdf"
    if "text/csv" in content:
        return "text"
    return "json"


def _url(endpoint: str, query: list[QueryPair]) -> str:
    if not query:
        return endpoint
    encoded = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in query)
    return f"{endpoint}?{encoded}"


def _shell(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _q(value: str) -> str:
    """Double-quoted literal that is valid in Python, TypeScript, and R."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _group(query: list[QueryPair]) -> list[tuple[str, list[str]]]:
    grouped: list[tuple[str, list[str]]] = []
    for name, value in query:
        for key, values in grouped:
            if key == name:
                values.append(value)
                break
        else:
            grouped.append((name, [value]))
    return grouped


def _repeated(query: list[QueryPair]) -> bool:
    names = [name for name, _ in query]
    return len(set(names)) != len(names)


def _r_name(name: str) -> str:
    return name if name.isidentifier() else f"`{name}`"


def _py_params(query: list[QueryPair], indent: int) -> str:
    pad = " " * indent
    lines = []
    for name, values in _group(query):
        rendered = _q(values[0]) if len(values) == 1 else "[" + ", ".join(map(_q, values)) + "]"
        lines.append(f"{pad}{_q(name)}: {rendered},")
    return "\n".join(lines)


def _r_entries(query: list[QueryPair], indent: int) -> list[str]:
    pad = " " * indent
    entries = []
    for name, values in _group(query):
        rendered = _q(values[0]) if len(values) == 1 else "c(" + ", ".join(map(_q, values)) + ")"
        entries.append(f"{pad}{_r_name(name)} = {rendered}")
    return entries


_TOKEN_SCRIPT = (
    "import json,sys,urllib.parse; "
    'token=json.load(sys.stdin)["meta"]["next_page_token"]; '
    'print("" if token is None else urllib.parse.quote(token, safe=""))'
)


def _curl_paging(url: str) -> str:
    joiner = "&" if "?" in url else "?"
    return f"""url={_shell(url)}
next_url="$url"
while [ -n "$next_url" ]; do
  page=$(curl -fsS "$next_url")
  printf '%s\\n' "$page"
  token=$(printf '%s' "$page" | python3 -c '{_TOKEN_SCRIPT}')
  if [ -n "$token" ]; then next_url="$url{joiner}page_token=$token"; else next_url=""; fi
done"""


def _python(endpoint: str, query: list[QueryPair], kind: str) -> str:
    params = _py_params(query, 8)
    params_arg = f"\n    params={{\n{params}\n    }}," if params else ""
    reader = "response.json()" if kind == "json" else "response.text"
    return f"""import httpx

response = httpx.get(
    {_q(endpoint)},{params_arg}
    timeout=30.0,
)
response.raise_for_status()
body = {reader}"""


def _py_filters(query: list[QueryPair]) -> str:
    return "{\n" + _py_params(query, 4) + "\n}" if query else "{}"


def _python_paging(endpoint: str, query: list[QueryPair]) -> str:
    return f"""import httpx

endpoint = {_q(endpoint)}
filters = {_py_filters(query)}
pages = []
with httpx.Client(timeout=30.0) as client:
    params = dict(filters)
    while True:
        response = client.get(endpoint, params=params)
        response.raise_for_status()
        body = response.json()
        pages.append(body)
        token = body["meta"]["next_page_token"]
        if token is None:
            break
        params = {{**filters, "page_token": token}}"""


def _typescript(endpoint: str, query: list[QueryPair], kind: str) -> str:
    appends = "".join(f"\nurl.searchParams.append({_q(k)}, {_q(v)});" for k, v in query)
    reader = "await response.json()" if kind == "json" else "await response.text()"
    declared = "unknown" if kind == "json" else "string"
    return f"""const url = new URL({_q(endpoint)});{appends}
const response = await fetch(url);
if (!response.ok) {{
  throw new Error(`Atlas HTTP ${{response.status}}`);
}}
const body: {declared} = {reader};"""


def _typescript_paging(endpoint: str, query: list[QueryPair]) -> str:
    rows = "".join(f"\n  [{_q(k)}, {_q(v)}]," for k, v in query)
    filters = rows + "\n" if rows else ""
    return f"""const endpoint = {_q(endpoint)};
const filters: [string, string][] = [{filters}];

type Page = {{ meta: {{ next_page_token: string | null }} }};

const pages: Page[] = [];
let pageToken: string | null = null;
do {{
  const url = new URL(endpoint);
  for (const [name, value] of filters) {{
    url.searchParams.append(name, value);
  }}
  if (pageToken !== null) {{
    url.searchParams.append("page_token", pageToken);
  }}
  const response = await fetch(url);
  if (!response.ok) {{
    throw new Error(`Atlas HTTP ${{response.status}}`);
  }}
  const page = (await response.json()) as Page;
  pages.push(page);
  pageToken = page.meta.next_page_token;
}} while (pageToken !== null);"""


def _r(endpoint: str, query: list[QueryPair], kind: str) -> str:
    steps = [f"request({_q(endpoint)})"]
    if query:
        entries = _r_entries(query, 4)
        if _repeated(query):
            entries.append('    .multi = "explode"')
        steps.append("req_url_query(\n" + ",\n".join(entries) + "\n  )")
    steps += ["req_timeout(30)", "req_perform()"]
    reader = "resp_body_json" if kind == "json" else "resp_body_string"
    pipeline = " |>\n  ".join(steps)
    return f"""library(httr2)

response <- {pipeline}
body <- {reader}(response)"""


def _r_paging(endpoint: str, query: list[QueryPair]) -> str:
    multi = ', .multi = "explode"' if _repeated(query) else ""
    rows = ",\n".join(_r_entries(query, 2))
    entries = f"\n{rows}\n" if rows else ""
    return f"""library(httr2)

endpoint <- {_q(endpoint)}
filters <- list({entries})

pages <- list()
page_token <- NULL
repeat {{
  response <- request(endpoint) |>
    req_url_query(!!!filters, page_token = page_token{multi}) |>
    req_timeout(30) |>
    req_perform()
  body <- resp_body_json(response)
  pages[[length(pages) + 1]] <- body
  page_token <- body$meta$next_page_token
  if (is.null(page_token)) break
}}"""
