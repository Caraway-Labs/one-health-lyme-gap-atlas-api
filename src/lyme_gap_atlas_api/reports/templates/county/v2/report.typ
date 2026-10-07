#import "../../shared/v1/components.typ": setup-document, report-header, callout
#let report = json("input.json")
#show: setup-document.with(report)
#report-header(report, "Investigate county observation report")
#let observation-context = report.at("observation_context")
*Observation period:* #observation-context.at("period_start") through #observation-context.at("period_end")
#callout("Interpretation", "Canonical observations with their governed value states. Modeled status is unavailable unless explicitly supplied by the governed methodology. No disease-risk or clinical inference.")
#for obs in observation-context.at("observations") [
  == #obs.at("measure_id")
  *Observation:* #obs.at("observation_id") \
  *Period / grain:* #obs.at("period_start") through #obs.at("period_end") / #obs.at("temporal_grain") \
  *Value state:* #obs.at("value_state") \
  *Value:* #if obs.at("value") == none { [Data unavailable] } else { str(obs.at("value")) } \
  *Unit:* #obs.at("unit") \
  *Source:* #obs.at("source_label") (#obs.at("source_id")) \
  *Source vintage:* #if obs.at("source_vintage") == none { [Unavailable] } else { obs.at("source_vintage") } \
  *Source URL:* #if obs.at("source_url") == none { [Unavailable] } else { obs.at("source_url") } \
  *Lineage source / dataset:* #obs.at("lineage_source_id") / #obs.at("dataset_id") \
  *Provenance reference:* #obs.at("provenance_ref") \
  *Methodology:* #if obs.at("methodology") == none { [Unavailable] } else { obs.at("methodology") } \
  *Methodology identity:* #if obs.at("methodology_id") == none { [Unavailable] } else { obs.at("methodology_id") } \
  *Release methodology version:* #if obs.at("release_methodology_version") == none { [Unavailable] } else { obs.at("release_methodology_version") } \
  *Transformation / semantic version:* #obs.at("methodology_version") / #obs.at("semantic_version") \
  *Atlas acquisition:* #if obs.at("atlas_acquired_at") == none { [Unavailable] } else { obs.at("atlas_acquired_at") }
  #if obs.at("environmental_context") != none [
    *Environmental coverage context:* #str(obs.at("environmental_context"))
  ]
  === Material caveats
  #if obs.at("limitations").len() == 0 { [No governed limitations supplied; completeness unavailable.] }
  #for limitation in obs.at("limitations") [#limitation #parbreak()]
]
== Release context
#report.at("provenance").at("limitations")
