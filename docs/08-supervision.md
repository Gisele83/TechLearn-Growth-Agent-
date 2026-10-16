# Supervision et exploitation

Ce document dit quoi regarder, et quoi faire quand ça sonne. Il part d'un
principe simple : **une alerte sans geste associé réveille quelqu'un qui ne
saura pas quoi faire, et finit par être ignorée.** Chaque seuil ci-dessous
porte donc son geste.

---

## Les trois sondes

| Sonde | Pour qui | Ce qu'elle regarde | Authentification |
|---|---|---|---|
| `GET /health/live` | l'orchestrateur, pour décider de redémarrer | le processus, rien d'autre | aucune |
| `GET /health/ready` | le répartiteur, pour décider de router | base, révision du schéma | aucune |
| `GET /health` | un humain | détail, connecteurs, avertissements | aucune |

**Pourquoi deux sondes et pas une.** Un orchestrateur qui redémarre
l'application parce que la base est tombée ne répare rien : il ajoute une
rafale de redémarrages à une panne qui ne le concerne pas, et perd au passage
les connexions encore valides. `/health/live` n'interroge donc aucune
dépendance : si le processus répond, il est vivant.

`/health/ready` renvoie **503** dès qu'une condition manque, pour que le
trafic soit retiré sans intervention humaine. Deux conditions :

* la base répond ;
* le schéma est à la révision que ce code attend.

Le schéma en fait partie parce qu'un conteneur démarré avant la fin des
migrations répond à tout — et se trompe sur la moitié des requêtes. Exemple
réel, observé en laboratoire sur une base restée en 0003 alors que le code
attend 0004 :

```
/health/live  : 200  <- le processus va bien, aucun redémarrage demandé
/health/ready : 503  <- retiré du trafic
    {"current": "0003_unsubscribe_and_reservation",
     "expected": "0004_crm_idempotency", "up_to_date": false}
```

Les trois sondes et `/metrics` sont exemptées du limiteur de débit : les
limiter revient à se rendre aveugle au moment précis où l'on a besoin de
voir, et à faire retirer du trafic une instance dont le seul défaut était
d'être occupée.

---

## Métriques

`GET /metrics`, format Prometheus, authentifié par `GA_METRICS_TOKEN`
présenté en `Bearer`. **Sans ce jeton, l'endpoint répond 404** — il n'est
jamais ouvert, même temporairement.

### Ce qu'elles ne contiennent pas

Aucune donnée personnelle. Les métriques sont lues par un système tiers et
conservées longtemps, hors de toute politique de rétention : une adresse qui
y entre n'en sort plus. Les seules étiquettes utilisées sont `product`,
`mode` et `reason` — trois ensembles fermés, donc aucune explosion de
cardinalité non plus.

Elles contiennent en revanche des **chiffres commerciaux** (organisations
contactées, oppositions, envois bloqués). C'est la raison du jeton.

### Ce qu'elles contiennent

| Métrique | Ce qu'elle dit |
|---|---|
| `growth_agent_database_up` | 1 si la base répond |
| `growth_agent_info{version,environment}` | quelle version tourne où |
| `growth_agent_email_sending_enabled` | **1 si un message peut réellement partir** |
| `growth_agent_configuration_warnings` | incohérences de configuration |
| `growth_agent_sends_24h{product,mode}` | messages remis, réel et simulé séparés |
| `growth_agent_sends_blocked_24h{reason}` | refus d'un garde-fou, par motif |
| `growth_agent_deliveries_uncertain` | envois dont on ignore s'ils sont partis |
| `growth_agent_reservations_stale` | réservations abandonnées par un processus mort |
| `growth_agent_approvals_pending` | file d'attente humaine |
| `growth_agent_suppression_entries` | oppositions enregistrées |
| `growth_agent_purge_last_success_age_seconds` | âge de la dernière purge réelle |
| `growth_agent_purge_failures_24h` | purges en échec ou interrompues |
| `growth_agent_purge_blocked_24h` | purges arrêtées par un garde-fou |
| `growth_agent_backup_last_success_age_seconds` | âge de la dernière sauvegarde vérifiée |

### Sans Prometheus

`GET /api/v1/system/alerts` calcule les mêmes seuils côté serveur et renvoie
les alertes déclenchées avec leur geste. Tout le monde n'a pas une pile de
supervision, et une alerte qui en exige une n'alerte personne chez qui elle
manque.

---

## Alertes, et ce qu'il faut faire

Les seuils sont définis **une seule fois**, dans `ALERT_RULES`
(`apps/api/src/growth_agent/services/observability.py`). `monitoring/alerts.rules.yml`
en est engendré, et un test échoue si les deux divergent : un seuil défini à
deux endroits finit par se contredire, et c'est toujours celui qu'on ne
regarde pas qui est appliqué.

### Critiques

**`EnvoiReelActiveSansAutorisation`** — l'envoi réel est activé.
→ Vérifier qu'un procès-verbal de mise en service l'autorise (domaine,
SPF/DKIM/DMARC, cadre juridique du pays ciblé). Sinon, repasser
`GA_EMAIL_SENDING_ENABLED` à `false` **immédiatement**. Cette alerte existe
parce que l'activation accidentelle est le pire scénario de ce système.

**`EnvoisIssueInconnue`** — des envois se sont terminés sans réponse lisible.
→ Ces messages sont peut-être partis. Ils ne seront **jamais rejoués
automatiquement**. Consulter le journal du fournisseur, puis trancher message
par message. Ne pas relancer en masse.

**`SauvegardeEnRetard`** — plus de 48 h sans sauvegarde vérifiée.
→ Vérifier la tâche et l'espace disque de destination. Profiter de
l'incident pour rejouer une restauration d'essai.

**`BaseInjoignable`**.
→ `/health/ready` renvoie déjà 503 et le trafic est retiré. **Ne pas
redémarrer l'application** : elle n'est pas en cause.

### Avertissements

**`ReservationsAbandonnees`** — une réservation de plus de 15 minutes signale
un processus mort entre la réservation et l'envoi. Le message est bloqué tant
qu'un humain n'a pas tranché.

**`EnvoisBloquesEnHausse`** — au-delà de 20 blocages sur 24 h. Une hausse
soudaine signale un problème de données ou de configuration, pas une série de
refus légitimes. Regarder la répartition par motif avant toute action.

**`PurgeEnRetard`** — plus de 48 h sans purge réussie : des données dépassent
leur durée de conservation.

**`PurgeEnEchec`** — lire `error_message`. La purge étant idempotente, la
relancer est sans danger.

**`PurgeArreteeParGardeFou`** — ce n'est **pas** une panne : le volume à
supprimer dépassait le plafond et le garde-fou a fait son travail. Lire
`stopped_reason` et comprendre d'où vient le volume. *Ne jamais relever le
plafond pour faire passer une purge qu'on n'a pas expliquée.*

**`ConfigurationIncoherente`** — détail dans `/health`.

---

## Sauvegarde et restauration

`monitoring/backup.sh` fait le dump au format `custom`, le chiffre, **vérifie
que l'archive est lisible**, et seulement alors touche le fichier témoin lu
par `growth_agent_backup_last_success_age_seconds`. L'ordre est délibéré :
toucher le témoin plus tôt ferait croire à une sauvegarde réussie alors que
seule la commande a été lancée — le genre de mensonge qu'on ne découvre qu'au
moment de restaurer.

Le témoin est un fichier, pas une ligne en base : une sauvegarde doit pouvoir
être constatée **même quand la base est en panne**, c'est-à-dire précisément
quand cela compte.

### Ce que la restauration réintroduit

Vérifié en laboratoire sur PostgreSQL 16 : une sauvegarde prise **avant** une
purge, restaurée **après**, réintroduit les données purgées en clair.

| | Avant purge | Après purge | Après restauration |
|---|---|---|---|
| Prospects | 2 | 2 | 2 |
| Anonymisés | 0 | 1 | **0** |
| Adresses en clair | 2 | 1 | **2** |

Ce n'est pas un défaut : c'est ce que fait une sauvegarde. Mais cela a une
conséquence directe sur la politique de conservation.

**Règle.** La durée de rétention des sauvegardes doit être **cohérente avec
la durée de conservation des données**. Garder treize mois de sauvegardes
quotidiennes d'une base dont les prospects s'anonymisent au bout de six mois
revient à conserver treize mois de données personnelles. Soit les sauvegardes
anciennes sont purgées au même rythme, soit la politique l'assume
explicitement et le documente auprès des personnes concernées.

**En cas de restauration réelle, relancer la purge immédiatement après.** Une
restauration ramène des données qui n'avaient plus le droit d'être là.

---

## Épreuve de restauration

À rejouer au moins une fois par trimestre, et après tout changement de
schéma. Une sauvegarde jamais restaurée n'est pas une sauvegarde.

```sh
# 1. Restaurer sur une base vierge — jamais par-dessus la production
createdb growth_restore
gpg --decrypt sauvegarde.dump.gpg | pg_restore -d growth_restore \
    --no-owner --no-privileges

# 2. L'application démarre-t-elle dessus ? C'est la seule question.
GA_DATABASE_URL="postgresql+psycopg://…/growth_restore" \
  curl -fsS localhost:8000/health/ready | jq .checks

# Attendu : {"database":"ok","schema":"ok", …, "up_to_date":true}
```

Si `up_to_date` est faux, la sauvegarde est antérieure à une migration :
appliquer `alembic upgrade head` sur la base restaurée **avant** de la
déclarer utilisable.

---

## Ce qui reste à faire en production

Ces points ne peuvent pas être réglés depuis le dépôt. Ils figurent à la
checklist de mise en service (`docs/03-deploiement.md`).

- [ ] `GA_METRICS_TOKEN` posé, et le collecteur configuré avec
- [ ] `GA_BACKUP_STAMP_PATH` posé, et `monitoring/backup.sh` en tâche planifiée
- [ ] `monitoring/alerts.rules.yml` chargé dans le Prometheus réel
- [ ] Épreuve de restauration passée sur l'instance de production
- [ ] Destinataire des alertes nommé — une alerte sans destinataire n'alerte personne
- [ ] Expiration du certificat TLS surveillée (hors périmètre de l'application)
- [ ] Espace disque surveillé (hors périmètre de l'application)
