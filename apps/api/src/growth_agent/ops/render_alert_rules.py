"""Engendre `monitoring/alerts.rules.yml` depuis `ALERT_RULES`.

    python -m growth_agent.ops.render_alert_rules > monitoring/alerts.rules.yml

Un seuil défini à deux endroits finit par se contredire, et c'est toujours
celui qu'on ne regarde pas qui est appliqué. Le fichier de règles est donc
engendré, et un test échoue si la copie versionnée a divergé.
"""

from __future__ import annotations

import sys

from growth_agent.services.observability import ALERT_RULES, AlertRule

#: Durée pendant laquelle la condition doit tenir avant que l'alerte parte.
#: Un pic d'une seconde n'est pas un incident ; une condition qui tient cinq
#: minutes en est un.
FOR_DURATION = {"critical": "2m", "warning": "10m"}

HEADER = """# Règles d'alerte Prometheus pour TechLearn Growth Agent.
#
# ENGENDRÉ — ne pas modifier à la main.
#   python -m growth_agent.ops.render_alert_rules > monitoring/alerts.rules.yml
#
# Les seuils vivent dans apps/api/src/growth_agent/services/observability.py
# (ALERT_RULES), d'où les lit aussi /api/v1/system/alerts.
"""


def _expression(rule: AlertRule) -> str:
    operator = ">" if rule.comparison == "gt" else "<"
    return f"{rule.metric} {operator} {_number(rule.threshold)}"


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render() -> str:
    lines = [HEADER, "groups:", "  - name: growth-agent", "    rules:"]
    for rule in ALERT_RULES:
        lines += [
            f"      - alert: {rule.name}",
            f"        expr: {_expression(rule)}",
            f"        for: {FOR_DURATION.get(rule.severity, '10m')}",
            "        labels:",
            f"          severity: {rule.severity}",
            "          service: growth-agent",
            "        annotations:",
            f"          summary: {_quote(rule.summary)}",
            f"          runbook: {_quote(rule.runbook)}",
        ]
    return "\n".join(lines) + "\n"


def main() -> int:
    sys.stdout.write(render())
    return 0


if __name__ == "__main__":  # pragma: no cover - point d'entrée
    raise SystemExit(main())
