# County identity, display geometry, and analysis geometry

County identity is the stable five-digit FIPS string, including leading zeros.
Names, coordinates, source vintage, and geometry representation are not join keys.
The canonical public `GeographyIdentity` and observation queries use this identity
independently of any polygon.

`GET /v1/atlas/geometry` retains its existing GeoJSON FeatureCollection: feature
`id` and `properties.fips` are county FIPS; `geometry` is the exact governed
`CURRENT_COUNTY_ATLAS_V.GEOMETRY_JSON` display projection. It comes from CDC/ATSDR
SVI 2022, generalized with `maxAllowableOffset=0.01`, in EPSG:4326. Coordinate
payload, dataset-version lookup, ETag, conditional 304, media type, and immutable
cache behavior remain unchanged. The canonical `/v1/geographies` resource retains
its documented unavailable behavior; this story adds no geometry endpoint.

Data #424 separately defines unsimplified 2025 Census TIGER/Line analysis polygons
in EPSG:4269, with equal-area transforms for grid weighting. They are internal
aggregation inputs and must never replace the display projection. The same FIPS
identifies both representations. A historical environmental observation aggregated
over the frozen 2025 vintage does not reconstruct historical legal boundaries.

Analytical provenance can travel through the existing observation `methodology_id`,
`methodology_version`, `methodology`, `provenance_ref`, and `limitations`, with
source and release lineage retained. Data #424 names the transform
`atlas-county-analysis-geometry/1` and weighting method
`atlas-grid-county-area-weight/1`; these are analytical method references, not
display dataset versions. Preserve the source's actual transformation version and
lineage rather than assigning one of these versions to unrelated observations.
The referenced governed provenance records retain artifact digests and geometry
version; neither observation nor identity payload needs an analytical polygon.
Any future public analysis-polygon surface requires a separate reviewed contract.

Source contracts: data `docs/contracts/county-identity-geometry/`
`county-identity-geometry-v1.md` and `county-analysis-geometry-v1.md`; workspace
ADR 0003. OpenAPI is the authoritative HTTP schema.
