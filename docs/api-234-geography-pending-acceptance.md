# API #234 canonical geography acceptance evidence

The API implementation already treats canonical geography identity delivery
as pending. For supported county and state FIPS keys,
`GET /v1/geographies/{geography_type}/{geography_id}` responds with HTTP 503,
`application/problem+json`, stable code `CANONICAL_DATA_UNAVAILABLE`, and a
request ID shared by the response body and `X-Request-ID`. It returns no data
or geometry. Invalid identity keys remain client errors. County display
geometry continues to use its existing separate endpoint and representation.

`tests/test_public_contract.py::test_valid_canonical_geographies_remain_structurally_unavailable`
checks representative county keys in Alabama and Connecticut, the Connecticut
Capitol Planning Region county-equivalent (`09110`), and a Connecticut state
key. The route's county validator accepts this five-digit FIPS shape; no new
geography type is needed. The Census identifies `09110` as Capitol Planning
Region in its [Connecticut county-equivalent listing](https://tigerweb.geo.census.gov/tigerwebmain/Files/acs24/tigerweb_acs24_county_2024_bas24_ct.html).
These application tests establish origin behavior only.

Public-ingress acceptance remains blocked by the shared API #237 issue:
production ingress has been observed replacing origin 503 responses with HTML
504 responses. API #237's exact reviewed head is the base for this work and
contains strict ingress-fidelity probes, but the provider has not confirmed a
supported pass-through control and no topology/contract decision has been
approved. This change does not claim production acceptance, does not fabricate
canonical geography data, and does not convert operational failure into empty
data. Close the public-ingress criterion only after provider/owner resolution
and read-only checks across the required public hosts and geographies.

Focused local verification:

```sh
uv run pytest -q tests/test_public_contract.py tests/test_public_openapi_metadata.py tests/test_public_observability.py
```
