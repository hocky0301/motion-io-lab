# Mutation check

Each row is one deliberate defect put into a copy of the code. A mutant is *killed* when the tests fail twice in a row; the first failing test is listed. The unchanged code passes first.

* **Set A, defects chosen together with the tests** (run against the tests meant to catch them): 32 of 32 killed. This shows the tests are not vacuous; it says little about what they miss.
* **Set B, mutants prepared separately from the tests** (run against the whole suite): 165 of 165 killed. This is the independent measure.
* **Judged equivalent** (run too; they must survive): 8 of 8 survived.

## Set A

| mutant | result | first failing test |
|---|---|---|
| fw: SET without epoch check | killed | `tests/test_conformance.py::test_hand_written_script_matches_firmware_line_for_line` |
| fw: duplicate seq latches a fault | killed | `tests/test_conformance.py::test_hand_written_script_matches_firmware_line_for_line` |
| fw: slew limit removed | killed | `tests/test_conformance.py::test_hand_written_script_matches_firmware_line_for_line` |
| fw: the ALL_LED_OFF_H=0 write comes back | killed | `tests/test_conformance.py::test_hand_written_script_matches_firmware_line_for_line` |
| fw: parser accepts a minus sign | killed | `tests/test_conformance.py::test_hand_written_script_matches_firmware_line_for_line` |
| fw: pulse range check removed | killed | `tests/test_conformance.py::test_hand_written_script_matches_firmware_line_for_line` |
| fw: HELLO keeps the old epoch | killed | `tests/test_conformance.py::test_hand_written_script_matches_firmware_line_for_line` |
| fw: a rejection extends the lease | killed | `tests/test_conformance.py::test_random_scripts_agree_with_firmware[10]` |
| fw: overflow fragment is handled as a command (a double STOP) | killed | `tests/test_conformance.py::test_hand_written_script_matches_firmware_line_for_line` |
| fw: ARM allowed while HARDWARE_VERIFIED is false | killed | `tests/test_conformance.py::test_not_verified_firmware_refuses_arm_in_both` |
| fw: lease length not checked | killed | `tests/test_conformance.py::test_hand_written_script_matches_firmware_line_for_line` |
| fw: stop does not invalidate the epoch | killed | `tests/test_conformance.py::test_hand_written_script_matches_firmware_line_for_line` |
| fw: OE not driven high at boot | killed | `tests/test_conformance.py::test_hand_written_script_matches_firmware_line_for_line` |
| vd: SET without epoch check | killed | `tests/test_vdevice.py::test_replayed_arm_and_set_after_reconnect_are_rejected_and_do_not_move_anything` |
| vd: duplicate seq becomes a fault | killed | `tests/test_vdevice.py::test_duplicate_seq_is_a_harmless_acknowledgement` |
| vd: slew limit removed | killed | `tests/test_vdevice.py::test_slew_limit_is_respected_at_every_tick` |
| vd: lease refreshed by duplicates | killed | `tests/test_vdevice.py::test_duplicate_seq_is_a_harmless_acknowledgement` |
| vd: stop keeps the epoch | killed | `tests/test_vdevice.py::test_replay_within_the_same_connection_after_disarm_is_rejected` |
| host: shaper without clamp | killed | `tests/test_shaping_motions_calibration.py::test_clamp_and_flags` |
| host: capture time rule dropped | killed | `tests/test_triallog.py::test_capture_time_cannot_be_invented` |
| host: writer appends instead of refusing | killed | `tests/test_triallog.py::test_never_appends_or_overwrites` |
| host: invalid observation may carry a value | killed | `tests/test_triallog.py::test_invalid_observation_must_not_carry_zero` |
| host: observation picks the first of several blobs | killed | `tests/test_observe.py::test_dropout_and_second_blob_frames_are_invalid_with_a_reason_and_null_values` |
| host: pacing relative is the only mode | killed | `tests/test_record_play.py::test_deadline_pacing_does_not_drift_but_sleep_after_send_does` |
| host: late commands are sent anyway | killed | `tests/test_record_play.py::test_late_commands_are_dropped_not_sent_stale` |
| host: resume without allow_clear | killed | `tests/test_record_play.py::test_resume_requires_a_latched_device_to_be_cleared_by_a_human_decision` |
| host: dataset valid column silently falsy | killed | `tests/test_dataset.py::test_valid_column_is_strict_a_typo_is_an_error_not_a_silent_rejection` |
| host: dataset overwrite allowed | killed | `tests/test_dataset.py::test_overwrite_is_refused_with_or_without_the_extension_and_for_the_manifest` |
| host: touch token not checked | killed | `tests/test_inputs.py::test_token_is_required_on_every_endpoint_even_on_loopback` |
| host: LAN bind by default | killed | `tests/test_inputs.py::test_binds_to_loopback_unless_lan_is_asked_for` |
| host: on_grid extrapolates | killed | `tests/test_analysis.py::test_on_grid_zero_order_hold_and_gap_to_nan` |
| host: hardware needs no confirmation | killed | `tests/test_record_play.py::test_hardware_style_link_needs_a_human_confirmation` |

## Set B (prepared separately)

| mutant | result | first failing test |
|---|---|---|
| shaper: truncate instead of round | killed | `tests/test_demo.py::test_summary_matches_the_checked_in_reference_within_tolerance` |
| shaper: slew limit doubled | killed | `tests/test_demo.py::test_hard_step_shows_the_device_limit` |
| shaper: clamp flag never set | killed | `tests/test_shaping_motions_calibration.py::test_clamp_and_flags` |
| shaper: slew flag never set | killed | `tests/test_more_edges.py::test_the_slew_limit_also_holds_for_a_short_period` |
| shaper: slew limit ignored when dt small | killed | `tests/test_more_edges.py::test_the_slew_limit_also_holds_for_a_short_period` |
| x_to_pulse: no clip of out-of-range axis | killed | `tests/test_shaping_motions_calibration.py::test_x_to_pulse_maps_and_clips` |
| validate: seq may repeat within epoch | killed | `tests/test_triallog.py::test_sequence_must_increase_within_an_epoch_but_may_restart_in_a_new_one` |
| validate: seq checked across epochs | killed | `tests/test_cli.py::test_summarize_session_reads_the_source_from_the_logs_and_never_overwrites` |
| validate: backwards receive time allowed | killed | `tests/test_triallog.py::test_validator_reports_line_numbers_for_every_defect` |
| validate: header/trial id mismatch allowed | killed | `tests/test_triallog.py::test_validator_reports_line_numbers_for_every_defect` |
| validate: sent time not required on linked sets | killed | `tests/test_stage_labels.py::test_a_set_row_on_a_link_needs_its_send_time_and_a_reject_needs_its_reason` |
| validate: second header allowed | killed | `tests/test_triallog.py::test_validator_rejects_missing_header_second_header_blank_and_nan` |
| validate: nonfinite constant allowed | killed | `tests/test_triallog.py::test_validator_rejects_missing_header_second_header_blank_and_nan` |
| writer: records not validated | killed | `tests/test_stage_labels.py::test_a_set_row_on_a_link_needs_its_send_time_and_a_reject_needs_its_reason` |
| check_id allows slash | killed | `tests/test_triallog.py::test_ids_may_not_end_with_a_newline_or_hide_a_path` |
| observation marker invalid keeps cx | killed | `tests/test_stage_labels.py::test_an_invalid_observation_may_carry_no_coordinate[cx_px]` |
| reply REJECTED needs no reason | killed | `tests/test_stage_labels.py::test_a_set_row_on_a_link_needs_its_send_time_and_a_reject_needs_its_reason` |
| lag: parabola sign flipped | killed | `tests/test_analysis.py::test_lag_estimate_recovers_a_known_shift[87.0]` |
| lag: min overlap 2 samples | killed | `tests/test_more_edges.py::test_a_lag_needs_enough_overlapping_samples` |
| lag: mean not removed | killed | `tests/test_analysis.py::test_lag_estimate_ignores_scale_and_offset_and_nan_gaps` |
| lag: search window 100 ms | killed | `tests/test_analysis.py::test_lag_estimate_recovers_a_known_shift[123.0]` |
| repeat_stats: ddof 0 | killed | `tests/test_analysis.py::test_repeat_stats_handles_nan_gaps_without_warnings` |
| on_grid: gap bridge allowed | killed | `tests/test_analysis.py::test_on_grid_zero_order_hold_and_gap_to_nan` |
| on_grid: hold uses next sample | killed | `tests/test_analysis.py::test_on_grid_zero_order_hold_and_gap_to_nan` |
| extract: applied time from sent not reply | killed | `tests/test_analysis.py::test_extract_uses_the_first_sent_command_as_time_zero_and_keeps_series_apart` |
| summarize_values: ddof 0 | killed | `tests/test_analysis.py::test_summarize_and_window_mean_and_rms` |
| timing: drift in ppb not ppm | killed | `tests/test_demo.py::test_pacing_deadline_does_not_drift_relative_does` |
| timing: p95 as p90 | killed | `tests/test_demo.py::test_summary_matches_the_checked_in_reference_within_tolerance` |
| resample: speed inverted | killed | `tests/test_record_play.py::test_segment_and_speed_are_respected` |
| resample: drops the last sample | killed | `tests/test_demo.py::test_summary_matches_the_checked_in_reference_within_tolerance` |
| resample: segment start ignored | killed | `tests/test_record_play.py::test_segment_and_speed_are_respected` |
| PlayConfig: faster than recorded allowed | killed | `tests/test_record_play.py::test_segment_and_speed_are_respected` |
| PlayConfig: valid_for upper not checked | killed | `tests/test_stage_labels.py::test_the_lease_length_limits_are_20_to_500_ms` |
| player: fault rejection not fatal | killed | `tests/test_stage_labels.py::test_every_rejection_that_means_you_are_no_longer_in_a_session_ends_the_replay` |
| player: hold samples not flagged | killed | `tests/test_record_play.py::test_plan_uses_hold_samples_and_keeps_the_final_value` |
| player: late drop logged as sent | killed | `tests/test_stage_labels.py::test_late_drops_are_not_counted_as_sent` |
| player: reshape ignores device start | killed | `tests/test_stage_labels.py::test_a_replay_starts_from_where_the_device_actually_is` |
| player: resume capped removed | killed | `tests/test_stage_labels.py::test_a_replay_gives_up_after_two_resumes` |
| client: duplicate counted as accepted | killed | `tests/test_stage_labels.py::test_duplicates_are_counted_apart_from_acceptances` |
| client: applied taken from commanded | killed | `tests/test_stage_labels.py::test_status_keeps_commanded_and_applied_in_their_own_columns` |
| client: seq not reset on HELLO | killed | `tests/test_stage_labels.py::test_hello_restarts_the_sequence_and_disarm_forgets_the_epoch` |
| client: disarm keeps epoch | killed | `tests/test_stage_labels.py::test_hello_restarts_the_sequence_and_disarm_forgets_the_epoch` |
| protocol: parse treats dup as str | killed | `tests/test_cli.py::test_record_play_export_roundtrip_through_the_cli` |
| session: latched device not refused | killed | `tests/test_link_pty.py::test_start_device_refuses_a_latched_device_without_clearing_it` |
| reference: loose abs tolerance 5 deg | killed | `tests/test_demo.py::test_reference_comparison_catches_real_differences` |
| reference: strict rel 1e-1 | killed | `tests/test_demo.py::test_reference_comparison_catches_real_differences` |
| reference: extra keys allowed | killed | `tests/test_demo.py::test_reference_comparison_catches_real_differences` |
| reference: list length ignored | killed | `tests/test_demo.py::test_reference_comparison_checks_list_lengths_and_types` |
| reference: strings not compared | killed | `tests/test_demo.py::test_reference_comparison_catches_real_differences` |
| reference: NaN passes | killed | `tests/test_more_edges.py::test_a_nan_is_never_close_to_a_reference_number` |
| manifest: part files hashed / manifest self hash | killed | `tests/test_cli.py::test_two_commands_in_one_trial_directory_keep_separate_manifests` |
| manifest: seed dropped | killed | `tests/test_demo.py::test_manifest_hashes_match_the_files` |
| manifest: overwrite allowed | killed | `tests/test_cli.py::test_two_commands_in_one_trial_directory_keep_separate_manifests` |
| dataset: groups by trial not session | killed | `tests/test_dataset.py::test_good_file_converts_and_manifest_has_units_hashes_and_versions` |
| dataset: min sessions not enforced | killed | `tests/test_dataset.py::test_too_few_sessions_is_refused_with_the_reason` |
| dataset: dt tolerance off | killed | `tests/test_dataset.py::test_dt_outside_tolerance_is_counted_not_silently_dropped` |
| dataset: y is after not delta | killed | `tests/test_dataset.py::test_good_file_converts_and_manifest_has_units_hashes_and_versions` |
| dataset: duplicates only among valid rows | killed | `tests/test_dataset.py::test_duplicate_keys_are_found_even_when_the_second_row_is_invalid` |
| dataset: range check skipped | killed | `tests/test_dataset.py::test_degrees_versus_radians_style_mistakes_hit_the_declared_range` |
| dataset: mixed definitions allowed | killed | `tests/test_dataset.py::test_blank_ids_and_definitions_are_errors` |
| calibration: negative slope accepted | killed | `tests/test_shaping_motions_calibration.py::test_calibration_rejects_bad_data` |
| calibration: offset dropped in pulse_to_deg | killed | `tests/test_shaping_motions_calibration.py::test_calibration_fit_and_version` |
| observe: angle not wrapped | killed | `tests/test_observe.py::test_angle_wraps_and_scales_correctly` |
| observe: no max-area filter | killed | `tests/test_stage_labels.py::test_a_green_area_bigger_than_max_area_is_not_a_marker` |
| observe: pivot radius check removed | killed | `tests/test_observe.py::test_marker_on_the_pivot_has_no_angle_and_is_marked_invalid` |
| observe: sign of angle flipped | killed | `tests/test_cli.py::test_summarize_session_reads_the_source_from_the_logs_and_never_overwrites` |
| observe: frame index off by one | killed | `tests/test_demo.py::test_summary_matches_the_checked_in_reference_within_tolerance` |
| camera config: hsv lower>upper allowed | killed | `tests/test_hardening.py::test_a_hue_range_may_wrap_through_zero_for_a_red_marker` |
| camera config: network source allowed | killed | `tests/test_observe.py::test_config_errors_name_the_problem[patch5-network]` |
| vd: range lower bound exclusive | killed | `tests/test_conformance.py::test_random_scripts_agree_with_firmware[1]` |
| vd: valid_for min exclusive | killed | `tests/test_conformance.py::test_random_scripts_agree_with_firmware[0]` |
| vd: lease boundary inclusive | killed | `tests/test_conformance.py::test_random_scripts_agree_with_firmware[2]` |
| vd: ARM grace 250 | killed | `tests/test_conformance.py::test_hand_written_script_matches_firmware_line_for_line` |
| vd: tick 20 | killed | `tests/test_conformance.py::test_hand_written_script_matches_firmware_line_for_line` |
| vd: heartbeat does not extend | killed | `tests/test_conformance.py::test_random_scripts_agree_with_firmware[46]` |
| vd: millis wrap mask removed | killed | `tests/test_conformance.py::test_the_32_bit_millisecond_clock_wraps_the_same_way_in_both` |
| vd: CLEAR allowed while armed | killed | `tests/test_conformance.py::test_random_scripts_agree_with_firmware[1]` |
| vd: stale epoch ARM accepted | killed | `tests/test_conformance.py::test_hand_written_script_matches_firmware_line_for_line` |
| vd: ESTOP does not latch | killed | `tests/test_conformance.py::test_random_scripts_agree_with_firmware[9]` |
| vd: HEARTBEAT epoch unchecked | killed | `tests/test_conformance.py::test_random_scripts_agree_with_firmware[1]` |
| fw: range lower bound exclusive | killed | `tests/test_conformance.py::test_random_scripts_agree_with_firmware[1]` |
| fw: lease boundary inclusive | killed | `tests/test_conformance.py::test_random_scripts_agree_with_firmware[2]` |
| fw: HEARTBEAT epoch unchecked | killed | `tests/test_conformance.py::test_random_scripts_agree_with_firmware[1]` |
| fw: ESTOP does not latch | killed | `tests/test_conformance.py::test_random_scripts_agree_with_firmware[9]` |
| fw: ARM with stale epoch accepted | killed | `tests/test_conformance.py::test_hand_written_script_matches_firmware_line_for_line` |
| fw: CLEAR allowed while armed | killed | `tests/test_conformance.py::test_random_scripts_agree_with_firmware[1]` |
| fw: seq_old check removed | killed | `tests/test_conformance.py::test_random_scripts_agree_with_firmware[7]` |
| fw: pulseToTicks floor not round | killed | `tests/test_conformance.py::test_random_scripts_agree_with_firmware[4]` |
| fw: i2c failure on write not latched | killed | `tests/test_conformance.py::test_an_i2c_write_that_fails_stops_and_latches_in_both` |
| fw: HELLO while armed does not stop | killed | `tests/test_conformance.py::test_random_scripts_agree_with_firmware[1]` |
| fw: 5us step -> 6us | killed | `tests/test_conformance.py::test_hand_written_script_matches_firmware_line_for_line` |
| touch: x range unchecked | killed | `tests/test_inputs.py::test_bad_events_are_rejected_not_clipped` |
| touch: body size unchecked | killed | `tests/test_inputs.py::test_bad_events_are_rejected_not_clipped` |
| touch: event cap off | killed | `tests/test_inputs.py::test_bad_events_are_rejected_not_clipped` |
| joycon: bool accepted | killed | `tests/test_stage_labels.py::test_a_joycon_boolean_is_not_a_number` |
| joycon: no clip | killed | `tests/test_inputs.py::test_joycon_source_reads_probe_lines_from_a_pipe_and_ignores_the_rest` |
| keyboard: no clamp | killed | `tests/test_more_edges.py::test_the_keyboard_axis_stops_at_plus_and_minus_one` |
| scripted: clip removed | killed | `tests/test_inputs.py::test_scripted_source_emits_events_on_schedule_with_tremor_and_clipping` |
| recorder: logs commanded as desired | killed | `tests/test_stage_labels.py::test_a_dry_recording_keeps_the_wish_and_the_shaped_command_apart` |
| recorder: input events valid always | killed | `tests/test_stage_labels.py::test_an_input_sample_without_a_value_is_logged_invalid_with_null` |
| motion_from_trial: uses sent over scheduled | killed | `tests/test_demo.py::test_summary_matches_the_checked_in_reference_within_tolerance` |
| trialio: DRY_RUN source labelled synthetic for human | killed | `tests/test_record_play.py::test_mode_resolution` |
| trialio: hardware link none | killed | `tests/test_record_play.py::test_mode_resolution` |
| link: unplug keeps in-flight | killed | `tests/test_demo.py::test_summary_matches_the_checked_in_reference_within_tolerance` |
| link: SimWorld write while unplugged ok | killed | `tests/test_cli.py::test_summarize_session_reads_the_source_from_the_logs_and_never_overwrites` |
| link: serial open failure not wrapped | killed | `tests/test_link_pty.py::test_unplugged_port_is_a_clean_error_not_a_traceback` |
| cli: --resume ignored | killed | `tests/test_stage_labels.py::test_the_cli_passes_resume_and_allow_clear_to_the_player` |
| cli: hardware needs only port | killed | `tests/test_cli.py::test_hardware_target_refuses_without_the_flags` |
| cli: hardware without tty allowed | killed | `tests/test_cli.py::test_hardware_target_with_flags_still_needs_a_terminal` |
| cli: compare-reference exit 0 on diff | killed | `tests/test_demo.py::test_compare_reference_cli_reports_the_level_and_can_require_identical` |
| cli: validate exit 0 on failure | killed | `tests/test_cli.py::test_validate_flags_a_broken_trial` |
| cli: default target loopback | killed | `tests/test_cli.py::test_default_target_is_dry_and_nothing_is_sent` |
| probe: pulse range widened | killed | `tests/test_link_pty.py::test_bounded_probe_dry_run_opens_nothing` |
| probe: duration cap widened | killed | `tests/test_link_pty.py::test_bounded_probe_dry_run_opens_nothing` |
| probe: executes without flag | killed | `tests/test_link_pty.py::test_bounded_probe_dry_run_opens_nothing` |
| log: ACCEPTED row reports target as applied | killed | `tests/test_stage_labels.py::test_an_acceptance_is_not_logged_as_an_applied_value` |
| to_deg: applied_deg built from commanded | killed | `tests/test_demo.py::test_summary_answers_the_question_with_numbers_for_all_three_motions` |
| report: replay-vs-recording applied uses commanded | killed | `tests/test_demo.py::test_summary_matches_the_checked_in_reference_within_tolerance` |
| report: lag applied->observed uses commanded | killed | `tests/test_demo.py::test_summary_answers_the_question_with_numbers_for_all_three_motions` |
| report: repeat SD of applied uses observed | killed | `tests/test_demo.py::test_summary_matches_the_checked_in_reference_within_tolerance` |
| report: c_vs_a sign/series swapped to observed | killed | `tests/test_demo.py::test_summary_matches_the_checked_in_reference_within_tolerance` |
| report: return error window moved | killed | `tests/test_more_edges.py::test_the_return_error_compares_the_end_of_the_run_with_its_start` |
| dataset extract: dt unit slip (s*100) | killed | `tests/test_dataset.py::test_extract_pairs_commands_with_observations_from_trial_logs` |
| dataset extract: before taken at after time | killed | `tests/test_dataset.py::test_extract_pairs_commands_with_observations_from_trial_logs` |
| extract: observed time for live uses sent not received | killed | `tests/test_stage_labels.py::test_live_observation_times_come_from_the_receive_time_and_invalid_frames_are_left_out` |
| extract: media offset ignored | killed | `tests/test_analysis.py::test_media_offset_moves_the_video_on_the_command_timeline` |
| timing: early_count sign | killed | `tests/test_demo.py::test_summary_matches_the_checked_in_reference_within_tolerance` |
| recorder: record_end reports ok when interrupted | killed | `tests/test_stage_labels.py::test_a_stop_from_the_device_ends_a_recording_and_the_log_says_so` |
| recorder: stop on STOP ignored | killed | `tests/test_stage_labels.py::test_a_stop_from_the_device_ends_a_recording_and_the_log_says_so` |
| player: stop on STOP ignored | killed | `tests/test_stage_labels.py::test_a_stop_from_the_device_ends_a_replay` |
| play: disarm at end skipped | killed | `tests/test_demo.py::test_summary_matches_the_checked_in_reference_within_tolerance` |
| record: disarm at end skipped | killed | `tests/test_stage_labels.py::test_recording_disarms_the_device_when_it_ends` |
| link: PTY non-ascii decode strict | killed | `tests/test_more_edges.py::test_a_non_ascii_byte_on_the_wire_ruins_that_line_only` |
| serial: reset_input_buffer removed | killed | `tests/test_more_edges.py::test_opening_a_port_drops_whatever_the_board_said_while_it_reset` |
| serial: LinkDown not raised on read error | killed | `tests/test_more_edges.py::test_a_vanished_port_is_a_link_down_on_read` |
| cli: play ignores --allow-clear | killed | `tests/test_stage_labels.py::test_the_cli_passes_resume_and_allow_clear_to_the_player` |
| cli: record target hardware not mapped | killed | `tests/test_robustness.py::test_ctrl_c_ends_a_touch_recording_and_keeps_the_log` |
| console: ARM allowed always | killed | `tests/test_link_pty.py::test_console_blocks_arm_without_the_flag_and_logs_both_directions` |
| console: lowercase arm bypass (startswith case) | killed | `tests/test_stage_labels.py::test_the_console_blocks_arm_in_any_letter_case_unless_allowed` |
| protocol: MAX_LINE 200 | killed | `tests/test_link_pty.py::test_protocol_line_checks` |
| probe: loopback flag ignored for ports | killed | `tests/test_link_pty.py::test_bounded_probe_dry_run_opens_nothing` |
| calibration: fit ignores center | killed | `tests/test_analysis.py::test_to_deg_needs_a_calibration_and_never_mixes_units` |
| observe: valid when no_calibration -> invalid | killed | `tests/test_cli.py::test_summarize_session_reads_the_source_from_the_logs_and_never_overwrites` |
| observe: file-backed received time as capture | killed | `tests/test_observe.py::test_file_backend_uses_media_position_and_never_invents_capture_time` |
| new: Ctrl+C is not caught in record | killed | `tests/test_robustness.py::test_ctrl_c_ends_a_touch_recording_and_keeps_the_log` |
| new: read-side LinkDown not caught in the player | killed | `tests/test_robustness.py::test_pulled_cable_that_makes_reads_fail_ends_the_run_as_interrupted` |
| new: unsent row keeps a sequence number | killed | `tests/test_hardening.py::test_a_dropped_late_row_has_no_sequence_number_and_sent_seqs_are_unique` |
| new: lag at the window edge reported as a value | killed | `tests/test_hardening.py::test_lag_beyond_the_search_window_is_not_reported_as_the_edge_value` |
| new: escape sequences are not dropped whole | killed | `tests/test_robustness.py::test_only_plain_arrows_and_letters_move_the_axis[\x1b[A-expected0]` |
| new: non-finite numbers in a read file pass | killed | `tests/test_triallog.py::test_a_number_that_parses_to_infinity_is_flagged_anywhere` |
| new: scheduled times may go backwards | killed | `tests/test_triallog.py::test_set_rows_whose_scheduled_or_sent_time_goes_backwards_are_flagged` |
| new: header stream vs file name unchecked | killed | `tests/test_triallog.py::test_a_command_stream_copied_under_the_input_name_is_refused` |
| new: git commit reported for a foreign checkout | killed | `tests/test_hardening.py::test_manifest_reports_the_commit_only_for_its_own_checkout` |
| new: plan length limit removed | killed | `tests/test_hardening.py::test_a_plan_longer_than_the_limit_is_refused_unless_asked_for` |
| new: read buffer cap removed | killed | `tests/test_hardening.py::test_serial_noise_without_line_ends_does_not_grow_the_buffer_forever` |
| new: 413 body is not drained | killed | `tests/test_robustness.py::test_touchpad_survives_overflowing_numbers_and_oversized_bodies` |
| new: touch ids may repeat inside a batch | killed | `tests/test_robustness.py::test_touchpad_survives_overflowing_numbers_and_oversized_bodies` |
| new: hue cannot wrap | killed | `tests/test_hardening.py::test_a_hue_range_may_wrap_through_zero_for_a_red_marker` |
| new: the identical level uses the loose tolerance | killed | `tests/test_demo.py::test_only_the_video_derived_numbers_get_the_cross_build_slack` |
| new: video-derived expected lag loosened | killed | `tests/test_demo.py::test_only_the_video_derived_numbers_get_the_cross_build_slack` |
| new: still-marker spread uses ddof 0 | killed | `tests/test_more_edges.py::test_the_still_marker_spread_uses_n_minus_one` |
| new: leave-one-out includes the replay itself | killed | `tests/test_demo.py::test_summary_matches_the_checked_in_reference_within_tolerance` |
| new: bridged share counts every grid point | killed | `tests/test_demo.py::test_summary_matches_the_checked_in_reference_within_tolerance` |
| new: after-500ms window starts at 0 | killed | `tests/test_demo.py::test_summary_matches_the_checked_in_reference_within_tolerance` |
| new: summary --repeats 1 allowed | killed | `tests/test_more_edges.py::test_the_demo_wants_at_least_two_replays` |

## Judged equivalent

| mutant | result | first failing test |
|---|---|---|
| fw: ARM resets the sequence counter | SURVIVED | |
| fw: dropping the `if (discarding_) return;` line | SURVIVED | |
| writer: part file left over ignored | SURVIVED | |
| extract: invalid frames counted as valid | SURVIVED | |
| protocol: check_line allows non-ascii | SURVIVED | |
| extract: EXECUTED filter accepts ACCEPTED | SURVIVED | |
| play: abort on refusal publishes anyway | SURVIVED | |
| calibration: 2 points accepted | SURVIVED | |

## Why the equivalent ones are equivalent

- **fw: ARM resets the sequence counter**: Unreachable difference: every stop clears the epoch, so a new ARM always follows a HELLO, and HELLO already resets the counter.
- **fw: dropping the `if (discarding_) return;` line**: Equivalent: while `discarding_` is set the newline branch discards the line anyway, and the device is already disarmed after the first stop.
- **writer: part file left over ignored**: The stream is opened with mode 'x', so an existing .part file raises FileExistsError anyway; the explicit check only gives a clearer message.
- **extract: invalid frames counted as valid**: An invalid observation has observed_value null (the log validator insists), and null values are skipped on the next line.
- **protocol: check_line allows non-ascii**: The range check on the same line (printable ASCII 32..126) already refuses every non-ASCII character.
- **extract: EXECUTED filter accepts ACCEPTED**: An ACCEPTED_NOT_EXECUTED reply carries no applied value (it is null in the log), and rows without one are dropped on the next line.
- **play: abort on refusal publishes anyway**: The trial context manager aborts the log on any exception, so the explicit abort() in play() does the same thing twice.
- **calibration: 2 points accepted**: The next check (at least 3 distinct pulse values) refuses two points as well, with the same "at least 3" wording, so the first check only gives an earlier message.

Set B had no survivors. The judged-equivalent mutants survive on purpose (see above); a survivor anywhere else would mean the tests do not constrain that behaviour.
