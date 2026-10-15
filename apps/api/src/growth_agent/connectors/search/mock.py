"""Fournisseur de recherche factice, alimenté par un jeu de données fictif.

Les organisations livrées ici sont INVENTÉES pour la démonstration. Les noms,
domaines et contacts ne correspondent à aucune entité réelle ; les URL pointent
vers un domaine d'exemple réservé (`example.org`, RFC 2606) afin qu'aucune
requête ne parte vers un vrai site.
"""

from __future__ import annotations

from datetime import UTC, datetime

from growth_agent.connectors.search.base import SearchProvider, SearchQuery, SearchResult

LICENSE_NOTE = "Jeu de données fictif livré avec le dépôt — aucune collecte réelle."

_FIXTURES: dict[str, list[dict]] = {
    "maintrix": [
        {
            "organization_name": "Port Autonome de Kribi Services",
            "domain": "pak-services.example.org",
            "country_code": "CM",
            "city": "Kribi",
            "sector": "Logistique portuaire",
            "headcount_estimate": 240,
            "source_kind": "job_posting",
            "title": "Offre : Technicien de maintenance industrielle",
            "excerpt": (
                "Le service technique recrute pour accompagner la modernisation de la "
                "maintenance des équipements de levage. Une GMAO sera déployée en 2027."
            ),
            "contact_full_name": "Awa Njoya",
            "contact_role": "Responsable maintenance",
            "contact_email": "a.njoya@pak-services.example.org",
        },
        {
            "organization_name": "Agro Douala Transformation",
            "domain": "agro-douala.example.org",
            "country_code": "CM",
            "city": "Douala",
            "sector": "Agroalimentaire",
            "headcount_estimate": 520,
            "source_kind": "press_article",
            "title": "Agro Douala investit dans ses lignes de production",
            "excerpt": (
                "L'industriel annonce vouloir digitaliser le suivi des arrêts machine, "
                "aujourd'hui tenu sur tableur."
            ),
            "contact_full_name": "Paul Mbarga",
            "contact_role": "Directeur technique",
            "contact_email": "p.mbarga@agro-douala.example.org",
        },
        {
            "organization_name": "Énergie Sanaga Maintenance",
            "domain": "sanaga-energie.example.org",
            "country_code": "CM",
            "city": "Edéa",
            "sector": "Énergie",
            "headcount_estimate": 130,
            "source_kind": "public_registry",
            "title": "Fiche d'entreprise — registre public",
            "excerpt": "Prestataire de maintenance pour installations électriques.",
            # Aucun contact : le prospect restera incomplet, et c'est voulu.
        },
        {
            "organization_name": "Manufacture Rhône Précision",
            "domain": "rhone-precision.example.org",
            "country_code": "FR",
            "city": "Lyon",
            "sector": "Mécanique de précision",
            "headcount_estimate": 85,
            "source_kind": "public_website",
            "title": "Notre démarche qualité",
            "excerpt": (
                "L'atelier modernise son pilotage des interventions préventives "
                "sur l'ensemble du parc machines."
            ),
            "contact_full_name": "Claire Fontaine",
            "contact_role": "Responsable production",
            "contact_email": "c.fontaine@rhone-precision.example.org",
        },
    ],
    "educore": [
        {
            "organization_name": "Collège Bilingue Les Palmiers",
            "domain": "clg-palmiers.example.org",
            "country_code": "CM",
            "city": "Yaoundé",
            "sector": "Enseignement secondaire",
            "headcount_estimate": 60,
            "source_kind": "public_website",
            "title": "Inscriptions 2026-2027",
            "excerpt": (
                "L'établissement ouvre deux nouvelles sections et souhaite améliorer "
                "sa gestion administrative des inscriptions."
            ),
            "contact_full_name": "Sylvie Ateba",
            "contact_role": "Chef d'établissement",
            "contact_email": "direction@clg-palmiers.example.org",
        },
        {
            "organization_name": "Institut Supérieur Nkolbisson",
            "domain": "is-nkolbisson.example.org",
            "country_code": "CM",
            "city": "Yaoundé",
            "sector": "Enseignement supérieur",
            "headcount_estimate": 140,
            "source_kind": "press_article",
            "title": "L'institut double ses effectifs",
            "excerpt": (
                "La direction reconnaît que le suivi des scolarités, encore manuel, "
                "atteint ses limites."
            ),
            "contact_full_name": "Hervé Tchoumi",
            "contact_role": "Directeur administratif",
            "contact_email": "h.tchoumi@is-nkolbisson.example.org",
        },
        {
            "organization_name": "Centre de Formation Douala Pro",
            "domain": "douala-pro.example.org",
            "country_code": "CM",
            "city": "Douala",
            "sector": "Formation professionnelle",
            "headcount_estimate": 35,
            "source_kind": "directory",
            "title": "Annuaire des centres agréés",
            "excerpt": "Centre agréé pour les formations techniques courtes.",
            # Contact inconnu volontairement.
        },
        {
            "organization_name": "Lycée Privé Saint-Exupéry Marseille",
            "domain": "lycee-stexupery.example.org",
            "country_code": "FR",
            "city": "Marseille",
            "sector": "Enseignement secondaire",
            "headcount_estimate": 95,
            "source_kind": "public_website",
            "title": "Projet d'établissement 2026",
            "excerpt": (
                "Le projet prévoit de digitaliser la gestion administrative et le "
                "suivi des absences."
            ),
            "contact_full_name": "Marc Delaunay",
            "contact_role": "Directeur administratif et financier",
            "contact_email": "m.delaunay@lycee-stexupery.example.org",
        },
    ],
}


class MockSearchProvider(SearchProvider):
    provider = "mock"

    def __init__(self, *, now: datetime | None = None) -> None:
        self._now = now or datetime.now(UTC)
        self.queries: list[SearchQuery] = []

    def search(self, query: SearchQuery) -> list[SearchResult]:
        self.queries.append(query)
        fixtures = _FIXTURES.get(query.product_code.lower(), [])
        results: list[SearchResult] = []

        for fixture in fixtures:
            if query.country_codes and fixture["country_code"] not in query.country_codes:
                continue
            if query.keywords and not _matches_keywords(fixture, query.keywords):
                continue
            results.append(
                SearchResult(
                    organization_name=fixture["organization_name"],
                    url=f"https://{fixture['domain']}/{_slugify(fixture['title'])}",
                    collected_at=self._now,
                    source_kind=fixture["source_kind"],
                    title=fixture["title"],
                    excerpt=fixture["excerpt"],
                    domain=fixture["domain"],
                    country_code=fixture["country_code"],
                    city=fixture.get("city"),
                    sector=fixture.get("sector"),
                    headcount_estimate=fixture.get("headcount_estimate"),
                    contact_full_name=fixture.get("contact_full_name"),
                    contact_role=fixture.get("contact_role"),
                    contact_email=fixture.get("contact_email"),
                    provider=self.provider,
                    license_note=LICENSE_NOTE,
                )
            )
            if len(results) >= query.limit:
                break
        return results


def _matches_keywords(fixture: dict, keywords: list[str]) -> bool:
    haystack = " ".join(
        str(fixture.get(field, "")) for field in ("title", "excerpt", "sector", "organization_name")
    ).lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def _slugify(value: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in value.lower()).strip("-")[:60]
