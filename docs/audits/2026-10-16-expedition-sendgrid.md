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

**Mise à jour du 16 octobre 2026 : `contact@techlearn-saem.com` existe et
reçoit.** `GA_REPLY_TO_EMAIL` est donc renseignée, et les messages invitent
de nouveau à répondre « STOP ». Un message réel, produit de bout en bout :

```
De       : noreply@techlearn-saem.com
Répondre : contact@techlearn-saem.com
Objet    : Qui suit les inscriptions chez Lycée Bilingue de Douala ?
----------------------------------------------------------------------
Bonjour,

Je cherche à savoir qui suit la gestion administrative chez Lycée Bilingue
de Douala.

EduCore regroupe les inscriptions, la scolarité et les absences dans un seul
outil. Si c'est utile, je vous montre une inscription d'élève sur vos
propres cas, en 30 minutes.

Est-ce que je m'adresse à la bonne personne ?

Si ce n'est pas votre sujet, répondez STOP et je ne vous recontacte pas.

Awa Njoya
Chargée de développement
TechLearn

--
Pour ne plus recevoir de message de notre part :
https://go.techlearn-saem.com/unsubscribe/TZAgt-0TzXO1gpcMbr_gSteeWNCQTHwy7aOVnhpYpB4
```

Les deux moyens d'opposition coexistent, et c'est voulu : le lien ne dépend
de personne, la réponse si.

### La réserve qui va avec

Votre exigence disait « réellement reçues **et traitées** ». Reçues : oui,
confirmé. Traitées : **à la main**. Rien ne relève cette boîte
automatiquement. La promesse n'est tenue que si une personne la lit chaque
jour ouvré et reporte les réponses via `POST /api/v1/replies/analyze` — une
route qui existe, qui enregistre l'opposition, change le statut du prospect
et bloque l'envoi suivant.

La procédure est écrite (`docs/08-supervision.md`). Elle prévoit notamment de
**vider `GA_REPLY_TO_EMAIL` pendant une absence prolongée** : les messages
cessent alors d'inviter à répondre et renvoient au lien, qui fonctionne sans
personne. Mieux vaut ne rien promettre que promettre à vide.

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
| Réception | ⚠️ boîte confirmée, **relevé manuel** — aucune intégration entrante |
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

**R21 — Le relevé des réponses est humain, mais il n'est plus invisible.**
`contact@techlearn-saem.com` est relevée chaque jour ouvré par **Merveille
Nguetse** (désignée le 16/10/2026). Rien ne la relève automatiquement, et une
opposition reportée trois jours après son arrivée laisse trois jours pendant
lesquels une relance peut partir.

Ce qui a changé : le système constate désormais le geste. Un relevé laisse
une trace **même quand la boîte est vide** — sans quoi « personne n'a écrit »
et « personne n'a relevé » produisent le même silence. L'alerte
`BoiteDeReponseNonRelevee` se déclenche après quatre jours, seuil choisi pour
ne pas sonner chaque week-end.

Reste ouvert : **la suppléance**. Une personne unique est un point de panne,
et la parade documentée (vider `GA_REPLY_TO_EMAIL` pendant une absence)
suppose que quelqu'un y pense.

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

1. ~~La boîte `contact@techlearn-saem.com` existe-t-elle et reçoit-elle ?~~
   **Confirmé le 16/10/2026.** L'adresse est activée.
2. ~~Qui relève cette boîte, chaque jour ouvré ?~~ **Merveille Nguetse,
   désignée le 16/10/2026.** Deux suites concrètes : lui créer un compte
   Growth Agent au rôle **commercial** (un compte lecteur ne permet ni de
   reporter une réponse ni de déclarer un relevé), et désigner une
   suppléance pour ses absences.
3. Une clé SendGrid de test, pour exécuter l'intégration en bac à sable.
4. La décision D2 sur `growth.techlearn-saem.com`.
5. Le mode de réception à terme : webhook SendGrid Inbound Parse, IMAP, ou
   relevé manuel assumé durablement.
