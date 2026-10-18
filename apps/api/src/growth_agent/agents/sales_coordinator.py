"""Sales Coordinator — tâches, relances, rendez-vous.

Garde-fou : respect de l'opposition et des quotas.

Aucune tâche de relance n'est créée pour un contact opposé, et le nombre de
relances est plafonné. Les tâches sont créées dans le CRM, qui reste la source
de vérité ; Growth Agent n'en conserve que la correspondance d'identifiants.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select

from growth_agent.agents.base import AgentContext, AgentResult, BaseAgent
from growth_agent.connectors.crm.base import CRMAdapter, CRMTask
from growth_agent.models.enums import (
    AgentName,
    NextActionKind,
    OutreachEventType,
    ProspectStage,
)
from growth_agent.models.outreach import OutreachEvent
from growth_agent.models.prospect import ProspectCandidate
from growth_agent.security.redaction import digest
from growth_agent.services.audit import record_audit
from growth_agent.services.crm_mapping import remember_mapping
from growth_agent.services.outreach import record_outreach_event
from growth_agent.services.pipeline import schedule_action
from growth_agent.services.suppression import check_suppression

MAX_FOLLOW_UPS = 2
DEFAULT_FOLLOW_UP_DAYS = 7


@dataclass(slots=True)
class CoordinationOutcome:
    tasks_created: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    meeting_scheduled: bool = False
    #: Double local de la tâche CRM, lisible par le tableau de bord.
    next_action_id: str = ""


class SalesCoordinatorAgent(BaseAgent[CoordinationOutcome]):
    name = AgentName.SALES_COORDINATOR
    guardrail = "Jamais de relance vers un contact opposé ; relances plafonnées."

    def __init__(self, crm: CRMAdapter) -> None:
        self._crm = crm

    def run(  # type: ignore[override]
        self,
        context: AgentContext,
        *,
        prospect: ProspectCandidate,
        task_title: str = "",
        due_in_days: int = DEFAULT_FOLLOW_UP_DAYS,
        owner_email: str = "",
        kind: str = "follow_up",
        **_: Any,
    ) -> AgentResult[CoordinationOutcome]:
        outcome = CoordinationOutcome()

        verdict = check_suppression(
            context.session,
            email=prospect.contact_email,
            product_id=prospect.product_id,
            now=context.moment,
        )
        if verdict.suppressed:
            outcome.skipped.append(
                {"reason": "suppressed", "detail": f"Contact opposé ({verdict.reason})."}
            )
            record_audit(
                context.session,
                actor=context.actor,
                actor_role=context.actor_role,
                action="sales_coordinator.task_skipped",
                object_type="prospect_candidate",
                object_id=prospect.id,
                outcome="blocked",
                metadata={"reason": "suppressed"},
                now=context.moment,
            )
            return AgentResult(value=outcome, summary={"skipped": "suppressed"})

        if kind == "follow_up":
            sent_count = context.session.execute(
                select(func.count())
                .select_from(OutreachEvent)
                .where(
                    OutreachEvent.prospect_id == prospect.id,
                    OutreachEvent.event_type == OutreachEventType.SENT,
                )
            ).scalar_one()
            if sent_count > MAX_FOLLOW_UPS:
                outcome.skipped.append(
                    {
                        "reason": "quota",
                        "detail": (
                            f"{sent_count} message(s) déjà envoyé(s) (maximum {MAX_FOLLOW_UPS})."
                        ),
                    }
                )
                return AgentResult(value=outcome, summary={"skipped": "quota"})

        due_at = context.moment + timedelta(days=max(0, due_in_days))
        title = task_title or _default_title(kind, prospect)
        idempotency_key = digest(
            f"task|{prospect.id}|{kind}|{due_at.date().isoformat()}", length=48
        )

        task = self._crm.create_task(
            CRMTask(
                title=title,
                due_at=due_at.isoformat(),
                owner_email=owner_email or None,
                body=f"Prospect : {prospect.organization_name}. Étape : {prospect.stage}.",
            ),
            idempotency_key=idempotency_key,
        )
        if task.remote_id:
            outcome.tasks_created.append(task.remote_id)
            _store_mapping(
                context,
                provider=self._crm.provider,
                object_type="task",
                local_id=prospect.id,
                remote_id=task.remote_id,
                idempotency_key=idempotency_key,
            )

        # La tâche part dans le CRM, et jusqu'ici elle n'en revenait jamais :
        # le tableau de bord ne pouvait pas la voir, et « qu'est-ce qui est
        # en retard » restait sans réponse. On en garde donc un double
        # local, avec son porteur et son échéance.
        local_action = schedule_action(
            context.session,
            product_id=prospect.product_id,
            prospect_id=prospect.id,
            kind=kind if kind in set(NextActionKind) else NextActionKind.FOLLOW_UP,
            title=title,
            owner_email=owner_email or context.actor,
            due_at=due_at,
            crm_remote_id=task.remote_id or "",
            actor=context.actor,
            actor_role=context.actor_role,
            now=context.moment,
        )
        outcome.next_action_id = str(local_action.id)

        if kind == "demo":
            record_outreach_event(
                context.session,
                product_id=prospect.product_id,
                campaign_id=prospect.campaign_id,
                prospect_id=prospect.id,
                event_type=OutreachEventType.DEMO_SCHEDULED,
                occurred_at=context.moment,
                actor=context.actor,
                payload={"due_at": due_at.isoformat()},
                idempotency_key=f"demo:{idempotency_key}",
            )
            prospect.stage = ProspectStage.DEMO_SCHEDULED
            outcome.meeting_scheduled = True
            context.session.flush()

        record_audit(
            context.session,
            actor=context.actor,
            actor_role=context.actor_role,
            action=f"sales_coordinator.{kind}_created",
            object_type="prospect_candidate",
            object_id=prospect.id,
            metadata={"task_remote_id": task.remote_id, "due_at": due_at.isoformat()},
            now=context.moment,
        )
        return AgentResult(
            value=outcome,
            summary={"tasks_created": len(outcome.tasks_created), "kind": kind},
        )


def _default_title(kind: str, prospect: ProspectCandidate) -> str:
    return {
        "follow_up": f"Relancer {prospect.organization_name}",
        "demo": f"Démonstration — {prospect.organization_name}",
        "call": f"Appeler {prospect.organization_name}",
    }.get(kind, f"Action commerciale — {prospect.organization_name}")


def _store_mapping(
    context: AgentContext,
    *,
    provider: str,
    object_type: str,
    local_id: uuid.UUID,
    remote_id: str,
    idempotency_key: str = "",
) -> None:
    """Enregistre la correspondance de la tâche créée.

    Plusieurs tâches existent pour un même prospect — une relance, puis une
    démonstration. La table ne les distingue que par la clé d'idempotence ;
    c'est elle qui porte l'unicité, pas le couple (prospect, type d'objet).
    """
    remember_mapping(
        context.session,
        provider=provider,
        object_type=object_type,
        local_id=local_id,
        remote_id=remote_id,
        idempotency_key=idempotency_key,
        now=context.moment,
    )
