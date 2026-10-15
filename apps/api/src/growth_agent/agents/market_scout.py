"""Market Scout — découverte d'organisations.

Garde-fou : pas de collecte illicite ni de scraping interdit.

Concrètement, dans ce code :
  * l'agent ne va jamais chercher une page lui-même ; il passe par un
    `SearchProvider`, qui porte la responsabilité des conditions d'utilisation ;
  * un résultat sans URL ni date de collecte est rejeté, pas « complété » ;
  * aucune adresse email n'est devinée à partir d'un nom et d'un domaine ;
  * les doublons sont fusionnés sur la clé de dédoublonnage, jamais recréés.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from growth_agent.agents.base import AgentContext, AgentResult, BaseAgent
from growth_agent.config import get_settings
from growth_agent.connectors.search.base import SearchProvider, SearchQuery, SearchResult
from growth_agent.models.catalog import Campaign, IdealCustomerProfile, Product
from growth_agent.models.enums import AgentName, ProspectStage, VerificationStatus
from growth_agent.models.prospect import ProspectCandidate, ProspectSource
from growth_agent.security.prompt_guard import sanitize_external_text
from growth_agent.services.audit import record_audit
from growth_agent.services.dedupe import build_dedupe_key, normalize_domain, normalize_email


@dataclass(slots=True)
class DiscoveryOutcome:
    created: list[uuid.UUID] = field(default_factory=list)
    updated: list[uuid.UUID] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)

    @property
    def total_seen(self) -> int:
        return len(self.created) + len(self.updated) + len(self.rejected)


class MarketScoutAgent(BaseAgent[DiscoveryOutcome]):
    name = AgentName.MARKET_SCOUT
    guardrail = "Pas de collecte illicite ni de scraping interdit ; aucune source inventée."

    def __init__(self, search_provider: SearchProvider) -> None:
        self._search = search_provider

    def run(  # type: ignore[override]
        self,
        context: AgentContext,
        *,
        product: Product,
        campaign: Campaign | None = None,
        icp: IdealCustomerProfile | None = None,
        limit: int = 25,
        **_: Any,
    ) -> AgentResult[DiscoveryOutcome]:
        settings = get_settings()
        query = SearchQuery(
            product_code=product.code,
            country_codes=list(icp.country_codes) if icp else [],
            sectors=list(icp.target_sectors) if icp else [],
            keywords=list(icp.need_signals) if icp else [],
            limit=limit,
        )
        results = self._search.search(query)
        outcome = DiscoveryOutcome()
        warnings: list[str] = []

        for result in results:
            rejection = _validate(result)
            if rejection:
                outcome.rejected.append(
                    {"organization_name": result.organization_name, "reason": rejection}
                )
                continue

            try:
                dedupe_key = build_dedupe_key(
                    organization_name=result.organization_name,
                    website=result.url,
                    domain=result.domain,
                    contact_email=result.contact_email,
                    country_code=result.country_code,
                )
            except ValueError as exc:
                outcome.rejected.append(
                    {"organization_name": result.organization_name, "reason": str(exc)}
                )
                continue

            existing = context.session.execute(
                select(ProspectCandidate).where(
                    ProspectCandidate.product_id == product.id,
                    ProspectCandidate.dedupe_key == dedupe_key,
                )
            ).scalar_one_or_none()

            if existing is not None:
                self._attach_source(context, existing, result)
                _fill_missing_fields(existing, result)
                outcome.updated.append(existing.id)
                continue

            prospect = ProspectCandidate(
                product_id=product.id,
                campaign_id=campaign.id if campaign else None,
                organization_name=result.organization_name.strip(),
                dedupe_key=dedupe_key,
                website=result.url,
                domain=normalize_domain(result.domain) or normalize_domain(result.url),
                country_code=(result.country_code or "FR")[:2].upper(),
                city=result.city,
                sector=result.sector,
                headcount_estimate=result.headcount_estimate,
                contact_full_name=result.contact_full_name,
                contact_role=result.contact_role,
                contact_email=normalize_email(result.contact_email),
                stage=ProspectStage.DISCOVERED,
                # Découvert n'est pas vérifié : la qualification tranchera.
                verification_status=VerificationStatus.UNKNOWN,
                discovered_by=self.name,
                retention_until=context.moment + timedelta(days=settings.data_retention_days),
            )
            context.session.add(prospect)
            context.session.flush()
            self._attach_source(context, prospect, result)
            outcome.created.append(prospect.id)

        if outcome.rejected:
            warnings.append(
                f"{len(outcome.rejected)} résultat(s) écarté(s) faute de provenance exploitable."
            )

        record_audit(
            context.session,
            actor=context.actor,
            actor_role=context.actor_role,
            action="market_scout.discovered",
            object_type="product",
            object_id=product.id,
            product_code=product.code,
            metadata={
                "provider": self._search.provider,
                "created": len(outcome.created),
                "updated": len(outcome.updated),
                "rejected": len(outcome.rejected),
            },
            now=context.moment,
        )

        return AgentResult(
            value=outcome,
            summary={
                "created": len(outcome.created),
                "updated": len(outcome.updated),
                "rejected": len(outcome.rejected),
                "provider": self._search.provider,
            },
            warnings=warnings,
        )

    def _attach_source(
        self, context: AgentContext, prospect: ProspectCandidate, result: SearchResult
    ) -> None:
        """Ajoute la source si cette URL n'est pas déjà rattachée au prospect."""
        already = context.session.execute(
            select(ProspectSource).where(
                ProspectSource.prospect_id == prospect.id,
                ProspectSource.url == result.url,
            )
        ).scalar_one_or_none()
        if already is not None:
            return

        # L'extrait vient d'une page externe : il est assaini avant stockage,
        # car il finira dans un prompt lors de la qualification.
        report = sanitize_external_text(result.excerpt, max_chars=1500)
        context.session.add(
            ProspectSource(
                prospect_id=prospect.id,
                kind=result.source_kind,
                url=result.url,
                title=result.title[:512],
                excerpt=report.text,
                collected_at=result.collected_at,
                collected_by=self.name,
                provider=result.provider,
                license_note=result.license_note,
            )
        )
        context.session.flush()


def _validate(result: SearchResult) -> str | None:
    """Motif de rejet, ou None si le résultat est exploitable."""
    if not result.url or not result.url.startswith(("http://", "https://")):
        return "URL de provenance absente ou non exploitable"
    if not result.collected_at:
        return "date de collecte absente"
    if not result.organization_name.strip():
        return "nom d'organisation absent"
    if result.contact_email and not normalize_email(result.contact_email):
        return "adresse de contact malformée"
    return None


def _fill_missing_fields(prospect: ProspectCandidate, result: SearchResult) -> None:
    """Complète les champs vides sans jamais écraser une donnée existante.

    Une source plus récente n'est pas forcément plus fiable ; on n'écrase donc
    rien, on ne fait que combler les trous.
    """
    if not prospect.sector and result.sector:
        prospect.sector = result.sector
    if not prospect.city and result.city:
        prospect.city = result.city
    if prospect.headcount_estimate is None and result.headcount_estimate:
        prospect.headcount_estimate = result.headcount_estimate
    if not prospect.contact_full_name and result.contact_full_name:
        prospect.contact_full_name = result.contact_full_name
    if not prospect.contact_role and result.contact_role:
        prospect.contact_role = result.contact_role
    if not prospect.contact_email and result.contact_email:
        prospect.contact_email = normalize_email(result.contact_email)
