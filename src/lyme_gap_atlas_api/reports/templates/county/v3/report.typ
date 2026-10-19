#let report = json("input.json")
#let brief = report.at("brief")

#let teal = rgb("0f7782")
#let teal-dark = rgb("075760")
#let navy = rgb("082a4d")
#let pale = rgb("e7f4f5")
#let ink = rgb("1b2830")
#let muted = rgb("5c6b73")
#let rule = rgb("d5e4e6")

#show "Borrelia burgdorferi": set text(style: "italic")
#show "Ixodes scapularis": set text(style: "italic")
#show "Ixodes pacificus": set text(style: "italic")

#set document(
  title: brief.at("county_heading") + " County Data Brief",
  author: "One Health Lyme Gap Atlas",
  description: "Published county surveillance data and suggested follow-up.",
)
#set text(font: "Libertinus Serif", size: 8.5pt, fill: ink, lang: "en", hyphenate: true)
#set par(leading: 0.42em, justify: false)

#show heading.where(level: 1): it => {
  set text(size: 16pt, weight: "bold", fill: navy)
  block(above: 1pt, below: 0pt, it.body)
}
#show heading.where(level: 2): it => {
  set text(size: 10.5pt, weight: "bold", fill: navy)
  block(above: 7pt, below: 2pt, it.body)
}

#let brand = text(size: 7.5pt, fill: teal-dark, weight: "bold", brief.at("branding"))

#let factor(row) = block(width: 100%, above: 2.5pt, below: 1.5pt)[
  #grid(
    columns: (16pt, 1fr),
    column-gutter: 4pt,
    text(size: 8pt, fill: teal-dark, weight: "bold")[#row.at("number")],
    [
      #if row.at("label") != "Question" [
        #text(size: 8.5pt, fill: navy, weight: "bold")[#row.at("label")]
        #linebreak()
      ]
      #text(size: 8.2pt)[#row.at("text")]
    ],
  )
]

#set page(
  paper: "us-letter",
  margin: (top: 0.52in, bottom: 0.36in, x: 0.52in),
  background: place(top, rect(width: 100%, height: 6pt, fill: teal)),
  header: align(left, brand),
  header-ascent: 42%,
  footer: context {
    set text(size: 7pt, fill: muted)
    let current = counter(page).get().first()
    let total = counter(page).final().first()
    grid(
      columns: (1fr, auto),
      align(left)[#brief.at("branding") · FIPS #brief.at("fips")],
      align(right)[#current / #total],
    )
  },
)

= #brief.at("county_heading")
#v(1pt)
#text(size: 8pt, fill: teal-dark, weight: "bold")[#brief.at("report_title")]
#v(3pt)
#text(size: 8.4pt)[
  FIPS #brief.at("fips") · Published gap score #brief.at("score_label") · #brief.at("response_title")
]

== Why it surfaced
#text(size: 8.3pt)[#brief.at("why_it_surfaced")]

#v(2pt)
#for row in brief.at("categories") [
  #factor(row)
]

#v(4pt)
#block(width: 100%, fill: pale, inset: 7pt, radius: 2pt)[
  #text(size: 8.4pt, fill: navy, weight: "bold")[Interpreting the data]
  #linebreak()
  #text(size: 8pt)[#brief.at("interpretation")]
]

#pagebreak()

= #brief.at("response_title")
#v(1pt)
#text(size: 8pt, fill: teal-dark, weight: "bold")[#brief.at("report_title")]
#h(6pt)
#text(size: 8.2pt, fill: muted)[#brief.at("county_heading") · Published gap-score response]

#v(5pt)
#block(width: 100%, fill: pale, inset: 6pt, radius: 2pt)[
  #text(size: 8.2pt, fill: navy, weight: "bold")[Recommended posture]
  #h(4pt)
  #text(size: 8.2pt)[#brief.at("posture")]
]

== Suggested follow-up
#table(
  columns: (1.85in, 1fr),
  inset: (x: 4pt, y: 3.2pt),
  stroke: (x, y) => (bottom: 0.4pt + rule),
  ..brief.at("workstreams").map(row => (
    text(size: 8pt, fill: navy, weight: "bold", hyphenate: false)[#row.at("label")],
    text(size: 8pt)[#row.at("text")],
  )).flatten(),
)

== Questions before acting
#for row in brief.at("questions") [
  #factor(row)
]

== Official resources
#text(size: 8pt, hyphenate: false)[
  #let pieces = ()
  #for (index, item) in brief.at("resources").enumerate() {
    if index > 0 {
      pieces.push([ · ])
    }
    pieces.push(link(item.at("url"))[#item.at("label")])
  }
  #pieces.join()
]

#v(5pt)
#block(width: 100%, inset: (top: 3pt), stroke: (top: 0.6pt + rule))[
  #text(size: 7pt, fill: muted)[
    #brief.at("traceability").join([\ ])
  ]
]
