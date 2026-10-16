# Demande d'autorisation D2 — domaine d'expédition dédié

**Destinataire :** détenteur de la zone DNS des domaines Maintrix et EduCore
**Émetteur :** équipe TechLearn Growth Agent
**Date :** à compléter à l'envoi
**Décision attendue :** accord écrit, nominatif et daté
**Réponse souhaitée avant :** à fixer — rien d'urgent, mais rien ne démarre sans

---

## En une page

Nous demandons l'autorisation de créer **un sous-domaine dédié à la
prospection commerciale**, avec ses propres enregistrements SPF, DKIM et
DMARC.

**Aucun enregistrement existant n'est modifié.** La demande porte uniquement
sur l'ajout d'entrées pour un nom qui n'existe pas encore.

La raison n'est pas le confort de l'équipe commerciale. C'est qu'en l'absence
de séparation, une campagne de prospection qui se passe mal peut dégrader la
délivrabilité des emails **transactionnels** de Maintrix et d'EduCore —
convocations, notifications, factures. Le dommage toucherait alors des clients
existants, pas des prospects.

**Aucune modification DNS n'a été effectuée.** Ce document est une demande.

---

## 1. Le risque, concrètement

La réputation d'expéditeur se calcule par domaine. Les grands fournisseurs de
messagerie — Gmail, Outlook, Yahoo — associent un score à chaque domaine
expéditeur, alimenté par les plaintes, les rebonds et les signalements en
courrier indésirable.

La prospection à froid génère structurellement des plaintes. Même irréprochable
— ciblage correct, désinscription en un clic, volumes faibles — elle produit un
taux de signalement supérieur à celui d'un email transactionnel attendu par son
destinataire.

Si prospection et transactionnel partagent le même domaine, les deux partagent
le même score.

### Le scénario à éviter

1. Une campagne EduCore part vers 200 établissements depuis `educore.exemple`.
2. Quinze destinataires cliquent « courrier indésirable » plutôt que de se
   désinscrire. C'est un taux ordinaire, pas un accident.
3. La réputation de `educore.exemple` baisse chez un grand fournisseur.
4. Trois semaines plus tard, un établissement **client** ne reçoit plus ses
   notifications de rentrée : elles arrivent en indésirable.
5. L'établissement appelle le support. Personne ne fait le lien avec une
   campagne commerciale vieille de trois semaines.

Ce scénario n'est ni rare ni théorique : c'est le mode de défaillance le plus
courant lorsqu'on ajoute de la prospection à un domaine transactionnel.

### Pourquoi la réparation coûte cher

Reconstruire la réputation d'un domaine prend des semaines, parfois des mois,
et suppose de réduire fortement les volumes pendant la période — y compris les
volumes transactionnels, qu'on ne peut pas réduire. Le coût de la prévention
est de quelques enregistrements DNS.

---

## 2. Ce que nous demandons

### Le sous-domaine

Un sous-domaine dédié, par exemple `growth.exemple` ou `contact.exemple` — le
nom exact vous revient. Il ne doit **pas** être celui qui sert aux emails
transactionnels.

### Les enregistrements à créer

Pour le seul sous-domaine retenu, noté ci-dessous `SOUSDOMAINE` :

| Type | Nom | Valeur | Rôle |
|---|---|---|---|
| TXT | `SOUSDOMAINE` | `v=spf1 include:<fournisseur> -all` | Déclare les serveurs autorisés à émettre |
| TXT | `<sélecteur>._domainkey.SOUSDOMAINE` | clé publique fournie par le prestataire | Signature DKIM |
| TXT | `_dmarc.SOUSDOMAINE` | `v=DMARC1; p=none; rua=mailto:<boîte>; pct=100` | Politique et rapports |
| MX | `SOUSDOMAINE` | serveur du prestataire | Réception des réponses et des rebonds |
| CNAME | `<suivi>.SOUSDOMAINE` | domaine du prestataire | Suivi des liens, facultatif |

Les valeurs exactes dépendent du prestataire d'envoi, qui n'est pas encore
choisi. Nous les fournirons avant toute création.

### La progression DMARC

Démarrer en `p=none` : la politique n'applique rien, mais les rapports
remontent. Après quatre à six semaines d'observation, passer à `p=quarantine`,
puis à `p=reject`.

Commencer directement en `p=reject` ferait rejeter des messages légitimes mal
configurés, sans qu'on ait eu le moyen de s'en apercevoir.

---

## 3. Ce qui reste strictement inchangé

| Élément | État |
|---|---|
| Enregistrements DNS des domaines existants | **Inchangés** |
| SPF, DKIM, DMARC des domaines transactionnels | **Inchangés** |
| Enregistrements MX existants | **Inchangés** |
| Serveurs de messagerie de Maintrix et EduCore | **Non touchés** |
| Dépôts, bases, conteneurs des deux plateformes | **Non touchés** |

La demande est strictement additive : créer un nom qui n'existe pas. Si elle
est refusée, rien ne change ; la prospection par email reste simplement
impossible, et l'équipe se rabat sur le téléphone et les rendez-vous.

---

## 4. Ce que nous avons déjà mis en place

Pour que l'autorisation ne soit pas un chèque en blanc, les protections sont
implémentées **avant** la demande, et testées.

| Protection | Mise en œuvre | Vérifié par |
|---|---|---|
| Aucun envoi sans validation humaine | L'approbation porte sur un condensat du contenu ; modifier le texte l'invalide | `test_approvals.py` |
| L'auteur ne valide pas son propre message | Comparaison insensible à la casse | `test_author_cannot_approve_their_own_request` |
| Opposition immédiate et définitive | Deuxième des six contrôles d'envoi, avant même l'approbation | `test_suppression.py` |
| Désinscription en un clic | En-têtes `List-Unsubscribe` (RFC 8058) sur chaque message | `test_sent_message_carries_one_click_unsubscribe_headers` |
| Message sans moyen d'opposition bloqué | L'agent refuse de produire un tel brouillon | `test_draft_without_opt_out_mention_is_blocked` |
| Plafond de 2 messages par contact / 30 jours | Configurable, appliqué à l'envoi | `test_contact_frequency_cap_is_enforced` |
| Plafond par campagne et par jour | Idem | `test_campaign_daily_cap_is_enforced` |
| Anti-doublon sur le contenu | Un même texte ne part pas deux fois au même contact | `test_identical_content_to_same_contact_is_deduplicated` |
| Aucun envoi réel possible aujourd'hui | Double verrou de configuration ; le fournisseur SMTP refuse de s'initialiser | `test_smtp_provider_refuses_to_initialize_when_sending_is_off` |

L'envoi réel est actuellement **impossible**, et le restera tant que cette
autorisation n'aura pas été accordée et le domaine configuré.

---

## 5. Montée en volume proposée

Un domaine neuf n'a aucune réputation. Envoyer 200 messages le premier jour
depuis un domaine inconnu est le meilleur moyen d'être classé indésirable
d'emblée.

| Semaine | Volume quotidien | Objectif |
|---|---|---|
| 1 | 5 | Vérifier l'authentification sur les principaux fournisseurs |
| 2 | 10 | Observer les premiers rapports DMARC |
| 3–4 | 20 | Mesurer plaintes et rebonds |
| 5–8 | 30 à 50 | Passer DMARC en `p=quarantine` |
| au-delà | selon les indicateurs | `p=reject` si les signaux sont bons |

Seuils d'arrêt, sans discussion :

- **taux de plainte > 0,3 %** → arrêt immédiat et analyse ;
- **taux de rebond dur > 2 %** → arrêt, le ciblage est en cause ;
- **échecs DMARC > 1 %** → arrêt, la configuration est en cause.

Le plafond quotidien par campagne est déjà implémenté et configurable : la
montée en volume se pilote par configuration, pas par discipline.

---

## 6. Et si vous refusez

C'est une issue acceptable, et il vaut mieux la dire :

- la prospection par email n'a pas lieu ;
- l'équipe travaille par téléphone, rendez-vous et recommandation ;
- Growth Agent reste utile pour la recherche, la qualification, le suivi des
  opportunités et des pilotes — tout fonctionne déjà sans envoi ;
- aucune modification DNS n'est faite.

Ce que nous ne ferons **pas** en cas de refus : utiliser le domaine
transactionnel « juste pour quelques messages ». C'est précisément le risque
que cette demande vise à écarter, et le code l'empêche.

---

## 7. Retrait de l'autorisation

L'autorisation est révocable à tout moment, sans justification et sans délai.

Pour la révoquer : nous le dire. Nous passons `GA_EMAIL_SENDING_ENABLED=false`,
ce qui rend tout envoi impossible immédiatement. Les enregistrements DNS du
sous-domaine peuvent être supprimés ensuite, sans aucun effet sur les domaines
existants.

Nous nous engageons à ne jamais réactiver l'envoi sans un nouvel accord écrit.

---

## 8. Ce que nous attendons de vous

1. **Accord ou refus** sur le principe du sous-domaine dédié.
2. Si accord : le **nom exact** du sous-domaine.
3. Qui crée les enregistrements — vous, ou nous avec une délégation ?
4. L'adresse de réception des **rapports DMARC**.

Nous fournirons les valeurs exactes dès le prestataire d'envoi choisi, et nous
ne demanderons la création des enregistrements qu'à ce moment-là.

---

## Formulaire de décision

```
Demande D2 — sous-domaine dédié à la prospection

[ ] Accordé          [ ] Refusé          [ ] Accordé sous conditions

Sous-domaine retenu : ______________________________________________

Conditions éventuelles : ____________________________________________

_____________________________________________________________________

Création des enregistrements assurée par : [ ] détenteur DNS  [ ] équipe Growth Agent

Adresse de réception des rapports DMARC : ___________________________

Nom : ______________________  Fonction : ____________________________

Date : ____________________   Signature : ___________________________
```

La décision est à consigner dans la table `integration_configs`, champ
`authorization_reference`, afin que la configuration reste auditable.

---

## Annexes

- [Registre des dépendances](../11-dependances-autorisation.md) — D2 et les trois autres
- [Sécurité et conformité](../02-securite-conformite.md) — modèle de menace, menace M2
- [Campagnes pilotes](../06-campagnes-pilotes.md) — volumes et règles d'envoi
- [Politique de rétention](../07-politique-de-retention.md) — durées de conservation

---

# Révision du 16 octobre 2026 — le risque est confirmé, la demande précisée

La version ci-dessus raisonnait sur un risque supposé. Il est désormais
**constaté**, et la demande devient concrète : le prestataire est choisi, le
domaine est connu, et le sous-domaine demandé porte un nom.

## R1. Ce qui a été vérifié, et comment

| Fait | Source | Statut |
|---|---|---|
| EduCore émet depuis `EduCore <noreply@techlearn-saem.com>` | Dépôt EduCore, `artifacts/api-server/src/lib/mailer.ts:20` (`DEFAULT_MAIL_FROM`), confirmé par `mailer.test.ts:16` et `.env.example:126` | **Vérifié** |
| Maintrix émet ses réinitialisations de mot de passe depuis `noreply@techlearn-saem.com` | Déclaration de la responsable du projet, 16/10/2026 | **Déclaré, non vérifié** — aucun accès autorisé au dépôt Maintrix |
| `noreply@techlearn-saem.com` est vérifié comme identité d'expéditeur SendGrid | Déclaration de la responsable du projet | **Déclaré, non vérifié** |
| EduCore utilise aussi `noreply@educampus.cm` pour les notifications push (VAPID) | Dépôt EduCore, `ARCHITECTURE.md:73`, `pushService.ts:35` | Vérifié — hors périmètre email |

**Ce qui n'a pas pu être vérifié**, faute d'accès autorisé, et qui doit l'être
par le détenteur de la zone avant toute décision :

- les enregistrements SPF, DKIM et DMARC actuellement publiés pour
  `techlearn-saem.com` ;
- l'existence d'une authentification de domaine SendGrid déjà en place sur le
  domaine principal, et pour quel compte ;
- les autres systèmes qui émettraient depuis ce domaine (facturation, support,
  outils tiers) ;
- la politique DMARC en vigueur et son éventuel `sp=` (qui s'appliquerait par
  défaut au sous-domaine demandé).

L'isolation **n'est pas acquise** et n'est pas supposée telle.

## R2. Le risque n'est plus théorique : c'est la même adresse

Le document initial craignait un entraînement de réputation entre deux usages
d'un même **domaine**. La situation réelle est plus serrée : la prospection
partirait de **l'adresse exacte** qui délivre les emails de service.

```
noreply@techlearn-saem.com
   ├── EduCore        — notifications, comptes          (vérifié)
   ├── Maintrix       — réinitialisation de mot de passe (déclaré)
   └── Growth Agent   — prospection commerciale          (demandé)
```

Le scénario concret : une campagne de prospection déplaît, des destinataires
cliquent « signaler comme indésirable ». Les filtres n'apprennent pas « cette
campagne est indésirable », ils apprennent **« cette adresse est
indésirable »**. Les réinitialisations de mot de passe de Maintrix partent
alors en indésirables, et un utilisateur qui ne peut pas récupérer son compte
appelle le support en pensant que le produit est cassé.

La réparation est lente — plusieurs semaines de réputation à reconstruire — et
elle touche des clients qui payent, pas des prospects.

## R3. Ce que nous demandons, précisément

Un sous-domaine dédié : **`growth.techlearn-saem.com`**.

Aucun enregistrement du domaine principal n'est modifié. La demande porte
uniquement sur des noms qui n'existent pas encore, sous ce sous-domaine.

### Les enregistrements, tels que SendGrid les demande

SendGrid propose deux modes d'authentification de domaine. Nous recommandons
le premier.

**Mode 1 — sécurité automatisée (recommandé).** Trois `CNAME`, et SendGrid
gère SPF et la rotation des clés DKIM à notre place :

| Type | Nom | Valeur | Rôle |
|---|---|---|---|
| CNAME | `em<NNNN>.growth.techlearn-saem.com` | `u<ID>.wl<NNN>.sendgrid.net` | Domaine de retour (rebonds) |
| CNAME | `s1._domainkey.growth.techlearn-saem.com` | `s1.domainkey.u<ID>.wl<NNN>.sendgrid.net` | Signature DKIM, clé 1 |
| CNAME | `s2._domainkey.growth.techlearn-saem.com` | `s2.domainkey.u<ID>.wl<NNN>.sendgrid.net` | Signature DKIM, clé 2 |

**Les valeurs `<NNNN>`, `<ID>` et `<NNN>` sont émises par SendGrid pour un
compte donné.** Elles ne peuvent pas être devinées ni réutilisées d'un autre
domaine. Elles s'obtiennent dans *Settings → Sender Authentication → Authenticate
Your Domain*, en déclarant `growth.techlearn-saem.com`. Nous les
transmettrons telles quelles, sans en inventer aucune.

**Mode 2 — sécurité automatisée désactivée.** Un `TXT` DKIM et un `TXT` SPF à
maintenir nous-mêmes. Plus de contrôle, plus d'entretien, et une rotation de
clé qui devient manuelle. À ne retenir que si votre politique interdit les
`CNAME` vers un tiers.

### À ajouter dans tous les cas

| Type | Nom | Valeur proposée | Rôle |
|---|---|---|---|
| TXT | `_dmarc.growth.techlearn-saem.com` | `v=DMARC1; p=none; rua=mailto:<boîte>; pct=100` | Politique propre au sous-domaine |

Ce `_dmarc` n'est pas facultatif : sans lui, le sous-domaine hérite de la
politique du domaine principal via `sp=`. Lui donner la sienne, c'est
précisément ce qui l'isole — et cela permet de durcir la prospection sans
toucher au transactionnel.

Progression inchangée : `p=none` pendant quatre à six semaines pour observer
les rapports, puis `p=quarantine`, puis `p=reject`.

### Ce que nous ne demandons pas

- **Pas de suivi de liens** (`link branding`). Le connecteur désactive le
  suivi des clics et des ouvertures ; demander les `CNAME` correspondants
  n'aurait servi à rien. Un pixel de traçage dans un message de prospection
  collecte des données sans consentement, et des liens réécrits feraient
  passer la désinscription par un tiers.
- **Aucune modification** de SPF, DKIM ou DMARC sur `techlearn-saem.com`.
- **Aucun MX sur le domaine principal.** Voir R4.

## R4. Les réponses

Le document initial prévoyait un `MX` sur le sous-domaine pour recevoir les
réponses et les rebonds. Ce n'est plus demandé à ce stade, et il faut dire
pourquoi.

L'expéditeur reste provisoirement `noreply@techlearn-saem.com`, avec un
`Reply-To` pointant vers une boîte **réellement surveillée**
(`GA_REPLY_TO_EMAIL`). Tant qu'aucune boîte n'est confirmée, les messages
n'invitent plus à répondre : ils renvoient au lien de désinscription, qui,
lui, fonctionne. Promettre « répondez STOP » depuis une adresse qui ne reçoit
pas est pire que ne rien promettre — la personne croit s'être opposée, et on
la recontacte.

`contact@techlearn-saem.com` est l'adresse candidate. **Elle n'est pas
activée** : il reste à confirmer que la boîte existe et reçoit.

Les rebonds, eux, sont traités par SendGrid via le domaine de retour
(`em<NNNN>`), sans MX de notre côté.

## R5. Point d'arrêt avant tout envoi réel

```
[ ] Sous-domaine growth.techlearn-saem.com autorisé
[ ] Enregistrements créés par le détenteur de la zone, valeurs issues de SendGrid
[ ] Authentification du sous-domaine vérifiée dans SendGrid
[ ] Enregistrements du domaine principal inchangés — vérifié après coup
[x] Boîte de réponse confirmée : contact@techlearn-saem.com existe et reçoit (16/10/2026)
[ ] Personne nommée pour relever cette boîte chaque jour ouvré
[ ] GA_EMAIL_FROM basculé sur le sous-domaine
[ ] Mode bac à sable SendGrid désactivé, en connaissance de cause
[ ] Procès-verbal de mise en service signé
```

Tant que cette liste n'est pas complète, `GA_EMAIL_SENDING_ENABLED` reste à
`false`, et `GA_SENDGRID_SANDBOX_MODE` à `true`.

## R6. État de la demande

**Ouverte.** Aucune modification DNS n'a été effectuée. Aucun envoi commercial
réel n'a eu lieu. Le connecteur SendGrid est écrit, testé contre une doublure,
et configuré en bac à sable par défaut.
