"""Fixed read-only startup census; never log connection or document content."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from typing import Any

from neo4j import READ_ACCESS, Driver, Query

logger = logging.getLogger(__name__)
COUNTS_QUERY = """
MATCH (p:Paper) WITH count(p) AS paper_nodes,count(DISTINCT p.pmid) AS unique_pmids
CALL () {
 MATCH (e:EvidencePassage)
 OPTIONAL MATCH (owner:Paper {id:e.paper_id})
 RETURN count(DISTINCT e) AS passage_nodes,
 count(DISTINCT CASE WHEN size(e.embedding)=1024 THEN e END) AS embedding_1024_nodes,
 count(DISTINCT CASE WHEN owner IS NULL THEN e END) AS missing_paper_joins
}
CALL () {
 MATCH (probe:EvidencePassage) WHERE size(probe.embedding)=1024
 WITH probe ORDER BY probe.id,probe.paper_id LIMIT 1
 CALL db.index.vector.queryNodes('evidence_passage_summary',20,probe.embedding)
 YIELD node AS hit,score
 OPTIONAL MATCH (joined:Paper {id:hit.paper_id})
 RETURN count(hit) AS raw_vector_hits,count(joined) AS joined_rows,
 count(CASE WHEN joined IS NULL THEN 1 END) AS unmatched_hits,
 count(DISTINCT joined.pmid) AS joined_unique_pmids
}
RETURN paper_nodes,unique_pmids,passage_nodes,embedding_1024_nodes,missing_paper_joins,
raw_vector_hits,joined_rows,unmatched_hits,joined_unique_pmids
"""
INDEX_QUERY = """
SHOW INDEXES YIELD name,type,state,populationPercent,labelsOrTypes,properties,options
WHERE name IN ['entity_names','evidence_passage_summary']
RETURN name,type,state,populationPercent,labelsOrTypes,properties,options
"""
IDENTITY_QUERY = "CALL db.info() YIELD id,name RETURN id,name"


def log_serving_graph_probe(driver: Driver, category: Callable[[BaseException], str]) -> None:
    """Three autocommit reads once at construction; failures never alter readiness."""
    queries = {"identity": IDENTITY_QUERY, "counts": COUNTS_QUERY, "indexes": INDEX_QUERY}
    for probe, query in queries.items():
        try:
            # Autocommit avoids the driver's managed-transaction retry loop.
            with driver.session(database="neo4j", default_access_mode=READ_ACCESS) as session:
                rows = [record.data() for record in session.run(Query(query, timeout=1.0))]
            context: dict[str, Any] = {"probe": probe, "database": "neo4j", "outcome": "success"}
            if probe == "identity":
                if len(rows) != 1 or not isinstance(rows[0].get("id"), str):
                    raise ValueError("invalid identity shape")
                context["database_id_sha256"] = hashlib.sha256(rows[0]["id"].encode()).hexdigest()
                context["expected_database_name"] = rows[0].get("name") == "neo4j"
            elif probe == "counts":
                expected = {
                    "paper_nodes",
                    "unique_pmids",
                    "passage_nodes",
                    "embedding_1024_nodes",
                    "missing_paper_joins",
                    "raw_vector_hits",
                    "joined_rows",
                    "unmatched_hits",
                    "joined_unique_pmids",
                }
                if len(rows) != 1 or any(
                    type(rows[0].get(k)) is not int or rows[0][k] < 0 for k in expected
                ):
                    raise ValueError("invalid count shape")
                context.update({k: rows[0][k] for k in expected})
            else:
                indexes = []
                for row in rows:
                    if row.get("name") not in {"entity_names", "evidence_passage_summary"}:
                        continue
                    options = row.get("options")
                    config = options.get("indexConfig", {}) if isinstance(options, dict) else {}
                    dimension = (
                        config.get("vector.dimensions") if isinstance(config, dict) else None
                    )
                    indexes.append(
                        {
                            "name": row["name"],
                            "type": row.get("type")
                            if row.get("type") in {"FULLTEXT", "VECTOR"}
                            else "unexpected",
                            "state": row.get("state")
                            if row.get("state") in {"ONLINE", "POPULATING", "FAILED"}
                            else "unexpected",
                            "fully_populated": row.get("populationPercent") == 100.0,
                            "expected_vector_dimension": dimension == 1024
                            if row["name"] == "evidence_passage_summary"
                            else None,
                            "expected_properties": row.get("properties")
                            == (
                                ["embedding"]
                                if row["name"] == "evidence_passage_summary"
                                else ["canonical_name", "aliases"]
                            ),
                            "evidence_passage_label": row.get("labelsOrTypes")
                            == ["EvidencePassage"]
                            if row["name"] == "evidence_passage_summary"
                            else None,
                        }
                    )
                context["indexes"] = indexes
            logger.info("knowledge_chat_serving_graph_probe", extra={"context": context})
        except Exception as error:
            logger.warning(
                "knowledge_chat_serving_graph_probe",
                extra={
                    "context": {
                        "probe": probe,
                        "database": "neo4j",
                        "outcome": "failure",
                        "error_category": category(error),
                    }
                },
                exc_info=False,
            )
