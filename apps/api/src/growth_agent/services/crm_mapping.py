"""Mémoire d'idempotence des écritures CRM, adossée à la base locale.

Elle répond à une question simple — « ai-je déjà créé cet objet ? » — sans
interroger le CRM. C'est nécessaire parce que les CRM, HubSpot en tête,
n'offrent pas d'idempotence et exposent un index de recherche à cohérence
différée : un objet créé il y a deux secondes n'y figure pas encore. Une
reprise après incident qui s'y fierait créerait un doublon.

Ici, la correspondance est écrite dans la même transaction que le reste du
travail, et relue immédiatement. Le CRM reste la source de vérité des données ;
cette table ne porte que le lien.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from growth_agent.db.base import utcnow
from growth_agent.models.prospect import CRMMapping

#: Identifiant local utilisé quand l'écriture ne correspond à aucun objet de
#: notre base — une note libre, par exemple. La correspondance vaut alors pour
#: la clé seule.
UNLINKED_LOCAL_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


class DatabaseIdempotencyStore:
    """Mémoire durable, portée par la session en cours.

    Une instance vaut pour une transaction : c'est volontaire. Partager un
    objet mutable entre requêtes ferait écrire l'une dans la transaction de
    l'autre.
    """

    def __init__(
        self,
        session: Session,
        *,
        local_id: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> None:
        self._session = session
        self._local_id = local_id or UNLINKED_LOCAL_ID
        self._now = now

    def lookup(self, *, provider: str, object_type: str, idempotency_key: str) -> str | None:
        if not idempotency_key:
            return None
        return self._session.execute(
            select(CRMMapping.remote_id).where(
                CRMMapping.crm_provider == provider,
                CRMMapping.object_type == object_type,
                CRMMapping.idempotency_key == idempotency_key,
            )
        ).scalar_one_or_none()

    def remember(
        self,
        *,
        provider: str,
        object_type: str,
        idempotency_key: str,
        remote_id: str,
    ) -> None:
        if not idempotency_key or not remote_id:
            return
        moment = self._now or utcnow()
        existing = self._session.execute(
            select(CRMMapping).where(
                CRMMapping.crm_provider == provider,
                CRMMapping.object_type == object_type,
                CRMMapping.idempotency_key == idempotency_key,
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.last_synced_at = moment
            return

        mapping = CRMMapping(
            crm_provider=provider,
            object_type=object_type,
            local_id=self._local_id,
            remote_id=remote_id,
            idempotency_key=idempotency_key,
            last_synced_at=moment,
        )
        # Deux travailleurs peuvent franchir ensemble la lecture ci-dessus. Le
        # point de reprise rattrape le perdant sans perdre la transaction en
        # cours : il relit, et accepte la ligne que l'autre vient d'écrire.
        try:
            with self._session.begin_nested():
                self._session.add(mapping)
                self._session.flush()
        except IntegrityError:
            self._session.expunge(mapping)


def remember_mapping(
    session: Session,
    *,
    provider: str,
    object_type: str,
    local_id: uuid.UUID,
    remote_id: str,
    idempotency_key: str = "",
    now: datetime | None = None,
) -> CRMMapping | None:
    """Enregistre une correspondance observée, sans passer par l'adaptateur.

    Renvoie `None` quand la correspondance existait déjà : l'appelant n'a rien
    à faire de plus.
    """
    moment = now or utcnow()
    existing = session.execute(
        select(CRMMapping).where(
            CRMMapping.crm_provider == provider,
            CRMMapping.object_type == object_type,
            CRMMapping.remote_id == remote_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.last_synced_at = moment
        if idempotency_key and not existing.idempotency_key:
            existing.idempotency_key = idempotency_key
        return None

    mapping = CRMMapping(
        crm_provider=provider,
        object_type=object_type,
        local_id=local_id,
        remote_id=remote_id,
        idempotency_key=idempotency_key,
        last_synced_at=moment,
    )
    try:
        with session.begin_nested():
            session.add(mapping)
            session.flush()
    except IntegrityError:
        session.expunge(mapping)
        return None
    return mapping
