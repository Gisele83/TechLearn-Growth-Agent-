"""Purge de rétention en ligne de commande.

    python -m growth_agent.retention.cli            # simulation
    python -m growth_agent.retention.cli --apply    # exécution réelle
    python -m growth_agent.retention.cli --json     # sortie exploitable

Conçu pour être appelé par une tâche planifiée (systemd timer ou cron) : voir
`infra/scheduling/`. Le mode simulation est le défaut, y compris quand la
purge est activée — il faut écrire `--apply` pour que quoi que ce soit soit
modifié.

Codes de sortie : 0 succès, 1 erreur, 2 bloqué par un garde-fou,
3 purge déjà en cours.
"""

from __future__ import annotations

import argparse
import json
import sys

from growth_agent.config import get_settings
from growth_agent.db.session import session_scope
from growth_agent.logging_config import configure_logging
from growth_agent.retention.runner import PurgeLockBusy, run_purge
from growth_agent.retention.service import SafetyStop


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="growth-agent-purge",
        description="Purge les données arrivées à échéance de conservation.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Exécute réellement la purge. Sans cette option, rien n'est modifié.",
    )
    parser.add_argument("--json", action="store_true", help="Sortie JSON, pour la supervision.")
    parser.add_argument(
        "--actor",
        default="scheduler",
        help="Qui déclenche l'exécution (inscrit au journal).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)

    if args.apply and not settings.purge_enabled:
        message = (
            "La purge est désactivée (GA_PURGE_ENABLED=false). "
            "Aucune suppression n'a été effectuée."
        )
        print(message, file=sys.stderr)
        return 2

    try:
        with session_scope() as session:
            run, report = run_purge(
                session, dry_run=not args.apply, actor=args.actor, settings=settings
            )
            payload = {"run_id": str(run.id), "status": run.status, **report.as_dict()}
    except PurgeLockBusy as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except SafetyStop as exc:
        print(f"Purge interrompue par un garde-fou : {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["status"] == "succeeded" else 2

    _print_human(payload, applied=args.apply)
    return 0 if payload["status"] == "succeeded" else 2


def _print_human(payload: dict, *, applied: bool) -> None:
    mode = "EXÉCUTION RÉELLE" if applied else "SIMULATION — aucune donnée modifiée"
    print(f"Purge de rétention — {mode}")
    print(f"Exécution {payload['run_id']} · statut {payload['status']}")
    print()

    plan = payload.get("plan") or {}
    if plan.get("blocked"):
        print(f"BLOQUÉE : {plan['blocked_reason']}")
        print()

    rules = plan.get("rules", [])
    if rules:
        width = max(len(r["label"]) for r in rules)
        print(f"{'Classe de donnée'.ljust(width)}  {'Échus':>8}  {'Table':>8}  Action")
        for rule in rules:
            print(
                f"{rule['label'].ljust(width)}  {rule['eligible']:>8}  "
                f"{rule['table_total']:>8}  {rule['action']}"
            )
            if rule["blocked_reason"]:
                print(f"{' ' * width}  ⚠ {rule['blocked_reason']}")
        print()

    counts = payload.get("counts") or {}
    if counts:
        print("Traité :" if applied else "Serait traité :")
        for key, value in counts.items():
            print(f"  {key.replace('_', ' ')} : {value}")
        print()

    for warning in plan.get("warnings", []):
        print(f"⚠ {warning}")

    if payload.get("stopped_reason"):
        print(f"Arrêt : {payload['stopped_reason']}")

    if not applied and not plan.get("blocked"):
        print("Relancer avec --apply pour appliquer ces changements.")


if __name__ == "__main__":
    raise SystemExit(main())
