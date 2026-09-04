#import "../../shared/v1/components.typ": setup-document, report-header, score-summary, metric-table, callout, provenance

#let report = json("input.json")
#show: setup-document(report)

#report-header(report, "Lyme Gap Atlas county report")

== Executive summary
#score-summary(report.at("score"))
v(12pt)
#callout("Priority", report.at("priority"))

== Human-health evidence and data
#metric-table(report.at("human_health"))

== Ecological evidence and data
#metric-table(report.at("ecological"))

== Data gaps and completeness
#metric-table((report.at("population"), report.at("data_completeness")))

#provenance(report)
