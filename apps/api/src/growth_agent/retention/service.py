"""Service de purge : calculer d'abord, écrire ensuite.

`plan()` n'écrit rien, jamais. Il compte ce qui serait affecté et évalue les
garde-fous. `apply()` refait le calcul puis exécute, par lots validés
séparément.

Les garde-fous, dans l'ordre où ils s'appliquent :

1. **Purge désactivée.** Sans `GA_PURGE_ENABLED=true`, rien ne s'exécute. Une
   application qui supprime des données dès l'installation est dangereuse.
2. **Échéance obligatoire.** Un enregistrement sans date d'échéance n'est
   jamais purgé. Une date absente est une inconnue, pas une autorisation.
3. **Blocage légal.** `legal_hold` gèle l'enregistrement.
4. **Plafond absolu.** Au-delà de `purge_max_records_per_run`, on s'arrête.
5. **Proportion maximale.** Si une règle voudrait purger plus de la moitié de
   sa table, l'exécution s'arrête **avant** la première écriture : un calcul
   de date erroné rendrait soudain tout « échu », et ce garde-fou est le seul
   à pouvoir l'attraper.
6. **Tables protégées.** Vérifié par construction et par un test.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from growth_agent.config import Settings, get_settings
from growth_agent.models.ops import AuditEvent, ProductPilotEvent
from growth_agent.models.outreach import MessageDraft
from growth_agent.models.prospect import ProspectCandidate, ProspectSource
from growth_agent.retention.policy import (
    ANONYMIZED_DRAFT_FIELDS,
    ANONYMIZED_PROSPECT_FIELDS,
    ANONYMIZED_SOURCE_FIELDS,
    PROTECTED_TABLES,
    RetentionPolicy,
    RetentionRule,
    build_policy,
    default_now,
)
from growth_agent.services.audit import record_audit


class SafetyStop(Exception):
    """Un garde-fou a interrompu l'exécution avant toute écriture."""


@dataclass(slots=True)
class RulePlan:
    """Ce qu'une règle ferait, sans l'avoir fait."""

    key: str
    label: str
    table: str
    action: str
    retention_days: int
    cutoff: datetime
    eligible: int = 0
    protected_by_legal_hold: int = 0
    table_total: int = 0
    blocked_reason: str = ""

    @property
    def ratio(self) -> float:
        return (self.eligible / self.table_total) if self.table_total else 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "table": self.table,
            "action": self.action,
            "retention_days": self.retention_days,
            "cutoff": self.cutoff.isoformat(),
            "eligible": self.eligible,
            "protected_by_legal_hold": self.protected_by_legal_hold,
            "table_total": self.table_total,
            "ratio": round(self.ratio, 4),
            "blocked_reason": self.blocked_reason,
        }


@dataclass(slots=True)
class PurgePlan:
    computed_at: datetime
    rules: list[RulePlan] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocked: bool = False
    blocked_reason: str = ""

    @property
    def total_eligible(self) -> int:
        return sum(r.eligible for r in self.rules)

    def as_dict(self) -> dict[str, object]:
        return {
            "computed_at": self.computed_at.isoformat(),
            "total_eligible": self.total_eligible,
            "blocked": self.blocked,
            "blocked_reason": self.blocked_reason,
            "warnings": list(self.warnings),
            "rules": [r.as_dict() for r in self.rules],
        }


@dataclass(slots=True)
class PurgeReport:
    dry_run: bool
    started_at: datetime
    finished_at: datetime | None = None
    counts: dict[str, int] = field(default_factory=dict)
    plan: PurgePlan | None = None
    stopped_reason: str = ""

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def as_dict(self) -> dict[str, object]:
        return {
            "dry_run": self.dry_run,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "counts": dict(self.counts),
            "total": self.total,
            "stopped_reason": self.stopped_reason,
            "plan": self.plan.as_dict() if self.plan else None,
        }


class PurgeService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        policy: RetentionPolicy | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._policy = policy or build_policy(self._settings)

    # --- Calcul ------------------------------------------------------------

    def plan(self, *, now: datetime | None = None) -> PurgePlan:
        """Calcule ce qui serait purgé. N'écrit rien."""
        moment = now or default_now()
        plan = PurgePlan(computed_at=moment)

        for rule in self._policy:
            # Vérification de cohérence : une règle ne peut pas viser une table
            # protégée. C'est une erreur de programmation, pas une condition
            # d'exécution — d'où l'exception plutôt qu'un avertissement.
            if rule.table in PROTECTED_TABLES:
                raise SafetyStop(
                    f"La règle « {rule.key} » vise la table protégée « {rule.table} »."
                )
            plan.rules.append(self._plan_rule(rule, moment))

        if not self._settings.purge_enabled:
            plan.blocked = True
            plan.blocked_reason = (
                "Purge désactivée (GA_PURGE_ENABLED=false). Le calcul est fourni "
                "à titre indicatif ; aucune exécution n'est possible."
            )

        # Garde-fou de proportion, évalué règle par règle.
        for rule_plan in plan.rules:
            if rule_plan.table_total == 0 or rule_plan.eligible == 0:
                continue
            if rule_plan.ratio > self._settings.purge_max_table_ratio:
                rule_plan.blocked_reason = (
                    f"{round(rule_plan.ratio * 100)} % de la table « {rule_plan.table} » "
                    f"serait affecté, au-delà du seuil de "
                    f"{round(self._settings.purge_max_table_ratio * 100)} %. "
                    "Vérifier la durée de conservation avant de poursuivre."
                )
                plan.blocked = True
                plan.blocked_reason = (
                    "Proportion anormale sur au moins une règle : exécution refusée."
                )

        if plan.total_eligible > self._settings.purge_max_records_per_run:
            plan.warnings.append(
                f"{plan.total_eligible} enregistrements échus pour un plafond de "
                f"{self._settings.purge_max_records_per_run} par exécution : "
                "la purge s'arrêtera au plafond et reprendra à l'exécution suivante."
            )

        protected = sum(r.protected_by_legal_hold for r in plan.rules)
        if protected:
            plan.warnings.append(
                f"{protected} enregistrement(s) échu(s) mais gelé(s) par un blocage légal."
            )

        return plan

    def _plan_rule(self, rule: RetentionRule, moment: datetime) -> RulePlan:
        cutoff = rule.cutoff(moment)
        rule_plan = RulePlan(
            key=rule.key,
            label=rule.label,
            table=rule.table,
            action=rule.action,
            retention_days=rule.retention_days,
            cutoff=cutoff,
        )

        if rule.key == "prospects":
            rule_plan.table_total = self._count(select(func.count()).select_from(ProspectCandidate))
            rule_plan.eligible = self._count(
                select(func.count())
                .select_from(ProspectCandidate)
                .where(*self._prospect_conditions(moment))
            )
            rule_plan.protected_by_legal_hold = self._count(
                select(func.count())
                .select_from(ProspectCandidate)
                .where(
                    ProspectCandidate.retention_until.is_not(None),
                    ProspectCandidate.retention_until < moment,
                    ProspectCandidate.legal_hold.is_(True),
                )
            )
        elif rule.key == "drafts":
            rule_plan.table_total = self._count(select(func.count()).select_from(MessageDraft))
            rule_plan.eligible = self._count(
                select(func.count())
                .select_from(MessageDraft)
                .where(*self._draft_conditions(cutoff))
            )
        elif rule.key == "audit":
            rule_plan.table_total = self._count(select(func.count()).select_from(AuditEvent))
            rule_plan.eligible = self._count(
                select(func.count()).select_from(AuditEvent).where(AuditEvent.occurred_at < cutoff)
            )
        elif rule.key == "pilot_events":
            rule_plan.table_total = self._count(select(func.count()).select_from(ProductPilotEvent))
            rule_plan.eligible = self._count(
                select(func.count())
                .select_from(ProductPilotEvent)
                .where(ProductPilotEvent.occurred_at < cutoff)
            )
        return rule_plan

    def _count(self, statement) -> int:
        return int(self._session.execute(statement).scalar_one())

    # --- Sélection ---------------------------------------------------------

    @staticmethod
    def _prospect_conditions(moment: datetime) -> tuple:
        """Conditions d'éligibilité d'un prospect.

        Une date d'échéance absente exclut l'enregistrement : on ne purge pas
        sur une inconnue. Un prospect déjà anonymisé est exclu aussi, ce qui
        rend l'opération idempotente et permet une reprise naturelle.
        """
        return (
            ProspectCandidate.retention_until.is_not(None),
            ProspectCandidate.retention_until < moment,
            ProspectCandidate.legal_hold.is_(False),
            ProspectCandidate.anonymized_at.is_(None),
        )

    @staticmethod
    def _draft_conditions(cutoff: datetime) -> tuple:
        return (
            MessageDraft.created_at < cutoff,
            MessageDraft.body != "[anonymisé]",
        )

    # --- Exécution ---------------------------------------------------------

    def apply(
        self,
        *,
        dry_run: bool = True,
        actor: str = "scheduler",
        now: datetime | None = None,
        on_batch=None,
    ) -> PurgeReport:
        """Exécute la purge. `dry_run=True` ne modifie rien.

        `on_batch` est appelé après chaque lot : c'est ce qui permet au
        planificateur de rafraîchir son battement de cœur et de valider la
        transaction lot par lot.
        """
        moment = now or default_now()
        report = PurgeReport(dry_run=dry_run, started_at=moment)
        plan = self.plan(now=moment)
        report.plan = plan

        if plan.blocked:
            report.stopped_reason = plan.blocked_reason
            report.finished_at = default_now()
            return report

        if dry_run:
            # On restitue le calcul tel quel, en nommant les compteurs comme
            # ceux d'une exécution réelle pour que les deux soient comparables.
            report.counts = {
                "prospects_anonymized": plan.rules[0].eligible,
                "drafts_scrubbed": plan.rules[1].eligible,
                "audit_events_deleted": plan.rules[2].eligible,
                "pilot_events_deleted": plan.rules[3].eligible,
            }
            report.finished_at = default_now()
            return report

        budget = self._settings.purge_max_records_per_run
        counts: dict[str, int] = {}

        for key, handler in (
            ("prospects_anonymized", self._apply_prospects),
            ("drafts_scrubbed", self._apply_drafts),
            ("audit_events_deleted", self._apply_audit),
            ("pilot_events_deleted", self._apply_pilot_events),
        ):
            if budget <= 0:
                report.stopped_reason = (
                    f"Plafond de {self._settings.purge_max_records_per_run} "
                    "enregistrements atteint ; la suite sera traitée à la prochaine "
                    "exécution."
                )
                break
            done = handler(moment, budget, on_batch)
            counts[key] = done
            budget -= done

        report.counts = counts
        report.finished_at = default_now()

        record_audit(
            self._session,
            actor=actor,
            action="retention.purge_applied",
            object_type="purge_run",
            metadata={
                "counts": counts,
                "stopped_reason": report.stopped_reason,
                # Aucune donnée personnelle ici : uniquement des compteurs.
            },
            now=moment,
        )
        self._session.flush()
        return report

    def _apply_prospects(self, moment: datetime, budget: int, on_batch) -> int:
        """Anonymise les prospects échus, par lots.

        Anonymisation et non suppression : les événements de prospection sont
        liés au prospect en cascade, et les supprimer réécrirait les
        indicateurs des périodes passées.
        """
        total = 0
        batch_size = min(self._settings.purge_batch_size, budget)

        while total < budget:
            ids = list(
                self._session.execute(
                    select(ProspectCandidate.id)
                    .where(*self._prospect_conditions(moment))
                    .order_by(ProspectCandidate.retention_until)
                    .limit(min(batch_size, budget - total))
                ).scalars()
            )
            if not ids:
                break

            self._anonymize_prospect_batch(ids, moment)
            total += len(ids)
            if on_batch:
                on_batch({"prospects_anonymized": total})
        return total

    def _anonymize_prospect_batch(self, ids: list[uuid.UUID], moment: datetime) -> None:
        values = dict(ANONYMIZED_PROSPECT_FIELDS)
        values["anonymized_at"] = moment
        self._session.execute(
            update(ProspectCandidate).where(ProspectCandidate.id.in_(ids)).values(**values)
        )
        self._session.execute(
            update(ProspectSource)
            .where(ProspectSource.prospect_id.in_(ids))
            .values(**ANONYMIZED_SOURCE_FIELDS)
        )
        draft_values = {k: (v() if callable(v) else v) for k, v in ANONYMIZED_DRAFT_FIELDS.items()}
        self._session.execute(
            update(MessageDraft).where(MessageDraft.prospect_id.in_(ids)).values(**draft_values)
        )

    def _apply_drafts(self, moment: datetime, budget: int, on_batch) -> int:
        """Efface le contenu des brouillons anciens, en conservant la ligne."""
        rule = self._policy.rule("drafts")
        cutoff = rule.cutoff(moment)
        total = 0

        while total < budget:
            ids = list(
                self._session.execute(
                    select(MessageDraft.id)
                    .where(*self._draft_conditions(cutoff))
                    .order_by(MessageDraft.created_at)
                    .limit(min(self._settings.purge_batch_size, budget - total))
                ).scalars()
            )
            if not ids:
                break
            values = {k: (v() if callable(v) else v) for k, v in ANONYMIZED_DRAFT_FIELDS.items()}
            self._session.execute(
                update(MessageDraft).where(MessageDraft.id.in_(ids)).values(**values)
            )
            total += len(ids)
            if on_batch:
                on_batch({"drafts_scrubbed": total})
        return total

    def _apply_audit(self, moment: datetime, budget: int, on_batch) -> int:
        return self._delete_in_batches(
            AuditEvent,
            AuditEvent.occurred_at < self._policy.rule("audit").cutoff(moment),
            AuditEvent.occurred_at,
            budget,
            on_batch,
            "audit_events_deleted",
        )

    def _apply_pilot_events(self, moment: datetime, budget: int, on_batch) -> int:
        return self._delete_in_batches(
            ProductPilotEvent,
            ProductPilotEvent.occurred_at < self._policy.rule("pilot_events").cutoff(moment),
            ProductPilotEvent.occurred_at,
            budget,
            on_batch,
            "pilot_events_deleted",
        )

    def _delete_in_batches(
        self, model, condition, order_column, budget: int, on_batch, counter_key: str
    ) -> int:
        total = 0
        while total < budget:
            ids = list(
                self._session.execute(
                    select(model.id)
                    .where(condition)
                    .order_by(order_column)
                    .limit(min(self._settings.purge_batch_size, budget - total))
                ).scalars()
            )
            if not ids:
                break
            for row in self._session.execute(select(model).where(model.id.in_(ids))).scalars():
                self._session.delete(row)
            self._session.flush()
            total += len(ids)
            if on_batch:
                on_batch({counter_key: total})
        return total
