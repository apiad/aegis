---
title: "aegis config + ConfigPanel v1 — TDD plan"
spec: 2026-05-27-aegis-config-surface-design.md
date: 2026-05-27
status: armed
---

# Plan

Eight slices, each TDD. Build vertically end-to-end on each — never
half a layer. Commit per slice with conventional-commits messages.

## Slice 1: edit.py mutators — agents

**Tests:** `tests/test_config_edit.py` (extend existing)

- `test_add_agent_creates_missing_file`
- `test_add_agent_preserves_comments_in_existing_file`
- `test_add_agent_rejects_duplicate_slug`
- `test_add_agent_rejects_bad_permission`
- `test_add_agent_claude_with_effort`
- `test_add_agent_gemini_without_effort`
- `test_remove_agent_drops_the_entry`
- `test_remove_agent_unknown_slug_fails`
- `test_remove_agent_referenced_by_queue_fails`
- `test_remove_agent_referenced_by_default_agent_fails`

**Implementation:** `add_agent`, `remove_agent` in
`src/aegis/config/edit.py`. Each:
1. Read .aegis.yaml via ruamel; create with `default_agent: <slug>`
   header comment if missing.
2. Mutate.
3. Round-trip through yaml_loader.load_config on the prospective
   body before writing — raise ConfigError if invalid.
4. Atomic write.

## Slice 2: edit.py mutators — queues

**Tests:**
- `test_add_queue_with_basic_fields`
- `test_add_queue_with_budgets`
- `test_add_queue_rejects_unknown_agent`
- `test_add_queue_rejects_zero_max_parallel`
- `test_add_queue_rejects_duplicate_name`
- `test_remove_queue_drops_entry`
- `test_remove_queue_unknown_name_fails`

**Implementation:** `add_queue(root, name, agent, max_parallel,
budgets=None)` + `remove_queue(root, name)`.

## Slice 3: edit.py mutators — telegram + default-agent + plugin-dirs

**Tests:**
- `test_set_telegram_token_only`
- `test_set_telegram_clear_chat_id`
- `test_set_telegram_creates_block_in_minimal_file`
- `test_set_default_agent_changes_value`
- `test_set_default_agent_rejects_unknown`
- `test_add_plugin_dir_appends`
- `test_remove_plugin_dir_drops_path`
- `test_add_plugin_dir_idempotent`

**Implementation:** `set_telegram` (with `Sentinel` marker for
"unchanged"), `set_default_agent`, `add_plugin_dir`,
`remove_plugin_dir`.

## Slice 4: cli_config.py — show + agent subcommands

**Tests:** `tests/test_cli_config.py` (new)
- `test_show_minimal_yaml`
- `test_show_empty_directory_exits_nonzero_hints_at_agent_add`
- `test_agent_list_table`
- `test_agent_add_writes_file_when_missing`
- `test_agent_add_validates_provider`
- `test_agent_remove_drops_entry`
- `test_agent_remove_referenced_by_default_agent_fails_loud`

**Implementation:** `src/aegis/cli_config.py` — Typer subapp.
`aegis.cli` wires it as `app.add_typer(config_app, name="config")`.
Subcommands delegate to the slice 1 mutators.

## Slice 5: cli_config.py — queue + telegram + default-agent + plugin-dir

**Tests:**
- `test_queue_add_with_budgets`
- `test_queue_list_table`
- `test_queue_remove`
- `test_telegram_set_token`
- `test_telegram_show_redacts_token`
- `test_telegram_set_clear_chat_id`
- `test_default_agent_change`
- `test_plugin_dir_add_list_remove`

**Implementation:** the remaining Typer subcommands. Budget specs
parse `"<constraint>:<limit>:<window>"` strings; repeatable `--budget`
flag.

## Slice 6: ConfigPanel — read-only render

**Tests:** `tests/test_config_panel.py` (new)
- `test_config_panel_mounts`
- `test_config_panel_renders_agents_table`
- `test_config_panel_renders_queues_table`
- `test_config_panel_renders_telegram_section`
- `test_config_panel_empty_state_shows_hint`

**Implementation:** `src/aegis/tui/config_panel.py`. A new Widget
subclass that pulls current state via `yaml_loader.load_config` and
renders four sections (agents, queues, telegram, plugin_dirs) as
Rich tables. Mount-on-demand via `Ctrl+,` in `app.py`.

Pure read at this slice. No buttons wired yet.

## Slice 7: ConfigPanel — add/remove modals (interactive)

**Tests:**
- `test_add_agent_modal_validates_required_fields`
- `test_add_agent_modal_writes_through_edit_helpers`
- `test_remove_agent_button_writes_through_edit_helpers`
- `test_add_queue_modal_validates_unknown_agent`
- `test_panel_refreshes_after_edit`

**Implementation:** AddAgentModal, AddQueueModal, EditTelegramModal,
delete handlers for table rows. On save → call the slice 1-3
mutators → re-read file → refresh tables.

## Slice 8: boot-into-panel + watchdog reload

**Tests:**
- `test_boot_no_yaml_mounts_config_panel`
- `test_boot_with_yaml_does_not_mount_config_panel`
- `test_watchdog_reload_picks_up_new_agent`
- `test_running_session_keeps_its_profile_after_reload`

**Implementation:**
- `app.py`: when `find_project_root()` returns None (no .aegis.yaml),
  bootstrap the app with empty agents and a single ConfigPanel tab,
  skip the normal session-spawn flow.
- `aegis serve` + the TUI install an `on_config_change` callback on
  the existing `ReloadWatcher` that swaps SessionManager.agents +
  QueueManager.queues + telegram config in place.

## Slice 9: retire `aegis init` + doc sweep

- Remove `aegis.cli.init` command. Keep `init_wizard` provider
  catalog + `_next_slug` helper for `aegis config agent add`.
- Rewrite `docs/install.md`, `docs/configuration.md`,
  `docs/usage.md`, AGENTS.md to point at `aegis config`.
- Delete `tests/test_cli.py::test_init_*` and
  `tests/test_init_wizard.py` (or rewrite to test the catalog
  helpers only).

## Out-of-band

Each slice is its own commit (or 2-3 if a slice produces a clean
intermediate). Push after each. The 971-test green baseline must
hold throughout.
