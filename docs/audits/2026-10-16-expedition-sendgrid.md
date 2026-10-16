# Expédition SendGrid — rapport

**Date** : 16 octobre 2026
**Déclenché par** : `noreply@techlearn-saem.com`, vérifiée chez SendGrid.
**Déploiement** : aucun. Aucun envoi réel. Aucune modification DNS.

---

## 1. Ce qui a été vérifié avant de câbler quoi que ce soit

Il vous a été demandé de vérifier les identités d'expédition réellement
utilisées, à partir des seules sources autorisées. Voici ce que cela donne.

| Fait | Source | Statut |
|---|---|---|
| EduCore émet depuis `EduCore <noreply@techlearn-saem.com>` | Dépôt EduCore, `artifacts/api-server/src/lib/mailer.ts:20`, confirmé par `mailer.test.ts:16` et `.env.example:126` | **Vérifié** |
| Maintrix émet ses réinitialisations depuis la même adresse | Votre déclaration | **Déclaré, non vérifié** — aucun accès autorisé au dépôt Maintrix |
| L'adresse est vérifiée chez SendGrid | Votre déclaration | **Déclaré, non vérifié** |

**Non vérifiable depuis ici, et à faire vérifier par le détenteur de la zone :**
les enregistrements SPF/DKIM/DMARC actuellement publiés, l'existence d'une
authentification de domaine SendGrid déjà posée sur le domaine principal, les
autres systèmes qui émettraient depuis ce domaine, et la politique DMARC en
vigueur avec son éventuel `sp=`.

L'isolation n'est pas acquise, et n'est pas supposée telle.

## 2. Le risque est plus serré que ce que D2 décrivait

D2 craignait un entraînement de réputation entre deux usages d'un même
domaine. La réalité est plus étroite : la prospection partirait de
**l'adresse exacte** qui délivre les emails de service.

```
noreply@techlearn-saem.com
   ├── EduCore        — notifications, comptes            (vérifié)
   ├── Maintrix       — réinitialisation de mot de passe   (déclaré)
   └── Growth Agent   — prospection commerciale            (demandé)
```

Les filtres n'apprennent pas « cette campagne est indésirable », ils
apprennent « cette adresse est indésirable ». Un utilisateur qui ne reçoit
plus sa réinitialisation de mot de passe appelle le support en pensant que le
produit est cassé.

D2 est donc **maintenu ouvert**, avec une révision datée qui demande
`growth.techlearn-saem.com` et liste les enregistrements SendGrid exacts —
en précisant que leurs valeurs sont émises par votre compte et ne peuvent pas
être devinées. Aucun enregistrement existant n'est touché.

## 3. La contradiction la plus directe, et ce que j'en ai fait

Chaque message se terminait par « répondez STOP et je ne vous recontacte
pas ». Depuis `noreply@`, cette réponse se perd : on promettait un droit
qu'on ne tenait pas, et la personne aurait cru s'être opposée.

Votre exigence 2 — garder STOP **uniquement si les réponses sont reçues** —
est maintenant une règle du code, pas une consigne de rédaction.

`Settings.replies_are_monitored()` tranche sur un fait vérifiable :

| Configuration | Réponses surveillées | Ligne finale du message |
|---|---|---|
| Rien de configuré | non | lien de désinscription |
| `noreply@` seul | **non** | lien de désinscription |
| `noreply@` + `GA_REPLY_TO_EMAIL` | oui | « répondez STOP » |
| Adresse qui reçoit | oui | « répondez STOP » |

`GA_REPLY_TO_EMAIL` est **vide**. `contact@techlearn-saem.com` figure en
commentaire dans `.env.example`, prête, **non activée** — à décommenter
quand vous aurez confirmé que la boîte existe et reçoit.

### Et le lien, lui, est devenu visible

Votre exigence 1. L'en-tête `List-Unsubscribe` alimente le bouton natif de
Gmail et d'Outlook, mais reste invisible ailleurs. Le lien réel est désormais
ajouté en bas du corps du message, **à l'envoi** — c'est le seul moment où le
jeton existe.

Conséquence assumée et documentée : le condensat d'approbation porte sur le
texte de l'auteur, pas sur ce pied, qui est une mention fixe que personne ne
modifie. Le texte approuvé n'est pas altéré en base (test à l'appui).

## 4. Détection des oppositions (exigence 3)

Deux listes existaient, une dans le rédacteur et une dans l'analyste. Elles
auraient divergé, et c'est toujours celle qui reconnaît le moins qui aurait
servi à écarter quelqu'un. Il n'y en a plus qu'une.

Élargie aux tournures réelles, comparée sans accents et sans casse :
désinscription, désabonnement, « ne plus me contacter », « je ne souhaite
plus », « retirez-moi », « supprimez mes données », « je m'oppose »,
« arrêtez de m' », « laissez-moi tranquille », `unsubscribe`, `remove me`,
`opt-out`… Quinze formulations testées, et quatre réponses ordinaires
vérifiées comme **non** interprétées en opposition.

La détection s'applique au texte brut **et** au texte assaini : la protection
anti-injection pourrait emporter les mots par lesquels quelqu'un demande
qu'on le laisse tranquille. Dans le doute, on retire le contact.

## 5. Blocage immédiat (exigence 4) — prouvé

`test_a_stop_reply_blocks_the_next_send_immediately` : envoi, réponse
« Merci de ne plus me contacter », puis

```
opposition enregistrée    : oui, portée globale
statut du prospect        : perdu
envoi suivant             : refusé (suppressed), brouillon bloqué
boîte d'envoi             : 1 message, pas 2
```

Et `test_the_objection_survives_a_restart` : relu depuis une session neuve.
Une opposition tenue en mémoire ne protège personne.

## 6. Ce qui n'est pas opérationnel, et qui le dit (exigence 5)

**Aucune intégration de réception n'existe.** Rien ne prétend le contraire :

- `/api/v1/system/integrations` renvoie `inbound_integration: "aucune"` et
  `reply_analysis: "non opérationnelle — réception non branchée"` ;
- les KPI portent une note explicite : le taux de réponse ne reflète que les
  réponses saisies à la main ;
- la recette affiche ❌ sur trois lignes : réception des réponses, envoi réel
  vers une boîte de test, et taux de réponse / entonnoir démo-pilote.

## 7. Le test de bout en bout demandé (exigence 6) — partiellement fait

| Étape | État |
|---|---|
| Envoi autorisé vers une boîte de test | ❌ **non fait** — pas d'autorisation d'envoi réel |
| Réponse STOP | ✅ simulée, injectée dans l'analyste |
| Réception | ❌ **non fait** — aucune intégration entrante |
| Mise à jour du statut du prospect | ✅ prouvé |
| Blocage d'un nouvel envoi | ✅ prouvé |

Les deux ❌ ne sont pas des oublis : l'un demande votre autorisation, l'autre
demande une intégration qui n'a pas encore été décidée (webhook SendGrid
Inbound Parse, IMAP sur une boîte, ou relevé manuel).

En attendant, `tests/test_email_integration.py` est prêt : il parle au **vrai**
SendGrid en **bac à sable**, ce qui valide la clé, l'identité d'expéditeur
vérifiée et la forme de notre charge — sans que rien ne soit livré. Le bac à
sable y est imposé par le test, pas par la configuration : même lancé avec une
clé de production, rien ne peut partir.

```sh
GA_SENDGRID_TEST_KEY="SG.…" pytest -m integration tests/test_email_integration.py
```

## 8. Le connecteur SendGrid

API v3 plutôt que le relais SMTP, qui n'aurait demandé aucune ligne de code.
La raison tient au protocole du lot 1, qui distingue trois issues :

| Issue | Réservation | Reprise |
|---|---|---|
| Accepté (202) | confirmée | refusée |
| Refus explicite (4xx) | **libérée** | autorisée une fois la cause levée |
| Panne (5xx, coupure) | gelée en « inconnue » | **jamais automatique** |

Au-dessus d'un relais SMTP, un rejet définitif remonte comme une exception et
se confond avec une coupure : le message finirait bloqué pour toujours en
attente d'un arbitrage qui n'avait pas lieu d'être.

Le même défaut existait dans l'adaptateur SMTP, qui reste disponible : il est
corrigé. `SMTPRecipientsRefused`, `SMTPSenderRefused` et consorts sont
désormais rapportés comme des refus, pas comme des pannes.

Trois décisions à signaler :

- **Aucun suivi.** Clics, ouvertures et `subscription_tracking` désactivés.
  Un pixel de traçage collecte des données sans consentement, et des liens
  réécrits feraient passer la désinscription par un tiers.
- **Bac à sable par défaut** (`GA_SENDGRID_SANDBOX_MODE=true`), avec un
  avertissement de configuration quand il est actif alors que l'envoi est
  autorisé — pour qu'on ne croie pas envoyer.
- **La clé n'apparaît nulle part** : ni dans la sonde, ni dans les journaux.
  Seul le domaine de l'expéditeur est exposé.

## 9. Preuves

```
448 passed, 5 skipped, 4 deselected
  tests/test_connector_sendgrid.py   20 tests
  tests/test_opt_out_journey.py      26 tests
  tests/test_email_integration.py     3 tests, exclus par défaut
ruff check / format                  All checks passed
tsc --noEmit                         OK
make secrets                         Aucun secret détecté
```

## 10. Risques résiduels

**R20 — L'adresse d'expédition reste partagée avec le transactionnel.** C'est
le risque principal, et il n'est pas levé : il dépend de D2. Tant que
l'envoi réel n'est pas activé, il ne se matérialise pas.

**R21 — Aucune réception de réponses.** Les prospects qui répondront à un
`Reply-To` écriront à une boîte que personne ne relève automatiquement. C'est
pour cela que le message n'invite pas à répondre tant que
`GA_REPLY_TO_EMAIL` est vide — mais quelqu'un peut toujours répondre
spontanément. La boîte doit être relevée par une personne.

**R22 — La détection d'opposition est une liste, pas une compréhension.** Une
tournure inédite (« je préfère qu'on en reste là ») passerait au travers. La
liste s'élargit à mesure qu'on rencontre des formulations réelles ; c'est un
choix délibéré contre une inférence, qui raterait autrement et sans trace.

**R23 — Le pied de désinscription n'est pas couvert par l'approbation.** Le
valideur approuve le texte de l'auteur ; le pied est ajouté ensuite. C'est
une mention fixe, mais elle n'est pas soumise à son regard sur chaque
message.

**R24 — Le connecteur SendGrid n'a jamais parlé au vrai service.** Tout est
prouvé contre une doublure. Le test en bac à sable est écrit, pas exécuté :
il demande une clé.

## 11. Ce que j'attends de vous

1. La boîte `contact@techlearn-saem.com` existe-t-elle et reçoit-elle ?
   Tant que la réponse n'est pas oui, `GA_REPLY_TO_EMAIL` reste vide et les
   messages n'invitent pas à répondre.
2. Une clé SendGrid de test, pour exécuter l'intégration en bac à sable.
3. La décision D2 sur `growth.techlearn-saem.com`.
4. Le choix du mode de réception des réponses, si vous en voulez un :
   webhook SendGrid Inbound Parse, IMAP, ou relevé manuel.
