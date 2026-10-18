# Lot 4 — Rapport de correction

**Date** : 18 octobre 2026
**Périmètre** : efficacité commerciale — P1-14, P1-15, P2-16.
**Déploiement** : aucun. Aucun envoi réel.

---

## 1. Ce qui manquait

Le parcours s'arrêtait au prospect. On savait qu'une organisation avait été
contactée, qu'elle avait répondu, qu'une démonstration était prévue — mais
pas **qui doit faire quoi, sur quel dossier, cette semaine**. C'est pourtant
la seule question que se pose une équipe commerciale le lundi matin.

Et les tâches créées par l'agent coordinateur partaient dans le CRM sans
jamais en revenir : le tableau de bord ne pouvait pas les voir.

## 2. Deux objets (P1-14)

`opportunities` — le dossier : porteur, montant, date de clôture envisagée,
motif de perte. Il reste **local** ; le CRM demeure la source de vérité des
comptes. Ce qui est ici sert à travailler et à mesurer.

`next_actions` — l'engagement : une chose à faire, par quelqu'un, avant une
date. `ix_next_action_owner` est l'index de la question du lundi matin.

Quatre règles, chacune pour une raison :

| Règle | Pourquoi |
|---|---|
| Un prospect n'a qu'un dossier ouvert | deux dossiers, ce sont deux personnes qui relancent sans le savoir |
| Un dossier perdu exige un motif, pris dans une liste fermée | « perdu » sans raison n'apprend rien ; un champ libre produit autant de motifs que de dossiers |
| Un dossier ne recule pas | reculer fausserait les délais médians, qui comptent les premières fois |
| Une action sans porteur est refusée | sans porteur, elle n'est faite par personne |

**Les tâches CRM redeviennent visibles.** L'agent coordinateur en garde
désormais un double local, avec l'identifiant distant conservé. Une tâche
créée dans HubSpot apparaît dans la charge de travail, et compte comme en
retard si elle l'est.

## 3. Le parcours au-delà de la démonstration (P1-15)

| Étape | Avant | Après |
|---|---|---|
| Démonstration planifiée | événement seul | inchangé |
| Démonstration tenue | ❌ aucune saisie | **compte rendu, avance le dossier, produit la suite** |
| Rendez-vous manqué | ❌ rien | **produit une action de reprogrammation** |
| Proposition | devis calculé, jamais suivi | **étape du dossier, avec action datée** |
| Perte | ❌ non consignée | **motif obligatoire, comptabilisé** |
| Pilote activé | lu depuis un connecteur désactivé | **inchangé** — dépend de l'autorisation produit |
| Renouvellement | alerte sans destinataire | **inchangé** — voir § 7 |

Un compte rendu produit **toujours** une prochaine action, y compris pour un
rendez-vous manqué : un rendez-vous manqué sans suite est un dossier qu'on
abandonne sans l'avoir décidé.

Les objections saisies sont des **catégories**, pas des verbatims. Le compte
rendu libre, lui, va dans les notes du dossier — soumises à la purge — et
n'entre pas dans le journal d'audit.

## 4. La lecture par cohorte (P2-16)

Un entonnoir en volumes cumulés mélange des organisations contactées il y a
six mois avec d'autres contactées hier, dont certaines n'ont pas eu le temps
de répondre. Le total monte, et on ne sait pas pourquoi.

`GET /api/v1/cohorts?product_code=educore` donne, par mois de premier
contact réel : l'entonnoir, les taux de conversion, les délais médians entre
étapes, les motifs de perte, les envois bloqués par motif, et les actions en
retard par personne.

Trois précautions, héritées des lots précédents :

**Seuls les envois réels comptent.** Une répétition n'a atteint personne, et
une réservation sans suite n'est pas un envoi.

**La médiane, pas la moyenne.** Vérifié sur `[2, 3, 4, 180]` jours : la
médiane donne 3,5, la moyenne aurait donné 47,25 — un chiffre qu'aucun
dossier n'a produit.

**Une cohorte trop jeune est signalée, pas masquée.** Moins de 30 jours,
ses taux sont mécaniquement bas ; les afficher sans avertir inviterait à
conclure d'une baisse qui n'est qu'un manque de recul.

L'illustration du problème, tirée d'un test : août convertit à 100 %,
septembre à 25 %. En cumulé on lirait 50 % — un chiffre que **ni l'une ni
l'autre** des deux cohortes n'a produit.

## 5. Un défaut trouvé en chemin : la purge ignorait les nouvelles tables

Les tables du pipeline portent du texte saisi à la main. Les notes d'un
dossier racontent un échange avec une personne nommée ; le compte rendu
d'une action aussi. Créées telles quelles, elles auraient laissé des données
personnelles **survivre à la durée de conservation du prospect auquel elles
se rapportent** — exactement ce que la politique de rétention interdit.

L'anonymisation couvre désormais `opportunities.notes`,
`next_actions.title`, `next_actions.outcome` et son `payload`. Ce qui reste :
la raison sociale, le montant et le motif de perte — des faits d'entreprise,
dont le retrait viderait les indicateurs historiques — et le porteur, qui est
un salarié et relève d'un autre régime.

`test_the_purge_reaches_the_new_tables` le vérifie de bout en bout. Au
passage, il a fallu deux prospects pour l'écrire : avec un seul, le
garde-fou de volume refuse une purge qui toucherait 100 % d'une table. Il
fait son travail.

## 6. Preuves

```
518 passed, 5 skipped, 7 deselected
  tests/test_pipeline.py        21 tests  (nouveau)
  tests/test_cohorts.py         10 tests  (nouveau)
  tests/test_api_rbac.py        +3 tests de pipeline
 27 passed                      (interface)
ruff check / format             All checks passed
tsc --noEmit                    OK
make secrets                    Aucun secret détecté
```

**Migration 0005 vérifiée sur PostgreSQL 16** : deux tables, cinq index
créés, aller-retour-aller stable, révision en tête. Aucune reprise de
données — ces tables n'existaient pas, et rien dans l'existant ne permet de
deviner un porteur ou une échéance. Le cluster temporaire a été supprimé
après vérification.

## 7. Ce qui n'a pas été fait, et pourquoi

**Le tableau de bord n'a pas d'écran.** Les données sont exposées par l'API
et testées ; l'interface web ne les affiche pas encore. C'est un choix de
périmètre : construire une page de cohortes utile demande des arbitrages de
présentation que je préfère vous soumettre plutôt que trancher seul. Dites-moi
si vous la voulez, et sous quelle forme.

**Le suivi du pilote reste lu depuis un connecteur désactivé.** Rien à
corriger ici : l'activation dépend d'une autorisation sur Maintrix et
EduCore que je n'ai pas, et que je ne demande pas dans ce lot.

**L'alerte de renouvellement n'a toujours pas de destinataire.** Le mécanisme
existe depuis le lot 3 ; nommer qui la reçoit est une décision
organisationnelle, comme la suppléance du relevé.

**Le protocole de validation sur deux cohortes n'a pas été lancé.** Il exige
D2, l'autorisation d'envoi réel et le cadre juridique camerounais. La recette
l'affiche ❌.

## 8. Risques résiduels

**R33 — Un dossier local peut diverger du CRM.** Rien ne synchronise les deux
sens : une affaire fermée dans HubSpot reste ouverte ici. Le double local a
été ajouté pour la visibilité, pas pour la réplication, et il faudra décider
lequel fait foi avant d'en faire un outil de pilotage.

**R34 — Les cohortes sont mensuelles, en dur.** Pour quinze organisations par
cohorte pilote, un découpage hebdomadaire serait plus lisible. Le paramétrer
est simple ; je ne l'ai pas fait sans savoir le volume réel.

**R35 — Les délais médians portent sur les premières occurrences.** Un
prospect recontacté six mois plus tard compte son premier envoi, pas le
second. C'est le bon choix pour mesurer une cohorte, pas pour mesurer une
campagne de relance — la question viendra.

**R36 — Aucune cohorte réelle n'existe.** Tout est éprouvé sur des données
fabriquées. Les premiers chiffres réels diront si le découpage et les
seuils tiennent.

## 9. État des dépendances

| Dépendance | État |
|---|---|
| D2 — `growth.techlearn-saem.com` | **ouverte** |
| Compte `operateur` pour Merveille Nguetse | **à créer** |
| Suppléance du relevé | **à désigner** |
| Destinataire des alertes de renouvellement | **à nommer** |
| Écran du tableau de bord par cohorte | **à arbitrer avec vous** |
| Connecteurs produits Maintrix / EduCore | désactivés, autorisation non demandée |
| `GA_EMAIL_SENDING_ENABLED` | reste à `false` |
| `GA_SENDGRID_SANDBOX_MODE` | reste à `true` |

Les quatre lots de l'audit sont désormais traités. Aucun déploiement. Aucune
modification de Maintrix ni d'EduCore.
