"""Socle commun aux agents.

Tout passage d'un agent est tracé dans `agent_actions` : durée, jetons
consommés, condensat de l'entrée, résumé de la sortie. Un agent qui échoue
enregistre son échec plutôt que de le taire.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from sqlalchemy.orm import Session

from growth_agent.models.ops import AgentAction
from growth_agent.security.redaction import digest, safe_log_payload

ResultT = TypeVar("ResultT")


@dataclass(slots=True)
class AgentContext:
    """Contexte d'exécution partagé par tous les agents."""

    session: Session
    actor: str = "system"
    actor_role: str = ""
    workflow_run_id: uuid.UUID | None = None
    now: datetime | None = None

    @property
    def moment(self) -> datetime:
        return self.now or datetime.now(UTC)


@dataclass(slots=True)
class AgentResult(Generic[ResultT]):
    """Sortie d'un agent, toujours accompagnée de sa traçabilité."""

    value: ResultT
    summary: dict[str, Any] = field(default_factory=dict)
    model_reference: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    warnings: list[str] = field(default_factory=list)


class BaseAgent(ABC, Generic[ResultT]):
    name: str = "abstract"
    #: Phrase affichée dans l'interface et la documentation : ce que l'agent
    #: s'interdit de faire.
    guardrail: str = ""

    @abstractmethod
    def run(self, context: AgentContext, **kwargs: Any) -> AgentResult[ResultT]: ...

    def execute(self, context: AgentContext, **kwargs: Any) -> AgentResult[ResultT]:
        """Exécute `run` en journalisant l'action, succès comme échec."""
        started = time.monotonic()
        try:
            result = self.run(context, **kwargs)
        except Exception as exc:
            self._log_action(
                context,
                status="failed",
                kwargs=kwargs,
                duration_ms=int((time.monotonic() - started) * 1000),
                error_message=f"{type(exc).__name__}: {exc}",
            )
            raise
        self._log_action(
            context,
            status="succeeded",
            kwargs=kwargs,
            duration_ms=int((time.monotonic() - started) * 1000),
            result=result,
        )
        return result

    def _log_action(
        self,
        context: AgentContext,
        *,
        status: str,
        kwargs: dict[str, Any],
        duration_ms: int,
        result: AgentResult[ResultT] | None = None,
        error_message: str = "",
    ) -> None:
        action = AgentAction(
            workflow_run_id=context.workflow_run_id,
            agent_name=self.name,
            action=kwargs.get("action", "run"),
            target_type=str(kwargs.get("target_type", "")),
            target_id=kwargs.get("target_id"),
            status=status,
            input_digest=digest(sorted(kwargs.keys())),
            output_summary=safe_log_payload(result.summary) if result else {},
            model_reference=result.model_reference if result else "",
            input_tokens=result.input_tokens if result else 0,
            output_tokens=result.output_tokens if result else 0,
            duration_ms=duration_ms,
            error_message=error_message[:2000],
        )
        context.session.add(action)
        context.session.flush()
