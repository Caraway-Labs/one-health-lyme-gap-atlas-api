#set page(paper: "us-letter", margin: 0.75in)
#set text(size: 11pt)

#let report = json("input.json")

= Lyme Gap Atlas Report

== Geography
#report.at("geography").at("name") (#report.at("geography").at("identifier"))

== Report provenance
Dataset version: #report.at("provenance").at("dataset_version") \
Template version: #report.at("identity").at("template_version") \
Methodology version: #report.at("provenance").at("methodology_version")
