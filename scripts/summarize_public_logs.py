"""Summarize App Platform JSON run logs from stdin without retaining request data."""

import json
import statistics
import sys

PUBLIC = (
    "/v1/indicators",
    "/v1/measures",
    "/v1/observations",
    "/v1/sources",
    "/v1/methodologies",
    "/v1/geographies",
)
DETAIL = ("/v1/indicators/", "/v1/measures/", "/v1/sources/", "/v1/methodologies/")


def main() -> None:
    requests = []
    conditional_hits = 0
    for line in sys.stdin:
        start = line.find("{")
        if start < 0:
            continue
        try:
            event = json.loads(line[start:])
        except json.JSONDecodeError:
            continue
        if event.get("message") == "public_read_conditional_hit":
            conditional_hits += 1
        if event.get("message") == "api_request_completed":
            context = event.get("context", {})
            if str(context.get("path", "")).startswith(PUBLIC):
                requests.append(context)
    durations = sorted(float(item["duration_ms"]) for item in requests)
    count = len(requests)
    report = {
        "requests": count,
        "server_errors": sum(int(item["status_code"]) >= 500 for item in requests),
        "rate_limit_rejections": sum(int(item["status_code"]) == 429 for item in requests),
        "conditional_detail_hits": conditional_hits,
        "detail_conditional_ratio": (
            round(
                sum(int(item["status_code"]) == 304 for item in requests)
                / sum(str(item["path"]).startswith(DETAIL) for item in requests),
                3,
            )
            if any(str(item["path"]).startswith(DETAIL) for item in requests)
            else None
        ),
        "median_ms": round(statistics.median(durations)) if durations else None,
        "p95_ms": durations[min(count - 1, int(0.95 * count))] if durations else None,
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
