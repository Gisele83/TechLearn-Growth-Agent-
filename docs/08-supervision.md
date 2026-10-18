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
| `growth_agent_inbox_last_declared_relay_age_seconds` | âge du dernier relevé **déclaré** de la boîte de réponse |
| `growth_agent_follow_ups_held_by_stale_inbox` | 1 quand les relances sont suspendues faute de relevé |

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

## Relever les réponses — une tâche humaine, tous les jours ouvrés

Les messages partent de `noreply@techlearn-saem.com` avec un `Reply-To` vers
`contact@techlearn-saem.com`, boîte confirmée le 16 octobre 2026. Dès lors
que cette adresse est renseignée, **chaque message promet « répondez STOP et
je ne vous recontacte pas »**.

**Rien ne relève cette boîte automatiquement.** Aucune intégration entrante
n'existe. La promesse n'est donc tenue que si quelqu'un lit. C'est une tâche,
pas une fonctionnalité.

| | |
|---|---|
| **Responsable** | Merveille Nguetse |
| **Fréquence** | chaque jour ouvré |
| **Accès requis** | compte Growth Agent, rôle **`operateur`** — il permet de reporter une réponse et de déclarer un relevé, **sans** donner le droit d'approuver un message. Un compte `lecteur` ne permettrait ni l'un ni l'autre |
| **Suppléance** | **à désigner** — voir « Suppléance et reprise » |
| **Nature du relevé déclaré** | une déclaration nominative, **pas une preuve** de consultation |

### La procédure

1. **Chaque jour ouvré**, relever `contact@techlearn-saem.com`.
2. Pour chaque réponse, la reporter telle quelle :

   ```
   POST /api/v1/replies/analyze
   { "prospect_id": "<identifiant>", "reply_text": "<le texte reçu, intégral>" }
   ```

   Coller le texte **sans le reformuler**. La détection d'opposition porte
   sur les mots employés ; résumer une réponse, c'est risquer d'en retirer
   celui qui compte.
3. Une opposition détectée est enregistrée immédiatement, le prospect passe
   en « perdu », et tout nouvel envoi vers cette adresse est refusé. Rien
   d'autre à faire.
4. En cas de doute sur une réponse qui n'est pas une opposition franche,
   ajouter l'opposition à la main plutôt que de trancher :
   `POST /api/v1/suppressions`. L'erreur de ne pas écrire à quelqu'un qui
   l'aurait accepté est sans conséquence ; l'inverse ne l'est pas.
5. **Si la boîte est vide**, le déclarer quand même :

   ```
   POST /api/v1/replies/relay-check
   ```

   Sans ce geste, « personne n'a écrit » et « personne n'a relevé »
   produisent exactement le même silence — et c'est le second cas qui laisse
   partir une relance vers quelqu'un qui s'était opposé.

### Ce que le relevé déclaré prouve, et ce qu'il ne prouve pas

Il faut le dire franchement, parce que la suite en dépend.

Un appel à `/replies/relay-check` atteste qu'un utilisateur **authentifié
affirme** avoir relevé la boîte, à une date donnée. Il n'atteste ni que la
boîte a été ouverte, ni qu'elle a été lue, ni qu'elle était vide.

Aucune preuve technique de consultation n'est possible depuis Growth Agent :
elle supposerait un accès au serveur de messagerie, que l'application n'a pas
et ne demande pas. Les noms le disent — la mesure s'appelle
`growth_agent_inbox_last_declared_relay_age_seconds`, pas `..._relay_...`.

Ce que la déclaration vaut quand même : elle est **nominative, horodatée,
journalisée**, et son absence se voit. Quelqu'un qui déclarerait sans relever
engagerait sa responsabilité. C'est le niveau de garantie qu'une organisation
peut offrir sur une tâche humaine, et il vaut mieux l'appeler par son nom que
le présenter pour ce qu'il n'est pas.

### Deux conséquences automatiques d'un relevé en retard

**1. L'alerte.** `BoiteDeReponseNonRelevee` se déclenche au bout de **quatre
jours**. Pas trois : un relevé le vendredi matin et le suivant le lundi matin
laissent 72 heures parfaitement normales, et une alerte qui sonne chaque
week-end finit par ne plus être lue. Les destinataires — responsable et
suppléante — sont lus dans `GA_INBOX_RELAY_OWNER_EMAIL` et
`GA_INBOX_RELAY_DEPUTY_EMAIL`, et exposés par `/api/v1/system/alerts` dans le
champ `notify`.

> Ces adresses ne figurent **jamais** dans une étiquette de métrique. Une
> adresse dans Prometheus est une donnée personnelle conservée par un système
> tiers, hors de toute politique de rétention.
>
> Growth Agent **n'envoie pas** lui-même ce courriel d'alerte : l'application
> n'a qu'un seul chemin d'envoi, celui de la prospection, et en ouvrir un
> second affaiblirait la garantie qui veut qu'aucun message ne parte sans
> approbation. L'acheminement revient à l'Alertmanager, ou à la personne qui
> lit `/api/v1/system/alerts`.

**2. Les relances sont suspendues.** Au-delà du même seuil
(`GA_INBOX_RELAY_MAX_AGE_HOURS`, 96 h par défaut), toute **relance** vers un
contact déjà sollicité est mise en attente avec le motif `stale_inbox`, au
lieu de partir. La raison est directe : une opposition pourrait dormir dans
la boîte non relevée.

Ce garde-fou a trois limites, toutes délibérées :

| | |
|---|---|
| **Premier contact** | jamais retenu — personne n'a encore pu répondre |
| **Opposition déjà enregistrée** | **toujours honorée immédiatement**, relevé en retard ou non. Le garde-fou protège de ce qu'on ignore, jamais au prix de ce qu'on sait |
| **Enregistrement d'une opposition** | jamais bloqué — une opposition doit pouvoir entrer à tout moment |

Le blocage est **transitoire** : le brouillon n'est pas condamné et repart
dès qu'un relevé est déclaré. Il laisse un événement `send_blocked` motivé —
une suspension silencieuse serait indiscernable d'un oubli.

### Suppléance et reprise après absence

Une personne unique est un point de panne. La suppléante doit être désignée
nommément, disposer d'un compte au rôle **commercial**, et connaître cette
procédure — pas la découvrir le jour où elle sert.

**Avant une absence prévue** (congés, déplacement) :

1. Renseigner `GA_INBOX_RELAY_DEPUTY_EMAIL` si ce n'est pas déjà fait.
2. Prévenir la suppléante des dates exactes.
3. Vérifier qu'elle peut se connecter et déclarer un relevé — un compte qui
   n'a jamais servi est un compte dont on découvre le mot de passe oublié au
   mauvais moment.

**Si personne ne peut relever** (absence imprévue, arrêt, surcharge) :

1. **Vider `GA_REPLY_TO_EMAIL`** et redéployer. Les messages cessent
   immédiatement d'inviter à répondre et renvoient au seul lien de
   désinscription, qui fonctionne sans personne.
2. Le contrôle de fraîcheur devient alors sans objet et les relances
   repartent : plus aucune boîte n'est promise, donc plus rien à relever.

Ce n'est pas une dégradation du service, c'est la seule attitude honnête :
mieux vaut ne rien promettre que promettre à vide.

**À la reprise :**

1. **Relever la boîte en premier**, avant toute autre chose. Les réponses
   accumulées pendant l'absence peuvent contenir des oppositions non
   enregistrées, et ce sont elles qui comptent.
2. Reporter chaque réponse (`POST /api/v1/replies/analyze`), en commençant
   par les plus anciennes.
3. Remettre `GA_REPLY_TO_EMAIL` si elle avait été vidée, et redéployer.
4. Déclarer le relevé (`POST /api/v1/replies/relay-check`). Les relances
   suspendues repartent d'elles-mêmes.
5. Vérifier `/api/v1/replies/relay-status` : `fresh` doit être vrai et
   `blocks_follow_ups` faux.

### Le délai compte

Une opposition reportée trois jours après son arrivée laisse trois jours
pendant lesquels une relance peut partir. Les plafonds de fréquence
(2 messages par contact et par mois) limitent la casse, mais ne la
suppriment pas.

Si la boîte ne peut pas être relevée pendant plusieurs jours — congés,
absence — **vider `GA_REPLY_TO_EMAIL`**. Les messages cesseront alors
d'inviter à répondre et renverront au lien de désinscription, qui, lui,
fonctionne sans personne. Mieux vaut ne rien promettre que promettre à vide.

### Ce qui reste à décider

Trois façons d'automatiser la réception, par ordre de simplicité :

| Option | Ce qu'elle demande | Ce qu'elle apporte |
|---|---|---|
| Webhook SendGrid *Inbound Parse* | un MX sur un sous-domaine, une route publique | réponses poussées en temps réel |
| Relevé IMAP | des identifiants de boîte, une tâche planifiée | aucun changement DNS |
| Relevé manuel *(en place)* | une personne, chaque jour ouvré | rien à construire |

Tant qu'aucune n'est retenue, le taux de réponse et l'entonnoir
démo/pilote ne comptent que ce qui a été reporté à la main. Les KPI le
disent dans leurs notes ; `/api/v1/system/integrations` aussi.

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
