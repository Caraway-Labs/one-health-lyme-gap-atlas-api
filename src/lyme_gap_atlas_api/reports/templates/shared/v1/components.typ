#import "tokens.typ": *

#let setup-document(report, body) = {
  set page(
    paper: "us-letter",
    margin: (x: 0.7in, y: 0.65in),
    footer: context align(center)[
      #text(size: 8pt, fill: muted)[
        Lyme Gap Atlas · #report.at("identity").at("template_version") · Page #counter(page).display("1")
      ]
    ],
  )
  set text(size: 10pt, fill: charcoal)
  set par(leading: 0.55em)
  set heading(numbering: none, outlined: true)
  show heading.where(level: 1): set text(size: 20pt, weight: "bold", fill: navy)
  show heading.where(level: 2): set text(size: 14pt, weight: "bold", fill: navy)
  body
}

#let metric-value(metric) = {
  if metric.at("availability") == "unavailable" {
    text(style: "italic", fill: muted)[Data unavailable]
  } else {
    str(metric.at("value"))
  }
}

#let metric-table(metrics) = table(
  columns: (2fr, 1fr),
  inset: 6pt,
  stroke: (x, y) => if y == 0 { (bottom: 1pt + navy) } else { (bottom: 0.5pt + rule) },
  table.header(
    text(weight: "bold", fill: navy)[Measure],
    text(weight: "bold", fill: navy)[Value],
  ),
  ..metrics.map(metric => (metric.at("label"), metric-value(metric))).flatten(),
)

#let report-header(report, report-title) = [
  = #report-title
  #text(size: 13pt, weight: "bold")[#report.at("geography").at("name")]
  #text(fill: muted)[
    Geography identifier: #report.at("geography").at("identifier") ·
    Dataset: #report.at("provenance").at("dataset_version") ·
    Generated: #report.at("identity").at("generated_at")
  ]
  v(#gap-large)
]

#let score-summary(score, label: "Atlas score") = table(
  columns: (1fr, 1fr, 1fr),
  inset: 8pt,
  fill: light-blue,
  stroke: none,
  text(weight: "bold", fill: navy)[#label],
  text(weight: "bold", fill: navy)[Human weakness],
  text(weight: "bold", fill: navy)[Ecological],
  str(score.at("score")),
  str(score.at("human_weakness")),
  str(score.at("ecological")),
)

#let callout(title, body) = block(
  width: 100%,
  fill: light-blue,
  inset: 9pt,
  radius: 2pt,
)[
  #text(weight: "bold", fill: navy)[#title]
  #parbreak()
  #body
]

#let provenance(report) = [
  == Methodology and provenance
  #callout("Interpretation", report.at("provenance").at("limitations"))
  v(#gap-medium)
  *Methodology version:* #report.at("provenance").at("methodology_version") \
  *Dataset version:* #report.at("provenance").at("dataset_version") \
  *Template version:* #report.at("identity").at("template_version")
  v(#gap-medium)
  #table(
    columns: (1.2fr, 1fr, 0.8fr),
    inset: 5pt,
    stroke: (x, y) => if y == 0 { (bottom: 1pt + navy) } else { (bottom: 0.5pt + rule) },
    table.header([*Source*], [*Vintage*], [*Notes*]),
    ..report.at("provenance").at("sources").map(source => (
      source.at("label"), source.at("vintage"), source.at("note"),
    )).flatten(),
  )
]
