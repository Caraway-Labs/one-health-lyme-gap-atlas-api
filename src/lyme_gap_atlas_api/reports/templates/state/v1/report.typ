#import "../../shared/v1/components.typ": setup-document, report-header, score-summary, metric-table, callout, provenance

#let report = json("input.json")
#show: setup-document.with(report)

#report-header(report, "Lyme Gap Atlas state report")

== Executive summary
#score-summary(report.at("mean_score"), label: "Mean Atlas score")
#v(12pt)
#callout("State summary", "This report summarizes the counties represented in the selected Atlas dataset.")

== Human-health, ecological, and data-gap summary
#metric-table(report.at("summary"))

#provenance(report)
