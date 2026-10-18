# Rôle `operateur` — séparer produire de valider

**Date** : 18 octobre 2026
**Origine** : réserve soulevée le 17/10 — le seul rôle capable de reporter
une réponse emportait aussi le droit d'approuver les messages des autres.
**Déploiement** : aucun.

---

## 1. Le problème

Le modèle de rôles est hiérarchique, et `CAN_APPROVE` valait `COMMERCIAL`,
tout comme `CAN_OPERATE`. Donner à Merveille Nguetse le rôle nécessaire pour
reporter une opposition revenait donc à lui donner le droit de valider des
envois — précisément la décision qu'un second regard est censé couvrir.

`lecteur` ne convenait pas : il ne permet ni de reporter une réponse, ni de
déclarer un relevé, et sans cela le relevé ne laisse aucune trace.

## 2. La correction

Un palier `operateur` entre `lecteur` et `commercial`.

| | `CAN_READ` | `CAN_OPERATE` | `CAN_APPROVE` | `CAN_ADMINISTER` |
|---|---|---|---|---|
| `lecteur` | oui | — | — | — |
| **`operateur`** | oui | **oui** | **—** | — |
| `commercial` | oui | oui | oui | — |
| `admin` | oui | oui | oui | oui |

```python
CAN_READ       = Role.LECTEUR
CAN_OPERATE    = Role.OPERATEUR     # était Role.COMMERCIAL
CAN_APPROVE    = Role.COMMERCIAL    # inchangé
CAN_ADMINISTER = Role.ADMIN
```

Les rangs sont passés à 10 / 20 / 30 / 40, espacés de dix pour qu'un palier
puisse être intercalé plus tard sans renuméroter les autres.

**Une seule route utilisait `CAN_APPROVE`** — la décision d'approbation.
Toutes les autres utilisaient `CAN_OPERATE` et basculent donc
automatiquement au nouveau palier. Aucune route n'a eu besoin d'être
modifiée.

**Aucune migration.** Le rôle est stocké en `String(16)` sans contrainte ni
type énuméré en base ; `operateur` fait neuf caractères. Les comptes
existants gardent leur rôle et leurs droits.

## 3. Un défaut trouvé en chemin : l'interface dupliquait la hiérarchie

`apps/web/src/lib/auth.tsx` portait sa propre copie :

```ts
const RANK: Record<Role, number> = { lecteur: 10, commercial: 20, admin: 30 }
```

Un compte `operateur` s'y serait connecté avec un rang `undefined`. Toutes
les comparaisons auraient échoué, et **l'interface se serait vidée de ses
boutons sans le moindre message d'erreur** — le pire mode de défaillance
possible pour un contrôle d'accès, parce qu'il ressemble à une panne
d'affichage.

C'est la même classe de défaut que les statuts de purge au lot 3 : une
constante recopiée d'un côté, oubliée de l'autre. Et comme là-bas, la
correction ne s'arrête pas à l'instance :

`test_the_web_role_ladder_matches_the_api` lit le littéral `RANK` dans le
fichier TypeScript et le compare à `ROLE_RANK`. J'ai vérifié qu'il échoue
réellement en cas de dérive, en désynchronisant volontairement les deux
avant de restaurer :

```
AssertionError: hiérarchie divergente —
  API       {lecteur: 10, operateur: 20, commercial: 30, admin: 40}
  interface {lecteur: 10, commercial: 20, admin: 40}
```

Au passage, quatre contrôles de l'interface étaient gardés par
`can('commercial')` alors que l'API n'exigeait que `CAN_OPERATE` : le bouton
« Envoyer » d'un message déjà approuvé, la découverte et la qualification de
prospects, et l'ajout d'une opposition. Ils passent à `can('operateur')` —
sans quoi Merveille aurait vu l'API accepter ce que l'interface lui
refusait.

## 4. Tests

```
484 passed, 5 skipped, 7 deselected   (API)
 27 passed                            (interface)
ruff check / format                   All checks passed
tsc --noEmit                          OK
make secrets                          Aucun secret détecté
```

Sept tests ajoutés à `test_api_rbac.py` :

| Test | Ce qu'il verrouille |
|---|---|
| `test_the_role_ladder_separates_producing_from_approving` | la matrice complète des quatre rôles |
| `test_an_operator_can_report_a_reply_and_declare_a_relay` | le travail réel de Merveille |
| `test_an_operator_cannot_approve_a_message` | **le point de toute la séparation** |
| `test_an_operator_can_still_request_an_approval` | demander n'est pas décider |
| `test_a_commercial_can_still_approve` | la séparation ne retire rien |
| `test_an_operator_cannot_administer` | le palier ne déborde pas vers le haut |
| `test_the_web_role_ladder_matches_the_api` | la dérive entre les deux hiérarchies |

Un compte de démonstration `operateur@growth.example.org` a été ajouté au
jeu local, avec le profil exact de la personne qui relève la boîte.

## 5. Ce que `operateur` permet encore, et pourquoi

`operateur` permet d'**expédier un message déjà approuvé par quelqu'un
d'autre**. C'est délibéré : la règle des deux paires d'yeux porte sur la
validation, pas sur le geste d'envoi, et elle reste entière — Merveille ne
peut approuver ni ses propres messages ni ceux des autres.

Le dire franchement : son rôle reste plus large que sa mission. Relever la
boîte ne demande ni de rédiger, ni d'expédier, ni de créer des prospects.
Un cloisonnement à cette finesse — « reporter une réponse sans pouvoir
expédier » — demanderait de sortir du modèle hiérarchique pour des
permissions indépendantes. C'est une refonte du contrôle d'accès, pas un
palier ; je ne l'engage pas sans demande explicite.

## 6. Risques résiduels

**R30 — Le modèle reste hiérarchique.** Chaque palier contient le précédent.
Tant qu'il restera linéaire, « peut A mais pas B » ne sera exprimable que si
B se trouve au-dessus de A. La séparation demandée tombait bien dans ce cas,
la suivante peut-être pas.

**R31 — La hiérarchie est dupliquée entre deux langages.** Le test de
cohérence la surveille, mais il lit un fichier source par expression
régulière : un refactoring de l'interface qui changerait la forme du
littéral le ferait échouer pour une mauvaise raison. Le message d'erreur
indique quoi faire.

**R32 — Le compte de Merveille n'est pas créé.** Le palier existe, le compte
non. Cela reste une action d'administration, hors du dépôt.

## 7. État des dépendances

| Dépendance | État |
|---|---|
| Compte `operateur` pour Merveille Nguetse | **à créer** |
| Suppléance | **à désigner** |
| D2 — `growth.techlearn-saem.com` | **ouverte** |
| Configuration d'expédition | **ouverte** |
| Acheminement des alertes | à décider |
| `GA_EMAIL_SENDING_ENABLED` | reste à `false` |
| `GA_SENDGRID_SANDBOX_MODE` | reste à `true` |

Aucun déploiement. Aucune modification de Maintrix ni d'EduCore.
