# Relevé de la boîte de réponse — rapport des changements

**Date** : 17 octobre 2026
**Origine** : validation de `POST /api/v1/replies/relay-check` et du seuil de
quatre jours, sous réserve de sept améliorations.
**Déploiement** : aucun. Aucun envoi réel, aucune modification DNS.

---

## 1. Les sept exigences, une par une

### 1. Déclaration associée à l'utilisateur authentifié, horodatée, auditée

Fait. `POST /api/v1/replies/relay-check` exige `CAN_OPERATE`, journalise
`inbox.relay_declared` avec `actor`, `actor_role` et `occurred_at`, et
renvoie ces éléments à l'appelant :

```json
{
  "status": "déclaration enregistrée",
  "declared_by": "merveille@techlearn-saem.com",
  "declared_at": "2026-10-17T08:12:04+00:00",
  "nature": "déclaration nominative, pas une preuve de consultation",
  "freshness": { "fresh": true, "max_age_seconds": 345600.0, … }
}
```

La trace ne contient **ni adresse de prospect, ni contenu de réponse** :
seulement qui a déclaré quoi, et quand.

### 2. Ne pas la faire passer pour une preuve de consultation

C'est l'exigence qui a le plus changé le code, parce qu'elle change les
noms. Une déclaration atteste qu'un utilisateur **affirme** avoir relevé la
boîte ; elle n'atteste ni qu'elle a été ouverte, ni lue, ni vide. Aucune
preuve technique n'est possible d'ici : elle supposerait un accès au serveur
de messagerie, que Growth Agent n'a pas et ne demande pas.

| | Avant | Après |
|---|---|---|
| Action auditée | `inbox.relayed` | `inbox.relay_declared` |
| Métrique | `..._inbox_last_relay_age_seconds` | `..._inbox_last_declared_relay_age_seconds` |
| Réponse de l'API | — | champ `nature` explicite |
| Métadonnée d'audit | — | `"nature": "déclaration"` |
| Module | — | `services/inbox_relay.py`, dont l'en-tête dit pourquoi |

L'ancien nom d'action reste reconnu, pour que les traces déjà écrites
continuent de compter.

Ce que la déclaration vaut quand même, et c'est dit comme tel dans la
documentation : nominative, horodatée, journalisée, et **son absence se
voit**. C'est le niveau de garantie qu'une organisation peut offrir sur une
tâche humaine.

### 3. Contrôle de fraîcheur avant toute relance

Fait, dans le service d'envoi, comme contrôle 5 bis. Au-delà de
`GA_INBOX_RELAY_MAX_AGE_HOURS` (96 h par défaut), une **relance** est
suspendue avec le motif `stale_inbox` au lieu de partir.

Trois limites, toutes délibérées :

| Cas | Comportement | Pourquoi |
|---|---|---|
| Premier contact | part normalement | personne n'a encore pu répondre ; le retenir n'empêcherait rien |
| Pas de boîte promise | aucun contrôle | sans invitation à répondre, aucun relevé n'est dû |
| Relance, relevé frais | part normalement | — |
| Relance, relevé périmé | **suspendue** | une opposition pourrait dormir dans la boîte |

Le blocage est **transitoire** : le brouillon n'est pas condamné et repart
dès qu'un relevé est déclaré. Il laisse un événement `send_blocked` motivé —
une suspension silencieuse serait indiscernable d'un oubli.

Pourquoi 96 h et non 48 : un relevé le vendredi matin et le suivant le lundi
matin laissent 72 heures parfaitement normales. Un seuil à 48 h suspendrait
toutes les relances chaque lundi matin, et on apprendrait vite à l'ignorer.
Le réglage est exposé pour qui veut une discipline plus stricte.

### 4. Traitement immédiat des oppositions déjà enregistrées

Maintenu, et c'est le point le plus important de l'ensemble. Le contrôle de
fraîcheur est placé **après** le contrôle d'opposition, pas avant. Une
opposition déjà connue écarte le contact immédiatement, que le relevé soit
en retard ou non.

Deux tests le verrouillent :

* `test_a_recorded_objection_is_honoured_even_with_a_stale_inbox` — avec un
  relevé vieux de dix jours, le motif de refus est `suppressed`, pas
  `stale_inbox` : le contact est écarté pour ce qu'on sait, pas mis en
  attente pour ce qu'on ignore ;
* `test_an_objection_can_always_be_recorded_whatever_the_relay_age` — une
  opposition peut **entrer** à tout moment ; le garde-fou porte sur l'envoi,
  jamais sur l'enregistrement.

Formulé autrement : ce garde-fou protège de ce qu'on ignore, jamais au prix
de ce qu'on sait déjà.

### 5. Alerte à la responsable et à sa suppléante

`GA_INBOX_RELAY_OWNER_EMAIL` et `GA_INBOX_RELAY_DEPUTY_EMAIL` portent les
destinataires. `/api/v1/system/alerts` les renvoie dans le champ `notify`
de l'alerte concernée, et le runbook y renvoie.

Deux décisions à signaler, parce qu'elles limitent ce que j'ai livré.

**Ces adresses ne figurent dans aucune métrique.** Une adresse dans une
étiquette Prometheus est une donnée personnelle conservée par un système
tiers, hors de toute politique de rétention. Un test le vérifie
(`test_no_address_ever_reaches_the_metrics`).

**Growth Agent n'envoie pas lui-même ce courriel.** L'application n'a qu'un
seul chemin d'envoi, celui de la prospection, soumis à approbation et
actuellement désactivé. En ouvrir un second pour les alertes affaiblirait
cette garantie, et l'envoi d'alerte survit mal à la panne qu'il signale.
L'acheminement revient à l'Alertmanager — ou, en attendant, à la personne
qui lit `/api/v1/system/alerts`. **C'est une dépendance organisationnelle,
pas une fonctionnalité livrée.**

L'absence de suppléante est elle-même devenue un avertissement de
configuration : *« Aucune suppléance déclarée : une absence de la
responsable interromprait le relevé sans relais. »*

### 6. Procédure de suppléance et reprise après absence

Écrite dans `docs/08-supervision.md`, section « Suppléance et reprise après
absence ». Elle couvre trois moments :

* **avant une absence prévue** — désigner, prévenir, et *vérifier que la
  suppléante peut effectivement se connecter* : un compte qui n'a jamais
  servi est un compte dont on découvre le mot de passe oublié au mauvais
  moment ;
* **quand personne ne peut relever** — vider `GA_REPLY_TO_EMAIL`. Les
  messages cessent d'inviter à répondre et renvoient au seul lien de
  désinscription ; le contrôle de fraîcheur devient sans objet et les
  relances repartent, puisque plus aucune boîte n'est promise ;
* **à la reprise** — relever la boîte **en premier**, reporter les réponses
  de la plus ancienne à la plus récente, remettre l'adresse, déclarer le
  relevé, et vérifier `/api/v1/replies/relay-status`.

### 7. Tests, sans aucun envoi réel

39 tests ajoutés ou modifiés. Aucun n'ouvre de connexion : les fournisseurs
sont des doublures en mémoire, et `GA_EMAIL_SENDING_ENABLED` reste à `false`.

---

## 2. Tests

```
476 passed, 5 skipped, 7 deselected
  tests/test_inbox_relay_gate.py     14 tests  (nouveau)
  tests/test_observability.py        +4 tests
  tests/test_opt_out_journey.py      ajustés
ruff check / format                  All checks passed
tsc --noEmit                         OK
make secrets                         Aucun secret détecté
```

`tests/test_inbox_relay_gate.py` couvre le contrôle lui-même (six cas, du
« jamais déclaré » au seuil configurable), son effet sur l'envoi (premier
contact, relance suspendue, reprise, trace), et la primauté de l'opposition.

---

## 3. Nouvelles dépendances

**Aucune dépendance logicielle.** Rien n'a été ajouté à `pyproject.toml` ;
tout repose sur le journal d'audit et la configuration existants.

**Trois nouveaux réglages**, tous facultatifs et avec des valeurs par
défaut sûres :

| Réglage | Défaut | Effet |
|---|---|---|
| `GA_INBOX_RELAY_MAX_AGE_HOURS` | `96` | seuil de suspension des relances |
| `GA_INBOX_RELAY_OWNER_EMAIL` | vide | destinataire de l'alerte |
| `GA_INBOX_RELAY_DEPUTY_EMAIL` | vide | suppléance |

**Deux dépendances organisationnelles**, qui ne peuvent pas être réglées
depuis le dépôt :

1. **Un compte au rôle `commercial` pour Merveille Nguetse** — voir § 4.
2. **Un acheminement des alertes** — Alertmanager, ou une personne qui lit
   `/api/v1/system/alerts`.

---

## 4. Le rôle de Merveille : une réserve à lever

Vous demandez « rôle `commercial`, avec uniquement les autorisations
nécessaires ». Les deux ne vont pas tout à fait ensemble, et je préfère le
dire.

Le modèle de rôles est **hiérarchique** : `lecteur < commercial < admin`.
Un compte `commercial` obtient donc d'un bloc :

| Permission | Nécessaire au relevé ? |
|---|---|
| `CAN_READ` | oui |
| `CAN_OPERATE` — reporter une réponse, déclarer un relevé | **oui** |
| `CAN_APPROVE` — **valider un message avant envoi** | non |

`CAN_APPROVE` vaut `COMMERCIAL` dans le code actuel. Merveille recevrait
donc le droit d'approuver des messages, dont elle n'a pas besoin. Le
garde-fou qui interdit d'approuver sa propre demande reste actif, mais il
n'empêche pas d'approuver celles des autres.

`lecteur` ne convient pas : il ne permet ni de reporter une réponse, ni de
déclarer un relevé — et sans cela, le relevé ne laisse aucune trace.

**Réserve levée le 18 octobre 2026.** Vous avez retenu la séparation. Un
palier `operateur` a été introduit entre `lecteur` et `commercial` : il porte
`CAN_OPERATE` sans `CAN_APPROVE`. Merveille Nguetse prend ce rôle. Voir
`docs/audits/2026-10-18-role-operateur.md`.

---

## 5. Risques résiduels

**R25 — Une déclaration de complaisance reste possible.** Quelqu'un peut
déclarer un relevé sans avoir ouvert la boîte. Aucune technique ne l'empêche
depuis ici. Ce que le système apporte : la déclaration est nominative et
journalisée, donc la responsabilité est tracée. C'est un contrôle
organisationnel, présenté comme tel.

**R26 — Les relances suspendues ne se relancent pas toutes seules.** Elles
repartent au prochain appel d'envoi, pas automatiquement à la déclaration du
relevé. Pour l'instant, quelqu'un doit relancer l'opération. Un rattrapage
automatique serait possible, mais il enverrait des messages sans action
humaine immédiate — ce qui demande votre validation.

**R27 — Le seuil de 96 h laisse une fenêtre.** Dans le pire cas, une
opposition peut dormir quatre jours avant que les relances ne soient
suspendues, et les plafonds de fréquence (2 messages par contact et par
mois) sont alors la seule protection. Abaisser le seuil à 48 h fermerait la
fenêtre, au prix d'une suspension systématique le lundi.

**R28 — La suppléance n'est toujours pas désignée.** Tant qu'elle ne l'est
pas, une absence imprévue de Merveille interrompt le relevé, et la parade
documentée — vider `GA_REPLY_TO_EMAIL` — suppose que quelqu'un y pense.

**R29 — L'alerte n'est acheminée par personne.** Les destinataires sont
configurables et exposés, mais aucun système ne les prévient. Tant que
l'Alertmanager n'est pas branché, l'alerte n'existe que pour qui va la lire.

---

## 6. État des dépendances

| Dépendance | État |
|---|---|
| D2 — sous-domaine `growth.techlearn-saem.com` | **ouverte**, inchangée |
| Configuration d'expédition | **ouverte**, inchangée |
| Compte `commercial` pour Merveille Nguetse | à créer — voir la réserve § 4 |
| Suppléance | **à désigner** |
| Acheminement des alertes | à décider |
| `GA_EMAIL_SENDING_ENABLED` | reste à `false` |
| `GA_SENDGRID_SANDBOX_MODE` | reste à `true` |

Aucun déploiement n'a été effectué. Aucune modification de Maintrix ni
d'EduCore.
