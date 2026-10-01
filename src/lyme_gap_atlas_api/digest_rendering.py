"""Offline rendering of frozen briefing artifacts, with no delivery composition."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from html import escape
from typing import Final

from pydantic import ValidationError

from .briefings import BriefingArtifact, BriefingModel, Digest, Token

RENDER_VERSION: Final = "digest-message-v1"
MAX_MESSAGE_BYTES: Final = 1_048_576


class DigestRenderPrerequisites(BriefingModel):
    """Exact frozen-artifact binding; not subscriber consent or send permission."""

    artifact_id: Digest
    input_sha256: Digest
    preference_revision: Token
    digest_version: Token


@dataclass(frozen=True)
class RenderedDigest:
    render_version: str
    artifact_id: str
    input_sha256: str
    preference_revision: str
    digest_version: str
    subject: str
    text: str
    html: str
    content_sha256: str


class DigestRenderError(RuntimeError):
    """Finite diagnostics; publisher content and resolver errors stay private."""


class DigestRenderer:
    """Requires an injected resolver for an approved, immutable stored artifact.

    No default resolver, subscriber store, email provider, send or scheduler.
    Eligibility, current preferences and suppression must be resolved by their
    approved owners before a future delivery composition can use this message.
    """

    def __init__(
        self,
        *,
        resolve_artifact: Callable[[DigestRenderPrerequisites], BriefingArtifact | None],
    ) -> None:
        self._resolve = resolve_artifact

    def render(self, prerequisites: DigestRenderPrerequisites) -> RenderedDigest:
        invalid = False
        try:
            prerequisites = DigestRenderPrerequisites.model_validate(
                prerequisites.model_dump(mode="json")
            )
        except (ValidationError, TypeError, AttributeError):
            invalid = True
        if invalid:
            raise DigestRenderError("DIGEST_RENDER_PREREQUISITES_INVALID")
        failed = False
        try:
            artifact = self._resolve(prerequisites)
        except Exception:
            failed = True
            artifact = None
        # Raise outside the handler: no raw resolver exception in the chain.
        if failed:
            raise DigestRenderError("DIGEST_RENDER_RESOLVER_FAILED")
        if artifact is None:
            raise DigestRenderError("DIGEST_RENDER_ARTIFACT_UNAVAILABLE")
        try:
            if (
                len(artifact.ranked_items) > 100
                or len(artifact.limitations) > 100
                or sum(len(row.evidence) for row in artifact.ranked_items) > 1000
            ):
                raise DigestRenderError("DIGEST_RENDER_BOUND_EXCEEDED")
            artifact = BriefingArtifact.model_validate(artifact.model_dump(mode="json"))
        except (ValidationError, TypeError, AttributeError):
            invalid = True
        if invalid:
            raise DigestRenderError("DIGEST_RENDER_ARTIFACT_INVALID")
        if any(
            getattr(artifact, field) != getattr(prerequisites, field)
            for field in ("artifact_id", "input_sha256", "preference_revision", "digest_version")
        ):
            raise DigestRenderError("DIGEST_RENDER_ARTIFACT_BINDING_MISMATCH")
        if artifact.matching_publications != len(
            artifact.ranked_items
        ) + artifact.omitted_publications or len(
            {row.item_id for row in artifact.ranked_items}
        ) != len(artifact.ranked_items):
            raise DigestRenderError("DIGEST_RENDER_ARTIFACT_INCONSISTENT")
        lines = [
            "Atlas intelligence digest",
            artifact.notice,
            f"Capture window: [{artifact.window.start}, {artifact.window.end}) UTC",
            "Window basis: fetched_at (not publication time)",
            f"Generated: {artifact.generated_at}",
            f"Artifact: {artifact.artifact_id}",
            f"Input: {artifact.input_sha256}; snapshot: {artifact.snapshot_id}",
            f"Preferences: {artifact.preference_revision}; digest: {artifact.digest_version}",
            f"Generation: {artifact.generation_version}; contract: {artifact.contract_version}",
            f"Scope: {_json(artifact.scope.model_dump(mode='json'))}",
            f"Matching publications: {artifact.matching_publications}; "
            f"omitted: {artifact.omitted_publications}",
        ]
        html = ['<!doctype html><html lang="en"><body>']
        html.extend(f"<p>{escape(line, quote=True)}</p>" for line in lines)
        if not artifact.ranked_items:
            lines.append(
                "No matching items in this frozen capture window."
                if artifact.matching_publications == 0
                else "All matching publications were omitted from this frozen artifact."
            )
            html.append(f"<p>{escape(lines[-1])}</p>")
        for rank, row in enumerate(artifact.ranked_items, 1):
            if row.rank != rank or not row.evidence:
                raise DigestRenderError("DIGEST_RENDER_ARTIFACT_INCONSISTENT")
            heading = f"Item {rank}: {row.item_id}; matching values: {row.matching_values}"
            heading += f"; publisher recency: {row.publisher_recency or 'unknown'}"
            lines.extend(["", heading])
            html.append(f"<h2>{escape(heading)}</h2>")
            for evidence in row.evidence:
                item, source = evidence.item, evidence.source
                if (
                    item.item_id != row.item_id
                    or item.source_id != source.source_id
                    or item.registry_version != source.registry_version
                ):
                    raise DigestRenderError("DIGEST_RENDER_ARTIFACT_INCONSISTENT")
                values = [
                    f"Publisher: {source.organization}; source: {source.source_id}; "
                    f"registry version: {source.registry_version}",
                    f"Reviewed source classification: {source.reviewed_trust_classification}",
                    f"Title: {item.title if item.title is not None else 'unknown'}",
                    "Publisher excerpt (untrusted): "
                    f"{item.excerpt if item.excerpt is not None else 'unknown'}",
                    f"Published: {item.published_at or 'unknown'}; "
                    f"updated: {item.updated_at or 'unknown'}; "
                    f"event: {item.event_at or 'unknown'}; fetched: {item.fetched_at}",
                    f"Field states: {_json(item.field_states.model_dump(mode='json'))}",
                    "Why matched: "
                    f"{_json([match.model_dump(mode='json') for match in evidence.why_matched])}",
                    "Geographies: "
                    f"{_json([tag.model_dump(mode='json') for tag in item.geographies])}",
                    f"Topics: {_json([tag.model_dump(mode='json') for tag in item.topics])}",
                    f"Revision: {item.revision_id}; transport: {item.transport}; "
                    f"transport identity: {item.transport_identity_sha256}",
                    f"Deduplication: {item.deduplication_key}; content: {item.content_sha256}",
                    f"Provenance: {_json(item.provenance.model_dump(mode='json'))}",
                    f"Item limitations: {_json(item.limitations)}",
                ]
                lines.extend(values)
                html.extend(f"<p>{escape(value, quote=True)}</p>" for value in values)
                if item.canonical_url is None:
                    lines.append("Canonical citation: unknown")
                    html.append("<p>Canonical citation: unknown</p>")
                else:
                    lines.append(f"Canonical citation: {item.canonical_url}")
                    url = escape(item.canonical_url, quote=True)
                    html.append(f'<p>Canonical citation: <a href="{url}">{url}</a></p>')
        limitations = f"Digest limitations: {_json(artifact.limitations)}"
        lines.append(limitations)
        html.extend([f"<p>{escape(limitations, quote=True)}</p>", "</body></html>"])
        text_body, html_body = "\n".join(lines) + "\n", "\n".join(html)
        if any(len(body.encode("utf-8")) > MAX_MESSAGE_BYTES for body in (text_body, html_body)):
            # Never trim citations or limitations to fit a provider's limit.
            raise DigestRenderError("DIGEST_RENDER_BOUND_EXCEEDED")
        subject = "Atlas intelligence digest"
        checksum = hashlib.sha256(
            _json([RENDER_VERSION, subject, text_body, html_body]).encode("utf-8")
        ).hexdigest()
        return RenderedDigest(
            RENDER_VERSION,
            artifact.artifact_id,
            artifact.input_sha256,
            artifact.preference_revision,
            artifact.digest_version,
            subject,
            text_body,
            html_body,
            checksum,
        )


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
