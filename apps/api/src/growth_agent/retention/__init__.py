"""Purge des données arrivées à échéance de conservation.

Point d'entrée : `PurgeService` pour calculer et exécuter, `run_purge` pour
l'exécution planifiée avec verrou et reprise.
"""

from growth_agent.retention.policy import (
    PROTECTED_TABLES,
    RetentionPolicy,
    RetentionRule,
    build_policy,
)
from growth_agent.retention.runner import PurgeLockBusy, run_purge
from growth_agent.retention.service import PurgePlan, PurgeReport, PurgeService, SafetyStop

__all__ = [
    "PROTECTED_TABLES",
    "PurgeLockBusy",
    "PurgePlan",
    "PurgeReport",
    "PurgeService",
    "RetentionPolicy",
    "RetentionRule",
    "SafetyStop",
    "build_policy",
    "run_purge",
]
