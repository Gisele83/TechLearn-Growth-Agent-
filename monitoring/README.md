# Supervision

Trois fichiers, et aucun n'est obligatoire : Growth Agent sait se superviser
sans pile externe, via `GET /api/v1/system/alerts`. Ce qui suit sert à ceux
qui ont déjà un Prometheus.

| Fichier | Rôle |
|---|---|
| `alerts.rules.yml` | Règles d'alerte Prometheus |
| `scrape.example.yml` | Extrait de configuration du collecteur |
| `backup.sh` | Sauvegarde chiffrée, qui pose le témoin lu par la métrique |

## Les seuils ne sont définis qu'une fois

`alerts.rules.yml` est **engendré** depuis `ALERT_RULES`
(`apps/api/src/growth_agent/services/observability.py`) :

```sh
python -m growth_agent.ops.render_alert_rules > monitoring/alerts.rules.yml
```

Un test (`test_alert_rules_file_matches_the_code`) échoue si le fichier a
divergé du code. Deux définitions d'un même seuil finissent toujours par se
contredire, et c'est toujours celle qu'on ne regarde pas qui est appliquée.

## Métriques

`GET /metrics`, authentifié par `GA_METRICS_TOKEN` en `Bearer`. Sans ce
jeton, l'endpoint répond 404 : il n'est jamais ouvert par défaut. Les mesures
portent des chiffres commerciaux — organisations contactées, oppositions,
envois bloqués — et aucune donnée personnelle : les seules étiquettes sont le
code produit, le mode d'envoi et le motif de blocage, trois ensembles fermés.

## Sondes

| Sonde | Pour qui | Ce qu'elle regarde |
|---|---|---|
| `/health/live` | l'orchestrateur, pour redémarrer | rien d'autre que le processus |
| `/health/ready` | le répartiteur, pour router | base, révision du schéma |
| `/health` | un humain | détail, connecteurs, avertissements |

La distinction compte : un orchestrateur qui redémarre l'application parce
que la base est tombée n'a rien réparé, il a ajouté une rafale de
redémarrages à une panne qui ne le concernait pas.
