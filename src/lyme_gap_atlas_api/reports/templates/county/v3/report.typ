#let report = json("input.json")
#let brief = report.at("brief")
#let geography = report.at("geography")
#let trace = brief.at("traceability")

#let navy = rgb("16375f")
#let teal = rgb("147a86")
#let ink = rgb("1c2b33")
#let muted = rgb("5c6c74")
#let rule = rgb("d5e2e8")
#let pale = rgb("e8f4fb")
#let white = rgb("ffffff")

#show "Borrelia burgdorferi": set text(style: "italic")
#show "Ixodes scapularis": set text(style: "italic")
#show "Ixodes pacificus": set text(style: "italic")

#set document(
  title: brief.at("county_heading") + " County Data Brief",
  author: "One Health Lyme Gap Atlas",
  description: "Published county surveillance data and suggested follow-up.",
)
#set text(font: ("Liberation Sans",), size: 10pt, fill: ink, lang: "en", hyphenate: false)
#set par(leading: 0.52em, justify: false, spacing: 0.58em)

#show heading.where(level: 1): it => {
  set text(size: 22pt, weight: "bold", fill: navy, hyphenate: false)
  set par(leading: 0.2em)
  block(above: 0pt, below: 8pt, it.body)
}
#show heading.where(level: 2): it => {
  set text(size: 12.5pt, weight: "bold", fill: navy, hyphenate: false)
  block(above: 12pt, below: 4pt, it.body)
}

#let footer-place = upper(geography.at("name")) + ", " + upper(geography.at("state_code"))
#let response-color = lower(brief.at("response_color"))
#let pill-ink = rgb("10212b")
#let score-known = brief.at("score_label") != "Unavailable"

#let lines-with(prefix) = trace.filter(item => item.starts-with(prefix))
#let first-with(prefix) = {
  let matched = lines-with(prefix)
  if matched.len() > 0 { matched.first() } else { "" }
}
#let after-label(prefix) = {
  let line = first-with(prefix)
  if line == "" { "" } else { line.slice(prefix.len()) }
}

#let numbered-rows(rows, label-width) = {
  for (index, row) in rows.enumerate() {
    block(
      width: 100%,
      inset: (top: 5pt, bottom: 5pt),
      stroke: if index + 1 < rows.len() { (bottom: 0.5pt + rule) } else { none },
    )[
      #grid(
        columns: (16pt, label-width, 1fr),
        column-gutter: 8pt,
        align: top,
        text(size: 10.5pt, fill: teal, weight: "bold")[#row.at("number")],
        text(size: 10pt, fill: navy, weight: "bold")[#row.at("label")],
        text(size: 10pt)[#row.at("text")],
      )
    ]
  }
}

#let question-rows(rows) = {
  for row in rows {
    block(width: 100%, above: 0pt, below: 3pt)[
      #grid(
        columns: (16pt, 1fr),
        column-gutter: 8pt,
        align: top,
        text(size: 10.5pt, fill: teal, weight: "bold")[#row.at("number")],
        text(size: 10pt)[#row.at("text")],
      )
    ]
  }
}

#set page(
  paper: "us-letter",
  margin: (top: 0.64in, bottom: 0.5in, left: 0.64in, right: 0.64in),
  header: {
    set text(size: 8pt, fill: navy, weight: "bold", tracking: 0.05em, hyphenate: false)
    block(width: 100%, inset: (bottom: 5pt), stroke: (bottom: 2pt + teal))[
      #grid(
        columns: (1fr, auto),
        column-gutter: 12pt,
        align(left + horizon)[#brief.at("branding")],
        align(right + horizon)[#brief.at("report_title")],
      )
    ]
  },
  header-ascent: 30%,
  footer: context {
    set text(size: 7pt, fill: muted, tracking: 0.02em, hyphenate: false)
    let current = counter(page).get().first()
    let total = counter(page).final().first()
    block(width: 100%, inset: (top: 3pt), stroke: (top: 0.6pt + rule))[
      #grid(
        columns: (1fr, auto),
        column-gutter: 12pt,
        align(left + horizon)[#brief.at("branding") | #footer-place],
        align(right + horizon)[#current / #total],
      )
    ]
  },
)

= #brief.at("county_heading")

#block(width: 100%, above: 1pt, below: 2pt)[
  #grid(
    columns: (1fr, auto),
    column-gutter: 16pt,
    align(left + horizon)[
      #box(
        fill: rgb(response-color.trim("#")),
        inset: (x: 11pt, y: 5pt),
        radius: 3pt,
      )[#text(size: 10.5pt, fill: pill-ink, weight: "bold", hyphenate: false)[#brief.at("response_title")]]
    ],
    align(right + horizon)[
      #text(size: 8.5pt, fill: muted)[Gap review score]
      #h(7pt)
      #if score-known [
        #text(size: 17pt, fill: navy, weight: "bold")[#brief.at("score_label")]
        #h(3pt)
        #text(size: 10pt, fill: muted)[#"/ 100"]
      ] else [
        #text(size: 13pt, fill: navy, weight: "bold")[Unavailable]
      ]
    ],
  )
]

== Why it surfaced
#text(size: 10pt)[#brief.at("why_it_surfaced")]

== County data: Six review factors
#text(size: 9.5pt, fill: muted)[#brief.at("factors_intro")]
#v(2pt)
#numbered-rows(brief.at("categories"), 1.55in)

#v(8pt)
#block(width: 100%, fill: pale, inset: (x: 11pt, y: 9pt), radius: 2pt)[
  #text(size: 11pt, fill: navy, weight: "bold")[Interpreting the data]
  #v(2pt)
  #text(size: 9.5pt)[#brief.at("interpretation")]
]

#v(6pt)
#text(size: 8.5pt, fill: muted)[
  #lines-with("Observation periods:").join()
]

#pagebreak()

#block(width: 100%, fill: pale, inset: (x: 11pt, y: 9pt), radius: 2pt)[
  #heading(level: 2)[Suggested follow-up]
  #text(size: 10pt)[#brief.at("posture")]
]

#v(4pt)
#numbered-rows(brief.at("workstreams"), 1.7in)

== Questions before acting
#question-rows(brief.at("questions"))

#block(width: 100%, fill: pale, inset: (x: 11pt, y: 7pt), radius: 2pt)[
#heading(level: 2)[Data sources and more information]
#text(size: 9pt, fill: muted)[
  Definitions, data sources, and methods are available on the Atlas website.
]
#v(3pt)
#text(size: 9.5pt, fill: teal, hyphenate: false)[
  #let pieces = ()
  #for (index, item) in brief.at("resources").enumerate() {
    if index > 0 {
      pieces.push(text(fill: muted)[ | ])
    }
    pieces.push(link(item.at("url"))[#item.at("label")])
  }
  #pieces.join()
]
]

#v(10pt)
#block(width: 100%, inset: (top: 7pt), stroke: (top: 0.6pt + rule))[
  #grid(
    columns: (1.2fr, 0.95fr, 1.15fr),
    column-gutter: 14pt,
    [
      #text(size: 8.5pt, fill: navy, weight: "bold")[Document details]
      #v(2pt)
      #text(size: 8pt)[#first-with("County: ")]
      #linebreak()
      #text(size: 8pt)[#first-with("County identifier (FIPS): ")]
      #linebreak()
      #text(size: 8pt)[#first-with("Published gap review score: ")]
      #linebreak()
      #text(size: 8pt)[#first-with("Review category: ")]
    ],
    [
      #text(size: 8.5pt, fill: navy, weight: "bold")[Data reference years]
      #v(2pt)
      #text(size: 8pt)[#first-with("Lyme case records: ")]
      #linebreak()
      #text(size: 8pt)[#first-with("Social vulnerability: ")]
      #linebreak()
      #text(size: 8pt)[#first-with("Rurality: ")]
    ],
    [
      #text(size: 8.5pt, fill: navy, weight: "bold")[Document information]
      #v(2pt)
      #text(size: 8pt)[#first-with("Report date: ")]
      #linebreak()
      #text(size: 8pt)[#first-with("Template version: ")]
      #linebreak()
      #text(size: 8pt)[#first-with("Website: ")]
      #linebreak()
      #text(size: 8pt)[#first-with("Data release: ")]
    ],
  )
  #v(5pt)
  #text(size: 7.5pt, fill: muted)[#first-with("Data methodology: ")]
  #linebreak()
  #text(size: 7.5pt, fill: muted)[#first-with("Score methodology: ")]
  #linebreak()
  #text(size: 7.5pt, fill: muted)[#first-with("Generated: ")]
  #linebreak()
  #text(size: 7.5pt, fill: muted)[#first-with("Source vintages: ")]
  #linebreak()
  #text(size: 7.5pt, fill: muted)[#first-with("Published release only.")]
  #linebreak()
  #text(size: 7.5pt, fill: muted)[#first-with("Copy: ")]
]
