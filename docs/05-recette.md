# Matrice de recette

État au terme du développement. Chaque ligne renvoie au test qui l'établit.

## Isolation vis-à-vis de Maintrix et d'EduCore

| Contrôle | Preuve | État |
|---|---|---|
| Aucun fichier des dépôts Maintrix ou EduCore modifié | Dépôt séparé, `git log` de ce dépôt uniquement | ✅ |
| Aucun conteneur, volume ou service des deux plateformes touché | `docker-compose.yml` local, services propres | ✅ |
| L'interface produit n'expose aucune écriture | `test_product_adapter_exposes_no_write_method` | ✅ |
| Le connecteur HTTP refuse de s'initialiser sans autorisation | `test_disabled_http_product_adapter_refuses_to_initialize` | ✅ |
| Trois conditions requises pour activer un connecteur produit | `test_product_connector_needs_all_three_conditions` | ✅ |
| Mode simulé par défaut | `test_product_factory_returns_mock_without_authorization` | ✅ |
| Les événements hors contrat sont écartés | `test_out_of_scope_events_are_discarded_at_the_boundary` | ✅ |
| Les champs hors contrat sont écartés avant stockage | `test_unexpected_payload_fields_are_stripped` | ✅ |

## Secrets

| Contrôle | Preuve | État |
|---|---|---|
| Aucun secret dans les fichiers suivis | `scripts/check-secrets.sh`, en pre-commit et en CI | ✅ |
| `.env.example` sans aucune valeur | Contrôlé par le script | ✅ |
| Masquage dans les journaux | `test_log_filter_redacts_the_final_message` | ✅ |
| Clés sensibles remplacées par un condensat | `test_sensitive_keys_are_replaced_by_a_digest` | ✅ |
| L'endpoint d'état n'expose aucune valeur | `test_integrations_endpoint_never_exposes_a_secret` | ✅ |

## Données et conformité

| Contrôle | Preuve | État |
|---|---|---|
| Dédoublonnage déterministe | `test_dedupe.py`, 11 tests | ✅ |
| Provenance obligatoire | `test_manual_prospect_requires_at_least_one_source` | ✅ |
| Sans source, rien n'est « vérifié » | `test_prospect_without_sources_stays_unknown` | ✅ |
| Cloisonnement des deux produits | `test_the_two_products_stay_separated` | ✅ |
| Un profil cible ne franchit pas la frontière | `test_an_icp_cannot_cross_product_boundaries` | ✅ |
| Un gabarit ne franchit pas la frontière | `test_composer_refuses_a_template_from_the_other_product` | ✅ |
| Purge automatique à échéance | — | ❌ **Non implémentée** (risque R3) |

## CRM

| Contrôle | Preuve | État |
|---|---|---|
| Pagination complète | `test_pagination_walks_every_page_without_repeating` | ✅ |
| Protection contre un curseur bloqué | `test_pagination_stops_if_the_cursor_repeats` | ✅ |
| Gestion du 429 avec `Retry-After` | `test_hubspot_retries_after_429_then_succeeds` | ✅ |
| Pas de réessai sur une erreur 4xx | `test_hubspot_does_not_retry_a_client_error` | ✅ |
| Idempotence des écritures | `test_idempotent_upsert_creates_a_single_company` | ✅ |
| Coupe-circuit sur service en panne | `test_circuit_breaker_opens_after_repeated_failures` | ✅ |
| Validé contre un compte HubSpot réel | — | ❌ **Non fait** (risque R9) |

## Envoi

| Contrôle | Preuve | État |
|---|---|---|
| Rien ne part sans validation | `test_send_without_approval_is_refused` | ✅ |
| Rien ne part vers un contact opposé | `test_send_to_suppressed_contact_is_refused` | ✅ |
| L'opposition prime sur tout | `test_suppression_is_checked_before_approval` | ✅ |
| Modifier le texte invalide la validation | `test_editing_the_body_invalidates_the_approval` | ✅ |
| L'auteur ne valide pas sa demande | `test_author_cannot_approve_their_own_request` | ✅ |
| Une validation expire | `test_expired_approval_blocks_the_send` | ✅ |
| Anti-doublon sur le contenu | `test_identical_content_to_same_contact_is_deduplicated` | ✅ |
| Plafond par contact | `test_contact_frequency_cap_is_enforced` | ✅ |
| Plafond par campagne | `test_campaign_daily_cap_is_enforced` | ✅ |
| En-têtes de désinscription | `test_sent_message_carries_one_click_unsubscribe_headers` | ✅ |
| Tout blocage est auditable | `test_every_block_leaves_an_auditable_event` | ✅ |
| Double verrou sur l'envoi réel | `test_real_sending_requires_both_flag_and_provider` | ✅ |

## Modèle de langage

| Contrôle | Preuve | État |
|---|---|---|
| Sortie validée par schéma | `test_malformed_llm_output_raises_instead_of_guessing` | ✅ |
| Motifs d'injection neutralisés | 7 cas dans `test_prompt_security.py` | ✅ |
| Un texte normal n'est pas mutilé | `test_legitimate_business_text_is_left_intact` | ✅ |
| Clés masquées avant le prompt | `test_secret_like_tokens_are_masked_before_reaching_the_model` | ✅ |
| Budget vérifié avant l'appel | `test_token_budget_refuses_the_call_before_spending` | ✅ |
| Un besoin inventé est écarté | `test_lead_intelligence_discards_a_need_absent_from_sources` | ✅ |
| Une panne n'interrompt pas la qualification | `test_lead_intelligence_survives_an_llm_outage` | ✅ |

## Rôles et API

| Contrôle | Preuve | État |
|---|---|---|
| Tout endpoint métier exige une authentification | 7 cas paramétrés | ✅ |
| Un lecteur ne peut pas écrire | `test_reader_cannot_write` | ✅ |
| Un commercial ne peut pas administrer | `test_commercial_cannot_administer` | ✅ |
| Seul un administrateur retire une opposition | `test_only_admin_can_remove_a_suppression` | ✅ |
| Un jeton altéré est refusé | `test_a_tampered_token_is_refused` | ✅ |
| L'échec de connexion ne révèle pas le compte | `test_invalid_credentials_are_refused_without_revealing_the_account` | ✅ |

## Indicateurs

| Contrôle | Preuve | État |
|---|---|---|
| Recalculés, jamais stockés | `test_kpis_are_recomputed_not_stored` | ✅ |
| Reproductibles | `test_growth_report_is_reproducible` | ✅ |
| `null` et non 0 sans base de calcul | `test_rates_are_null_when_there_is_no_basis` | ✅ |
| Un taux ne dépasse pas 100 % | `test_repeated_replies_from_one_prospect_count_once` | ✅ |
| Rejouer un événement ne gonfle rien | `test_recording_the_same_event_twice_is_idempotent` | ✅ |
| Pas de conclusion sur trop peu de données | `test_growth_report_refuses_to_conclude_on_thin_data` | ✅ |

## Migrations

| Contrôle | Preuve | État |
|---|---|---|
| Aller et retour | `test_upgrade_downgrade_upgrade_is_stable` | ✅ |
| Conformes aux modèles | `test_migration_matches_the_models` | ✅ |
| Contraintes d'unicité présentes | `test_unique_constraints_are_present` | ✅ |

## Interface

| Contrôle | Preuve | État |
|---|---|---|
| Compilation stricte | `tsc --noEmit` | ✅ |
| Tests unitaires | 23 tests | ✅ |
| Construction | 64 ko compressés | ✅ |
| Rendu réel des sept écrans | Vérifié au navigateur sur l'API réelle | ✅ |
| Aucune erreur console | Vérifié sur toutes les pages | ✅ |
| Responsive | Vérifié en 390 px | ✅ |
| Pas de métrique factice | `test_ui.test.tsx` | ✅ |
| Tests Playwright automatisés | — | ❌ Vérification manuelle seulement |

## Verdict

**Prêt pour un usage interne en mode simulé.** Le parcours complet fonctionne
et les garanties tiennent.

**Pas prêt pour une campagne réelle.** Trois points bloquants, aucun
technique :

1. cadre juridique camerounais non établi ;
2. domaine d'expédition non séparé ;
3. approbateurs non nommés.

Un point de dette technique mérite d'être remboursé rapidement : la purge
automatique des données (R3).
