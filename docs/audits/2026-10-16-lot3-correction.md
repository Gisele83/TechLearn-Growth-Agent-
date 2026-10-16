# Lot 3 — Rapport de correction

**Date** : 16 octobre 2026
**Périmètre autorisé** : exploitation — sondes, métriques, alertes, et P2-13
(sauvegardes documentées mais jamais exécutées).
**Hors périmètre** : lot 4 — non commencé.
**Déploiement** : aucun.

---

## 1. Ce qui manquait

L'audit relevait, pour l'exploitation : « sonde de vivacité distincte de la
sonde de disponibilité, métriques exportables, alertes sur les seuils qui
comptent ». Et pour P2-13 : la politique de sauvegarde était écrite, la
procédure de restauration aussi, **mais aucune n'avait jamais été exécutée
sur PostgreSQL**.

---

## 2. Trois sondes au lieu d'une

| Sonde | Pour qui | Ce qu'elle regarde |
|---|---|---|
| `/health/live` | l'orchestrateur, pour redémarrer | le processus, rien d'autre |
| `/health/ready` | le répartiteur, pour router | base, révision du schéma |
| `/health` | un humain | détail, connecteurs, avertissements |

La distinction n'est pas cosmétique. Un orchestrateur qui redémarre
l'application parce que la base est tombée ne répare rien : il ajoute une
rafale de redémarrages à une panne qui ne le concerne pas, et perd au passage
les connexions encore valides.

`/health/ready` vérifie aussi que **le schéma est à la révision que ce code
attend**, parce qu'un conteneur démarré avant la fin des migrations répond à
tout et se trompe sur la moitié des requêtes. La tête attendue est lue dans
les fichiers de migration du dépôt, pas codée en dur : elle suit le code, ce
qui est exactement la question posée — « ce binaire et cette base sont-ils de
la même génération ? »

Vérifié sur une base réelle restée en 0003 pendant que le code attend 0004 :

```
/health/live  : 200  <- le processus va bien, aucun redémarrage demandé
/health/ready : 503  <- retiré du trafic
    {"current": "0003_unsubscribe_and_reservation",
     "expected": "0004_crm_idempotency", "up_to_date": false}
```

Sondes et `/metrics` sont exemptées du limiteur de débit : les limiter
revient à se rendre aveugle au moment précis où l'on a besoin de voir.

---

## 3. Métriques

`GET /metrics`, format Prometheus écrit à la main — le format tient en
quelques lignes, et une dépendance de plus dans un connecteur commercial est
une dépendance à suivre, mettre à jour et auditer.

**Protégée par un jeton dédié** (`GA_METRICS_TOKEN`, en `Bearer`, comparé à
temps constant) plutôt que par un compte utilisateur : un collecteur n'est
pas une personne. Sans jeton configuré, l'endpoint répond **404**, jamais
« ouvert en attendant » — un endpoint de métriques laissé ouvert le temps de
configurer ne se referme jamais.

**Aucune donnée personnelle.** Les métriques sont conservées longtemps par un
système tiers, hors de toute politique de rétention : une adresse qui y entre
n'en sort plus. Les seules étiquettes sont `product`, `mode` et `reason` —
trois ensembles fermés, donc pas d'explosion de cardinalité non plus. Un test
le vérifie en cherchant l'adresse, l'identifiant et le nom d'un prospect réel
dans la sortie.

Quatorze mesures, choisies pour ce système plutôt que par habitude. Les plus
utiles ne sont pas des compteurs de requêtes :

- `growth_agent_email_sending_enabled` — **1 si un message peut réellement
  partir.** L'activation accidentelle est le pire scénario de ce système ;
  elle a sa propre alerte critique.
- `growth_agent_deliveries_uncertain` — envois dont on ignore s'ils sont
  partis, jamais rejoués automatiquement.
- `growth_agent_reservations_stale` — réservations de plus de 15 minutes :
  un processus est mort entre la réservation et l'envoi.
- `growth_agent_sends_24h{product,mode}` — réel et simulé séparés, comme
  partout depuis le lot 1.
- `growth_agent_backup_last_success_age_seconds` — voir § 5.

---

## 4. Alertes

Dix règles, chacune avec **le geste à faire**. Une alerte sans geste associé
réveille quelqu'un qui ne saura pas quoi faire, et finit par être ignorée —
un test refuse une règle dont le `runbook` est vide ou trop vague.

Les seuils sont définis **une seule fois**, dans `ALERT_RULES`.
`monitoring/alerts.rules.yml` en est engendré
(`python -m growth_agent.ops.render_alert_rules`), et un test échoue si la
copie versionnée a divergé : un seuil défini à deux endroits finit par se
contredire, et c'est toujours celui qu'on ne regarde pas qui est appliqué.

`GET /api/v1/system/alerts` calcule les mêmes seuils côté serveur, pour que
la supervision ne suppose pas une pile Prometheus — tout le monde n'en a pas
une, et une alerte qui en exige une n'alerte personne chez qui elle manque.

---

## 5. Sauvegarde et restauration — exécutées pour de vrai (P2-13)

### 5.1 L'épreuve

Sur un PostgreSQL 16 isolé, avec des données synthétiques comprenant un
prospect échu et un prospect courant :

```
1. SAUVEGARDE        pg_dump --format=custom       → archive 60K
2. VÉRIFICATION      pg_restore --list             → 21 tables de données lisibles
3. PURGE RÉELLE      retention.cli --apply         → 1 prospect anonymisé
4. APRÈS PURGE       prospects 2, anonymisés 1, adresses en clair 1
5. RESTAURATION      pg_restore sur base vierge    → terminée
6. APRÈS RESTAURATION prospects 2, anonymisés 0, adresses en clair 2
7. DÉMARRAGE         /health/ready → 200, révision 0004 en tête
                     connexion admin → 200, KPI recalculés → 200, 2 produits
```

L'étape 7 est la seule qui compte vraiment, et elle passe : l'application
démarre sur la base restaurée, s'authentifie, et recalcule ses indicateurs.

### 5.2 Ce que la restauration réintroduit

| | Avant purge | Après purge | Après restauration |
|---|---|---|---|
| Prospects | 2 | 2 | 2 |
| Anonymisés | 0 | 1 | **0** |
| Adresses en clair | 2 | 1 | **2** |

Ce n'est pas un défaut : c'est ce que fait une sauvegarde. Mais la
conséquence est directe et doit être écrite. **La durée de rétention des
sauvegardes doit être cohérente avec la durée de conservation des données.**
Garder treize mois de sauvegardes quotidiennes d'une base dont les prospects
s'anonymisent à six mois revient à conserver treize mois de données
personnelles. Et toute restauration réelle doit être suivie d'une purge :
elle ramène des données qui n'avaient plus le droit d'être là.

Le même constat avait été fait au stade R3, mais **sur SQLite**. Il est
maintenant établi sur le moteur réel.

### 5.3 Le script de sauvegarde

`monitoring/backup.sh` fait le dump au format `custom`, le chiffre, **vérifie
que l'archive est lisible**, et seulement alors touche le fichier témoin lu
par la métrique. L'ordre est délibéré : toucher le témoin plus tôt ferait
croire à une sauvegarde réussie alors que seule la commande a été lancée —
le genre de mensonge qu'on ne découvre qu'au moment de restaurer.

Le témoin est un fichier et non une ligne en base : une sauvegarde doit
pouvoir être constatée **même quand la base est en panne**, c'est-à-dire
précisément quand cela compte.

---

## 6. Deux défauts trouvés en exécutant

Ni l'un ni l'autre n'aurait été vu en relisant le code. Les deux sont
apparus en lançant la supervision contre un PostgreSQL réel.

### D8 — La métrique de purge ne sortait jamais

La supervision filtrait sur `status == "completed"`. Le runner n'écrit jamais
ce statut : il écrit `succeeded`, `blocked`, `failed`, `interrupted`,
`running`. Conséquence : `growth_agent_purge_last_success_age_seconds`
n'apparaissait **pas** dans la sortie, et l'alerte « purge en retard »
n'aurait jamais pu se déclencher. Une surveillance muette, c'est-à-dire pire
que pas de surveillance — on croit regarder.

Mes propres tests ne l'avaient pas vu : ils reprenaient le même statut
inventé. Le test est devenu le complice du défaut. C'est l'exécution contre
la vraie base, après une vraie purge, qui l'a révélé.

Corrigé en deux temps : les statuts sont désormais **nommés dans le runner**
(`PURGE_SUCCEEDED`, `PURGE_BLOCKED`, `PURGE_FAILED`, `PURGE_INTERRUPTED`,
`PURGE_RUNNING`) et importés par la supervision au lieu d'être recopiés. Et
un test (`test_the_metrics_use_the_statuses_the_runner_actually_writes`)
écrit une exécution de chaque statut réel et vérifie que les métriques les
comptent — il prévient la classe de défaut, pas seulement ce cas-ci.

Au passage, un arrêt par garde-fou était compté comme un échec. Ce n'en est
pas un : c'est le garde-fou qui fait son travail devant un volume anormal, et
cela demande de comprendre, pas de dépanner. Mesure et alerte séparées
(`growth_agent_purge_blocked_24h`), avec un runbook qui dit explicitement de
**ne jamais relever le plafond pour faire passer une purge qu'on n'a pas
expliquée**.

### D9 — « Jamais de sauvegarde » s'affichait comme « pas de mesure »

Quand aucune sauvegarde n'a jamais abouti, l'âge vaut `+Inf` — correct pour
Prometheus. Mais JSON ne sait pas écrire l'infini : l'alerte ressortait avec
`"value": null`, qui se lit « aucune mesure »,
c'est-à-dire **l'inverse de ce que l'alerte signale**.

```
avant : [critical] SauvegardeEnRetard : …=None (seuil 172800)
après : [critical] SauvegardeEnRetard : … = jamais constaté (seuil 172800)
```

Corrigé par un champ `value_text` explicite, côté alertes comme côté mesures.

---

## 7. Preuves

```
407 passed, 4 deselected          (dont 5 sur PostgreSQL)
  tests/test_observability.py     31 tests
 27 passed                        (web)
ruff check / format               All checks passed
tsc --noEmit                      OK
make secrets                      Aucun secret détecté
```

Épreuve de sauvegarde/restauration : § 5.1, reproductible via
`docs/08-supervision.md`.

---

## 8. Risques résiduels

**R14 — Rien de tout cela n'a été éprouvé sur une instance de production, qui
n'existe pas.** L'épreuve de restauration a eu lieu en laboratoire, sur une
base de quelques dizaines de lignes. Un dump de production prendra plus
longtemps, et c'est la durée qui révèle les vrais problèmes : verrous, espace
disque, fenêtre de restauration. La checklist de mise en service l'exige
avant l'ouverture du service.

**R15 — Le fichier témoin de sauvegarde est déclaratif.** `backup.sh` le
touche après avoir vérifié que l'archive est lisible, mais rien n'empêche de
le toucher à la main. La métrique dit « quelqu'un a affirmé qu'une sauvegarde
a réussi », pas « une sauvegarde existe et se restaure ». Seule l'épreuve
trimestrielle le prouve.

**R16 — Les métriques sont celles d'un processus.** Derrière plusieurs
répliques, chacune rapporte les mêmes agrégats de base (ils sont corrects, vu
qu'ils viennent de la base) mais les états propres au processus — limiteur de
débit CRM du lot 2, limiteur de requêtes — restent locaux. Un limiteur
partagé via Redis serait nécessaire au-delà d'un processus.

**R17 — Aucune métrique de latence ni de taux d'erreur HTTP.** Choix assumé :
pour une application commerciale à faible trafic, un compteur de requêtes par
seconde n'apprend rien que `/health/ready` ne dise déjà, et il aurait fallu
un middleware supplémentaire avec sa cardinalité à surveiller. À revoir si le
trafic change de nature.

**R18 — Les alertes n'ont aucun destinataire.** Le fichier de règles est
prêt, le calcul côté serveur aussi, mais personne n'est nommé. Une alerte
sans destinataire n'alerte personne. C'est une ligne de la checklist de mise
en service, et elle ne peut pas être cochée depuis le dépôt.

**R19 — `/metrics` est protégé par un jeton partagé.** Pas de rotation
automatique, pas de révocation individuelle. Acceptable pour un collecteur
unique sur un réseau privé ; à revoir si plusieurs systèmes le lisent.

---

## 9. Nettoyage

Le PostgreSQL temporaire (`/var/tmp/growth-pgtest`) a été recréé pour
l'épreuve de sauvegarde, puis arrêté et supprimé — données, archives et
volumes compris. Mêmes conditions qu'aux lots 1 et 2 : aucun port TCP à
l'écoute, socket Unix seule, données entièrement synthétiques, aucune
connexion vers Maintrix ou EduCore.

---

## 10. Ce qui n'a pas été fait

Le lot 4 n'a pas été commencé. Aucun déploiement. `GA_EMAIL_SENDING_ENABLED`
reste à `false`. Aucune modification de Maintrix ni d'EduCore.
