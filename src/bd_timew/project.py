"""Project lifecycle: init-project, cleanup, run-service.

Configures Beads projects with recommended settings, registers them for
automated cleanup via systemd timers, and provisions optional Dolt
server-mode wiring (.envrc + pass credential).
"""

# Section: cmd_init_project
# Section: cmd_cleanup
# Section: cmd_run_service (systemd cleanup entrypoint)
# Section: repos config helpers (_load_repos_config, _save_repos_config, _get_repo_entry)
# Section: cleanup state helpers (_load_cleanup_state, _save_cleanup_state)
# Section: cadence parsing (_parse_cadence, _INTERVAL_ALIASES)
# Section: server-mode helpers (_pass_entry_exists, _provision_pass_entry, _update_envrc, _provision_dolt_user)
# Section: systemd unit installation (uses templates/systemd/*.tmpl)
