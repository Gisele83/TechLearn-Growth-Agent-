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
| Masquage des traces d'exception | `test_an_exception_traceback_is_redacted` | ✅ corrigé après relecture |
| Masquage d'une erreur stockée en base | `test_a_failure_message_is_stored_without_personal_data` | ✅ |
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
| Purge automatique à échéance | `test_retention.py`, 28 tests | ✅ implémentée, **désactivée par défaut** |
| Les oppositions survivent à la purge | `test_opt_out_survives_the_purge_of_its_prospect` | ✅ |
| Un blocage légal gèle l'enregistrement | `test_legal_hold_freezes_an_expired_record` | ✅ |
| La simulation ne modifie rien | `test_dry_run_changes_absolutely_nothing` | ✅ |
| Les indicateurs ne bougent pas après purge | `test_kpis_do_not_move_after_a_purge` | ✅ |
| Verrou et reprise après incident | `test_retention_runner.py`, 10 tests | ✅ |
| Comportement des sauvegardes vérifié | `test_retention_backups.py`, 2 tests | ✅ constat documenté |
| La purge ne franchit pas la frontière entre produits | `test_the_purge_does_not_cross_product_boundaries` | ✅ |
| Toute voie de création pose une échéance | `test_every_creation_path_sets_a_retention_date` | ✅ |

## CRM

| Contrôle | Preuve | État |
|---|---|---|
| Pagination complète | `test_pagination_walks_every_page_without_repeating` | ✅ |
| Protection contre un curseur bloqué | `test_pagination_stops_if_the_cursor_repeats` | ✅ |
| Gestion du 429 avec `Retry-After` | `test_hubspot_retries_after_429_then_succeeds` | ✅ |
| Pas de réessai sur une erreur 4xx | `test_hubspot_does_not_retry_a_client_error` | ✅ |
| Idempotence des écritures | `test_idempotent_upsert_creates_a_single_company` | ✅ |
| Coupe-circuit sur service en panne | `test_circuit_breaker_opens_after_repeated_failures` | ✅ |
| Un rejeu immédiat ne crée pas de doublon | `test_local_memory_prevents_the_duplicate` | ✅ |
| La mémoire survit à un redémarrage | `test_memory_survives_a_restart` | ✅ |
| Mémoire perdue : le filet distant rattrape | `test_a_lost_local_memory_falls_back_to_the_remote_search` | ✅ |
| Une synchronisation répétée coûte un appel, pas deux | `test_a_repeated_sync_costs_one_call_instead_of_two` | ✅ |
| Une note déjà écrite n'est pas réécrite | `test_a_note_already_written_is_not_rewritten` | ✅ |
| Affaire rattachée à son entreprise et à son contact | `test_a_deal_is_attached_to_its_company_and_its_contact` | ✅ |
| Tâche rattachée à son contact | `test_a_task_is_attached_to_its_contact` | ✅ |
| Aucune association vers un objet absent | `test_nothing_is_associated_to_a_missing_counterpart` | ✅ |
| Le plafond de débit configuré est appliqué | `test_the_configured_rate_limit_is_actually_applied` | ✅ |
| Les réessais consomment aussi le quota | `test_retries_also_consume_the_quota` | ✅ |
| L'attente du limiteur n'enfle pas | `test_beyond_the_capacity_each_call_waits_its_turn` | ✅ |
| Plusieurs tâches pour un prospect | `test_coordinator_handles_several_tasks_for_one_prospect` | ✅ |
| 10 écritures concurrentes → 1 correspondance | `test_concurrent_crm_writes_create_one_mapping` *(PostgreSQL)* | ✅ |
| Validé contre un compte HubSpot réel | `tests/test_crm_integration.py` — prêt, non exécuté | ❌ **Non fait** (risque R9) |

Les tests d'intégration CRM sont écrits mais **exclus par défaut** : ils
parlent à un portail réel. Ils se lancent contre un bac à sable, jamais
contre un portail de production :

```sh
GA_CRM_SANDBOX_TOKEN="pat-…" pytest -m integration tests/test_crm_integration.py
```

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

## Désinscription

| Contrôle | Preuve | État |
|---|---|---|
| Le jeton porte 256 bits d'entropie | `test_token_carries_256_bits_of_entropy` | ✅ |
| 200 jetons, aucun motif détectable | `test_tokens_are_never_repeated_and_look_random` | ✅ |
| La base ne contient que l'empreinte | `test_database_never_stores_the_token_in_clear` | ✅ |
| Le clic fonctionne sans compte | `test_one_click_post_works_without_authentication` | ✅ |
| Recliquer confirme sans erreur | `test_one_click_post_is_idempotent` | ✅ |
| Un jeton inconnu ne révèle rien | `test_unknown_token_reveals_nothing` | ✅ |
| Un jeton révoqué cesse de marcher | `test_revoked_token_stops_working` | ✅ |
| Le lien du message désinscrit vraiment | `test_link_from_the_sent_message_actually_unsubscribes` | ✅ |
| La page ne charge aucune ressource tierce | `test_human_page_is_self_contained` | ✅ |
| Le lien vit hors du préfixe d'API | `test_public_routes_are_outside_the_api_prefix` | ✅ |

## Idempotence, pannes et concurrence

| Contrôle | Preuve | État |
|---|---|---|
| Panne avant la remise : rien n'est parti, reprise refusée | `test_crash_before_handoff_leaves_a_reservation_and_blocks_the_retry` | ✅ |
| Réponse illisible : issue inconnue, jamais rejouée | `test_provider_timeout_marks_the_send_uncertain_and_never_replays_it` | ✅ |
| Panne après la remise : pas de doublon au redémarrage | `test_crash_after_handoff_does_not_send_twice_after_restart` | ✅ |
| Refus explicite : la réservation est levée | `test_explicit_refusal_releases_the_reservation` | ✅ |
| 12 travailleurs concurrents → 1 seul envoi | `test_twelve_concurrent_workers_send_exactly_one_message` *(PostgreSQL)* | ✅ |
| La contrainte d'unicité existe bien en base | `test_unique_constraint_is_what_enforces_it` *(PostgreSQL)* | ✅ |
| L'état survit à un redémarrage | `test_state_survives_a_restart` *(PostgreSQL)* | ✅ |
| 8 clics simultanés → 1 seule opposition | `test_concurrent_unsubscribes_do_not_duplicate_the_suppression` *(PostgreSQL)* | ✅ |

Les quatre derniers exigent un vrai PostgreSQL — SQLite sérialise les écrivains
et ne peut pas démontrer la course. Ils s'ignorent automatiquement sans lui :

```sh
GA_TEST_PG_URL="postgresql+psycopg://user@/base?host=/chemin/socket" \
    pytest tests/test_concurrency_pg.py -v -s
```

## Simulé et réel

| Contrôle | Preuve | État |
|---|---|---|
| Une répétition porte un statut distinct | `test_mock_provider_marks_the_event_simulated` | ✅ |
| Un envoi réel est marqué réel | `test_real_provider_marks_the_event_real` | ✅ |
| Une répétition ne bloque pas l'envoi réel | `test_a_rehearsal_does_not_consume_the_real_idempotency_key` | ✅ |
| Les répétitions ne comptent pas comme contacts | `test_simulated_sends_are_never_counted_as_contacted` | ✅ |
| Réservé et incertain ne comptent pas non plus | `test_reserved_and_uncertain_sends_are_not_counted_either` | ✅ |

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
| La matrice des quatre rôles est celle attendue | `test_the_role_ladder_separates_producing_from_approving` | ✅ |
| Un opérateur reporte une réponse et déclare un relevé | `test_an_operator_can_report_a_reply_and_declare_a_relay` | ✅ |
| **Un opérateur ne peut pas approuver un message** | `test_an_operator_cannot_approve_a_message` | ✅ |
| Un opérateur peut demander une validation | `test_an_operator_can_still_request_an_approval` | ✅ |
| Un commercial approuve toujours | `test_a_commercial_can_still_approve` | ✅ |
| Un opérateur ne peut pas administrer | `test_an_operator_cannot_administer` | ✅ |
| Un lecteur ne peut pas déclarer un relevé | `test_a_reader_can_neither_report_a_reply_nor_declare_a_relay` | ✅ |
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
| **0006 sur PostgreSQL 16 réel** : reprise des lignes existantes, retrait du défaut serveur, index, deux allers-retours | Cluster temporaire sur socket Unix, aucun port TCP, supprimé après essai | ✅ |

La migration `0006` a une branche propre à PostgreSQL — le retrait de la
valeur par défaut après la reprise — que SQLite n'exerce jamais. Elle a donc
été éprouvée sur un vrai moteur : huit événements écrits **avant** la
migration, puis vérification que `sent`, `send_blocked`, `delivered`,
`bounced` et `opened` ressortent « constatés » et le reste « déclaré », que
la colonne n'a plus de valeur par défaut, et que le schéma redescend et
remonte deux fois de suite.

## Naturel des messages

| Contrôle | Preuve | État |
|---|---|---|
| Un message produit passe ses propres contrôles | `test_a_composed_message_passes_the_naturalness_check` | ✅ |
| Signé par une personne | `test_a_composed_message_is_signed_by_a_person` | ✅ |
| Refus de rédiger sans expéditeur | `test_the_composer_refuses_to_write_without_a_sender` | ✅ |
| Aucune URL collée dans le texte | `test_a_composed_message_never_pastes_a_url` | ✅ |
| Entre 40 et 120 mots | `test_a_composed_message_stays_short` | ✅ |
| Les clichés bloquent le brouillon | 8 cas paramétrés | ✅ |
| Chaque constat propose un remplacement | `test_every_cliche_comes_with_a_replacement` | ✅ |
| Un bon message n'est pas signalé | `test_a_well_written_message_scores_full_marks` | ✅ |
| Pas de jargon interne dans le message | `test_the_message_quotes_the_prospect_not_our_internal_notes` | ✅ corrigé après exécution réelle |
| Accord en nombre dans l'objet | 4 cas paramétrés | ✅ |
| Horodatages non pris pour des téléphones | `test_a_timestamp_is_not_mistaken_for_a_phone_number` | ✅ corrigé |

## Interface

| Contrôle | Preuve | État |
|---|---|---|
| Compilation stricte | `tsc --noEmit` | ✅ |
| Tests unitaires | 57 tests (dont 30 pour « Performance commerciale ») | ✅ |
| Construction | 67 ko compressés | ✅ |
| Rendu réel des huit écrans | Vérifié au navigateur sur l'API réelle | ✅ |
| Aucune erreur console | Vérifié sur toutes les pages | ✅ |
| Responsive | Vérifié en 390 px | ✅ |
| Pas de métrique factice | `test_ui.test.tsx` | ✅ |
| Captures reproductibles de l'écran de suivi | `scripts/demo-captures.sh` → `docs/captures/` | ✅ |
| Tests Playwright automatisés du parcours complet | — | ❌ Vérification manuelle seulement |

## Expédition et opposition

| Contrôle | Preuve | État |
|---|---|---|
| Sans boîte de réponse, le message n'invite pas à répondre | `test_without_a_monitored_mailbox_the_message_does_not_invite_a_reply` | ✅ |
| Le lien de désinscription est visible dans le texte | `test_the_sent_message_always_carries_a_visible_unsubscribe_link` | ✅ |
| Le pied n'altère pas le texte approuvé | `test_the_footer_is_added_without_touching_the_stored_draft` | ✅ |
| 15 formulations d'opposition reconnues | `test_every_realistic_phrasing_is_recognised` | ✅ |
| Aucun faux positif sur une réponse ordinaire | `test_an_ordinary_reply_is_not_mistaken_for_an_objection` | ✅ |
| Une réponse STOP bloque l'envoi suivant | `test_a_stop_reply_blocks_the_next_send_immediately` | ✅ |
| L'opposition survit à un redémarrage | `test_the_objection_survives_a_restart` | ✅ |
| SendGrid : refus définitif distingué d'une panne | `test_a_rejected_message_is_a_refusal_not_an_unknown_outcome` | ✅ |
| SendGrid : expéditeur non vérifié traité comme un refus | `test_an_unverified_sender_is_a_refusal` | ✅ |
| SendGrid : aucun suivi de clic ni d'ouverture | `test_nothing_is_tracked` | ✅ |
| SendGrid : en-têtes RFC 8058 préservés | `test_the_unsubscribe_headers_survive_intact` | ✅ |
| SendGrid : la clé n'apparaît pas dans la sonde | `test_the_healthcheck_never_reveals_the_key` | ✅ |
| La boîte de réponse réactive l'invitation à répondre | `test_the_confirmed_mailbox_turns_the_invitation_back_on` | ✅ |
| Le lien reste présent même quand on invite à répondre | `test_a_monitored_message_still_carries_the_link` | ✅ |
| L'adresse de réponse atteint le fournisseur | `test_the_configured_reply_address_reaches_the_provider` | ✅ |
| Un relevé vide laisse une trace | `test_an_empty_relay_counts_as_a_relay` | ✅ |
| Le relevé est attribué à son auteur | `test_an_empty_relay_is_recorded_and_attributed` | ✅ |
| Un lecteur ne peut pas attester d'un relevé | `test_a_reader_cannot_declare_a_relay` | ✅ |
| Quatre jours sans relevé déclenchent une alerte | `test_four_days_of_silence_does` | ✅ |
| Un week-end ne déclenche rien | `test_a_weekend_does_not_set_off_the_alarm` | ✅ |
| L'alerte porte ses destinataires | `test_the_alert_carries_who_to_warn` | ✅ |
| Aucune adresse n'atteint les métriques | `test_no_address_ever_reaches_the_metrics` | ✅ |
| L'absence de suppléance est elle-même signalée | `test_an_unnamed_deputy_is_itself_an_alert` | ✅ |
| Une relance est suspendue si le relevé est trop vieux | `test_a_follow_up_is_held_when_the_declaration_is_stale` | ✅ |
| Le brouillon suspendu n'est pas condamné | `test_the_held_draft_is_not_condemned` | ✅ |
| La suspension laisse un événement motivé | `test_the_hold_is_recorded_as_a_blocked_event` | ✅ |
| Un premier contact n'est jamais suspendu | `test_a_first_contact_goes_out_even_without_any_declaration` | ✅ |
| Sans boîte promise, rien n'est suspendu | `test_follow_ups_are_not_held_when_no_mailbox_is_promised` | ✅ |
| **Une opposition enregistrée prime sur le relevé en retard** | `test_a_recorded_objection_is_honoured_even_with_a_stale_inbox` | ✅ |
| Une opposition peut entrer quel que soit l'âge du relevé | `test_an_objection_can_always_be_recorded_whatever_the_relay_age` | ✅ |
| Le seuil de fraîcheur est configurable | `test_the_threshold_is_configurable` | ✅ |
| **Relevé automatique de la boîte de réponse** | — | ❌ **Non fait** — relevé manuel par Merveille Nguetse, surveillé, voir `docs/08-supervision.md` |
| **Envoi réel vers une boîte de test** | — | ❌ **Non fait** — en attente de D2 et du procès-verbal |
| **Taux de réponse et entonnoir démo/pilote** | — | ⚠️ **Partiels** — ne comptent que les réponses reportées à la main |

`contact@techlearn-saem.com` existe et reçoit (confirmé le 16/10/2026),
relevée chaque jour ouvré par **Merveille Nguetse**. Les messages invitent
donc de nouveau à répondre « STOP ». Rien ne relève la boîte
automatiquement, mais le relevé est **observable** : chaque passage laisse
une trace, y compris quand la boîte est vide, et une alerte se déclenche
après quatre jours de silence. Le produit ne prétend pas pour autant à une
réception automatique — `/api/v1/system/integrations` renvoie
`inbound_integration: "aucune"` et `reply_handling: "manuelle"`, et les KPI
portent la note correspondante.

Dans les tests, la réponse STOP est injectée dans l'analyste comme si elle
venait d'arriver. Tout ce qui suit la détection est prouvé ; **le relevé
lui-même reste humain**.

## Supervision

| Contrôle | Preuve | État |
|---|---|---|
| La vivacité ne dépend d'aucune dépendance | `test_liveness_never_depends_on_anything` | ✅ |
| La disponibilité retire le trafic si le schéma est en retard | `test_readiness_refuses_traffic_when_the_schema_is_behind` | ✅ |
| Sondes et métriques hors limiteur de débit | `test_the_probes_are_not_rate_limited` | ✅ |
| /metrics refusé sans jeton configuré | `test_metrics_are_refused_when_no_token_is_configured` | ✅ |
| /metrics refusé avec un mauvais jeton | `test_metrics_require_the_right_token` | ✅ |
| Aucune donnée personnelle dans les métriques | `test_no_personal_data_reaches_the_metrics` | ✅ |
| Étiquettes bornées à des ensembles fermés | `test_label_values_stay_within_closed_sets` | ✅ |
| Simulé et réel comptés à part | `test_simulated_and_real_sends_are_counted_apart` | ✅ |
| Blocages ventilés par motif | `test_blocked_sends_are_split_by_reason` | ✅ |
| Réservation abandonnée détectée | `test_an_old_reservation_reveals_a_dead_worker` | ✅ |
| Une répétition de purge ne compte pas comme une purge | `test_a_dry_run_does_not_count_as_a_purge` | ✅ |
| Les métriques utilisent les statuts que le runner écrit | `test_the_metrics_use_the_statuses_the_runner_actually_writes` | ✅ |
| Un arrêt par garde-fou n'est pas un échec | `test_a_guardrail_stop_is_not_reported_as_a_failure` | ✅ |
| Chaque alerte porte un geste | `test_every_alert_carries_a_runbook` | ✅ |
| Le fichier de règles ne diverge pas du code | `test_the_alert_rules_file_matches_the_code` | ✅ |
| Alertes accessibles sans pile Prometheus | `test_the_alerts_endpoint_needs_authentication` | ✅ |

## Sauvegarde et restauration

| Contrôle | Preuve | État |
|---|---|---|
| Sauvegarde PostgreSQL réelle prise | Laboratoire, 16/10/2026 — `pg_dump --format=custom`, 21 tables | ✅ |
| Archive vérifiée sans être appliquée | `pg_restore --list` | ✅ |
| Restauration sur base vierge | Laboratoire, 16/10/2026 | ✅ |
| L'application démarre sur la base restaurée | `/health/ready` → 200, révision en tête | ✅ |
| Conséquence de la restauration sur la purge | `docs/08-supervision.md` | ✅ documentée |
| Restauration éprouvée sur l'instance de **production** | — | ❌ **Non fait** (aucune instance) |

## Pipeline commercial

| Contrôle | Preuve | État |
|---|---|---|
| Un prospect n'a jamais deux dossiers ouverts | `test_a_prospect_never_gets_two_open_files` | ✅ |
| Un dossier clos n'interdit pas d'en rouvrir un | `test_a_closed_file_does_not_block_a_new_one` | ✅ |
| Perdre un dossier exige un motif | `test_losing_a_file_requires_a_reason` | ✅ |
| Les motifs de perte sont une liste fermée | `test_an_unknown_loss_reason_is_refused` | ✅ |
| Un dossier ne recule jamais | `test_a_file_never_goes_backwards` | ✅ |
| Un passage d'étape laisse un événement, une seule fois | `test_replaying_a_stage_does_not_inflate_the_funnel` | ✅ |
| Une démonstration tenue produit une suite | `test_a_held_demo_advances_and_produces_a_next_step` | ✅ |
| Un rendez-vous manqué aussi | `test_a_missed_demo_still_produces_a_next_step` | ✅ |
| Aucun verbatim dans le journal d'audit | `test_the_audit_trail_carries_categories_not_verbatims` | ✅ |
| Une action sans porteur est refusée | `test_an_action_without_an_owner_is_refused` | ✅ |
| « En retard » = échu et encore ouvert | `test_overdue_is_what_is_late_and_still_open` | ✅ |
| La charge montre qui croule | `test_workload_shows_who_is_drowning` | ✅ |
| Les tâches CRM redeviennent visibles | `test_a_coordinator_task_is_mirrored_locally` | ✅ |
| L'opposition empêche tâche **et** double local | `test_no_task_no_mirror_for_a_suppressed_contact` | ✅ |
| **La purge atteint les nouvelles tables** | `test_the_purge_reaches_the_new_tables` | ✅ |
| Parcours complet via l'API | `test_an_operator_runs_the_pipeline_end_to_end` | ✅ |
| Un lecteur voit sans modifier | `test_a_reader_sees_the_pipeline_but_does_not_change_it` | ✅ |

## Lecture par cohorte

| Contrôle | Preuve | État |
|---|---|---|
| Les répétitions ne forment pas de cohorte | `test_without_any_real_send_there_is_no_cohort` | ✅ |
| La cohorte est le mois du premier contact | `test_a_prospect_belongs_to_the_month_of_its_first_contact` | ✅ |
| La conversion se lit par cohorte, pas en cumulé | `test_conversion_is_read_against_the_cohort_not_the_total` | ✅ |
| Une cohorte trop jeune est signalée | `test_a_young_cohort_is_flagged_not_hidden` | ✅ |
| Une réservation sans suite n'est pas un contact | `test_a_reservation_without_follow_through_is_not_a_contact` | ✅ |
| La médiane résiste à un dossier qui traîne | `test_the_median_resists_one_file_that_drags` | ✅ |
| Une étape jamais atteinte n'a pas de médiane | `test_a_stage_never_reached_has_no_median` | ✅ |
| Les pertes se comptent par motif | `test_losses_are_counted_by_reason` | ✅ |
| Les envois bloqués sont enfin affichés | `test_blocked_sends_are_surfaced_by_reason` | ✅ |
| Les retards sont attribués | `test_overdue_actions_are_attributed` | ✅ |
| Le filtre par campagne isole bien une campagne | `test_the_campaign_filter_isolates_one_campaign` | ✅ |
| Une campagne inconnue est refusée, pas ignorée | `test_an_unknown_campaign_is_refused_rather_than_silently_ignored` | ✅ |
| **Validation sur deux cohortes réelles** | — | ❌ **Non fait** — exige D2 et l'autorisation d'envoi |

## Constaté ou déclaré

| Contrôle | Preuve | État |
|---|---|---|
| Ce que le système fait lui-même est marqué constaté | `test_what_the_system_does_itself_is_recorded_as_observed` | ✅ |
| Ce qu'une personne saisit est marqué déclaré | `test_a_stage_someone_types_in_is_recorded_as_declared` | ✅ |
| La reprise de la migration 0006 suit ce que le code écrit | `test_the_migration_backfill_agrees_with_what_the_code_writes` | ✅ vérifié en désynchronisant |
| Le rapport dit, par étape, ce qu'il a constaté | `test_the_report_says_how_many_of_each_stage_it_actually_saw` | ✅ |
| Jamais plus de constatés que de comptés | `test_what_was_observed_never_exceeds_what_was_counted` | ✅ |
| Un rapport majoritairement déclaré le dit | `test_a_report_made_mostly_of_declarations_says_so` | ✅ |
| L'avertissement disparaît quand tout est constaté | `test_a_fully_observed_report_carries_no_declaration_warning` | ✅ |
| Un rendez-vous manqué est compté à part | `test_a_missed_meeting_is_counted_apart_from_demos` | ✅ |
| Aucun fait annexe n'entre dans l'entonnoir | `test_every_side_fact_is_kept_out_of_the_funnel` | ✅ |
| L'écran lit les étapes que l'API produit | `test_the_screen_reads_the_stages_the_api_actually_produces` | ✅ vérifié en désynchronisant |
| L'écran lit les délais que l'API produit | `test_the_screen_reads_the_median_keys_the_api_actually_produces` | ✅ vérifié en désynchronisant |

## Opposition et relances humaines

Une opposition arrêtait les envois. Elle n'arrêtait pas les tâches confiées
à une personne : un rendez-vous manqué produisait « Reprogrammer la
démonstration » pour un contact qui venait d'écrire STOP.

| Contrôle | Preuve | État |
|---|---|---|
| Un rendez-vous manqué après opposition ne planifie pas de rappel | `test_a_missed_meeting_after_an_opposition_does_not_schedule_a_call_back` | ✅ corrigé |
| Sans opposition, la reprogrammation reste proposée | `test_without_an_opposition_a_missed_meeting_still_asks_to_reschedule` | ✅ |
| Le fait reste compté même après opposition | `test_a_missed_meeting_is_still_recorded_when_the_contact_opposed` | ✅ |
| Aucune nature d'action de contact n'échappe au contrôle | `test_no_contact_action_can_be_scheduled_for_an_opposed_contact` | ✅ 5 natures |
| Le travail interne reste possible | `test_internal_work_remains_possible_after_an_opposition` | ✅ |
| Une opposition de domaine couvre ses adresses | `test_an_opposition_on_the_domain_stops_the_follow_up_too` | ✅ |
| Une opposition sur l'autre produit ne bloque pas celui-ci | `test_an_opposition_on_the_other_product_does_not_block_this_one` | ✅ |
| Sans adresse, rien n'a été refusé | `test_a_prospect_without_an_address_refused_nothing` | ✅ |
| **Une action inscrite avant l'opposition reste ouverte** | `test_an_opposition_recorded_after_the_action_does_not_rewrite_it` | ⚠️ limite assumée, annulation manuelle |

## Réidentification après purge

| Contrôle | Preuve | État |
|---|---|---|
| Balayage de toutes les tables après purge | `test_after_the_purge_nothing_in_the_database_still_names_the_person` | ✅ |
| Le nom ne survit nulle part | `test_the_persons_name_survives_nowhere_at_all` | ✅ |
| Chaque survivance tolérée est motivée par écrit | `test_every_tolerated_survival_states_a_reason` | ✅ |
| Une source perd son titre et le chemin de son URL | `test_a_source_keeps_its_origin_and_loses_its_path` | ✅ corrigé |
| Une évaluation perd ses citations, garde son score | `test_an_assessment_keeps_its_score_and_loses_its_quotes` | ✅ corrigé |
| Le `payload` d'un événement est réduit à une liste blanche | `test_an_event_payload_keeps_only_what_an_indicator_reads` | ✅ corrigé |
| Le pointeur vers la fiche CRM est retiré | `test_the_pointer_to_the_crm_record_is_dropped` | ✅ corrigé |
| Un brouillon perd son motif de blocage | `test_a_blocked_draft_loses_the_reason_it_was_blocked` | ✅ corrigé |
| Les indicateurs passés restent inchangés | `test_the_indicators_are_unchanged_by_the_deeper_anonymization` | ✅ |
| Une opposition bloque encore après la purge | `test_an_opposition_still_blocks_a_send_after_the_purge` | ✅ |

## Écran « Performance commerciale »

| Contrôle | Preuve | État |
|---|---|---|
| Une ligne par cohorte, la plus récente comprise | `donne une ligne à chaque cohorte` | ✅ |
| Cohorte de moins de 30 jours signalée, pas masquée | `signale une cohorte de moins de 30 jours sans la masquer` | ✅ vérifié en la masquant |
| Volume constaté distingué du volume saisi | `distingue un volume constaté d'un volume saisi à la main` | ✅ vérifié en les confondant |
| Aucune moyenne cumulée | `n'affiche aucune moyenne cumulée` | ✅ |
| Un tiret, jamais 0 %, sans dénominateur | `écrit un tiret, et non 0 %, quand la cohorte n'a aucun contact` | ✅ vérifié en forçant 0 |
| Filtres produit, période, campagne transmis à l'API | `interroge l'API avec le produit, la période et la campagne choisis` | ✅ |
| Changer de produit oublie la campagne | `oublie la campagne quand on change de produit` | ✅ |
| Le filtre par cohorte ne relance pas la requête | `restreint l'affichage à une cohorte sans relancer la requête` | ✅ |
| Aucun chiffre affiché quand l'API échoue | `n'affiche aucun chiffre quand l'API échoue` | ✅ |
| Un refus d'accès se distingue d'une panne | `distingue un refus d'accès d'une panne` | ✅ |
| État vide expliqué | `explique l'absence de cohorte au lieu d'afficher un tableau vide` | ✅ |
| Les mises en garde de l'API sont reprises telles quelles | `reprend telles quelles les mises en garde de l'API` | ✅ |
| **Restriction de la page aux seuls rôles de direction** | — | ❌ arbitrage ouvert : l'API l'ouvre à tout compte en lecture |

## Verdict

**Prêt pour un usage interne en mode simulé.** Le parcours complet fonctionne
et les garanties tiennent.

**Pas prêt pour une campagne réelle.** Trois points bloquants, aucun
technique :

1. cadre juridique camerounais non établi ;
2. domaine d'expédition non séparé ;
3. approbateurs non nommés.

La purge automatique des données (R3) a été implémentée depuis : il reste à
l'activer en production, par étapes, selon la procédure de mise en service de
la politique de rétention.
