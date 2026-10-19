#let report = json("input.json")
#let brief = report.at("brief")
#let geography = report.at("geography")

#let teal = rgb("0f7782")
#let teal-dark = rgb("0b5c64")
#let navy = rgb("0c2c4d")
#let ink = rgb("1c2b33")
#let muted = rgb("5c6c74")
#let rule = rgb("d7e3e6")
#let mint = rgb("e7f4f3")
#let mint-bar = rgb("1a8a80")
#let amber = rgb("fbf6ea")
#let amber-bar = rgb("d9923a")
#let row-tint = rgb("f3f8f8")

#show "Borrelia burgdorferi": set text(style: "italic")
#show "Ixodes scapularis": set text(style: "italic")
#show "Ixodes pacificus": set text(style: "italic")

#set document(
  title: brief.at("county_heading") + " County Data Brief",
  author: "One Health Lyme Gap Atlas",
  description: "Published county surveillance data and suggested follow-up.",
)
#set text(font: "Libertinus Serif", size: 8.5pt, fill: ink, lang: "en", hyphenate: false)
#set par(leading: 0.42em, justify: false, spacing: 0.45em)

#show heading.where(level: 1): it => {
  set text(size: 20pt, weight: "bold", fill: navy, hyphenate: false)
  set par(leading: 0.22em)
  block(above: 1pt, below: 1pt, it.body)
}
#show heading.where(level: 2): it => {
  set text(size: 11pt, weight: "bold", fill: navy, hyphenate: false)
  block(above: 9pt, below: 3pt, it.body)
}

#let footer-place = upper(geography.at("name")) + ", " + upper(geography.at("state_code"))

#let kicker(body) = text(size: 7.5pt, fill: muted, weight: "regular", tracking: 0.01em, hyphenate: false, body)

#let callout(fill, accent, title, body) = block(
  width: 100%,
  fill: fill,
  inset: (left: 10pt, right: 9pt, y: 7pt),
  stroke: (left: 3.25pt + accent),
  above: 7pt,
  below: 2pt,
)[
  #text(size: 8.3pt, fill: navy, weight: "bold")[#title]
  #h(5pt)
  #text(size: 8.2pt)[#body]
]

#let factor(row, show-label) = block(
  width: 100%,
  inset: (top: 4.5pt, bottom: 4pt),
  stroke: (bottom: 0.45pt + rule),
)[
  #grid(
    columns: (20pt, 1fr),
    column-gutter: 6pt,
    text(size: 8pt, fill: teal, weight: "bold")[#row.at("number")],
    [
      #if show-label [
        #text(size: 8.4pt, fill: navy, weight: "bold")[#row.at("label")]
        #linebreak()
      ]
      #text(size: 8.15pt)[#row.at("text")]
    ],
  )
]

#set page(
  paper: "us-letter",
  margin: (top: 0.72in, bottom: 0.46in, x: 0.58in),
  background: context {
    let bar-width = page.width - 1.16in
    place(
      top + left,
      dx: 0.58in,
      dy: 0.22in,
      rect(width: bar-width, height: 6.5pt, radius: 2pt, fill: teal),
    )
  },
  header: align(
    left + bottom,
    text(size: 7.4pt, fill: teal-dark, weight: "bold", tracking: 0.07em, brief.at("branding")),
  ),
  header-ascent: 38%,
  footer: context {
    set text(size: 6.6pt, fill: muted, tracking: 0.015em, hyphenate: false)
    let current = counter(page).get().first()
    let total = counter(page).final().first()
    block(width: 100%, inset: (top: 3pt), stroke: (top: 0.6pt + rule))[
      #grid(
        columns: (1fr, auto),
        column-gutter: 10pt,
        align(left + horizon)[#brief.at("branding") | #footer-place],
        align(right + horizon)[#current / #total],
      )
    ]
  },
)

= #brief.at("county_heading")
#kicker[
  #brief.at("report_title")
  |
  Published response: #brief.at("response_title")
  |
  Score #brief.at("score_label")
  |
  FIPS #brief.at("fips")
]

#callout(mint, mint-bar, [Why it surfaced], brief.at("why_it_surfaced"))

== County data / Six review factors
#for row in brief.at("categories") [
  #factor(row, true)
]

#callout(amber, amber-bar, [Interpreting the data], brief.at("interpretation"))

#v(6pt)
#text(size: 7pt, fill: muted)[
  #brief.at("traceability").filter(item => item.starts-with("Observation periods:")).join()
]

#pagebreak()

= #brief.at("response_title")
#kicker[
  #brief.at("report_title")
  |
  #brief.at("county_heading")
  |
  Published gap-score response
]

#callout(amber, amber-bar, [Recommended posture], brief.at("posture"))

== Five suggested follow-up workstreams
#table(
  columns: (1.82in, 1fr),
  inset: (x: 7pt, y: 4.2pt),
  stroke: (x, y) => if y < brief.at("workstreams").len() - 1 { (bottom: 0.7pt + white) } else { none },
  fill: row-tint,
  ..brief.at("workstreams").map(row => (
    text(size: 7.8pt, fill: navy, weight: "bold", hyphenate: false)[#row.at("label")],
    text(size: 7.8pt)[#row.at("text")],
  )).flatten(),
)

== Questions before acting
#for row in brief.at("questions") [
  #block(width: 100%, above: 1.5pt, below: 1.5pt)[
    #grid(
      columns: (20pt, 1fr),
      column-gutter: 4pt,
      text(size: 8pt, fill: teal, weight: "bold")[#row.at("number")],
      text(size: 8.15pt)[#row.at("text")],
    )
  ]
]

== Official resources & traceability
#text(size: 7.7pt, fill: teal, hyphenate: false)[
  #let pieces = ()
  #for (index, item) in brief.at("resources").enumerate() {
    if index > 0 {
      pieces.push(text(fill: muted)[ | ])
    }
    pieces.push(link(item.at("url"))[#item.at("label")])
  }
  #pieces.join()
]

#v(6pt)
#block(width: 100%, inset: (top: 4pt), stroke: (top: 0.6pt + rule))[
  #text(size: 6.8pt, fill: muted)[
    #brief.at("traceability").join([\ ])
  ]
]
