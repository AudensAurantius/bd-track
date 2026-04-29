"""Scoped bead-execution queue subcommands.

Maintains an ordered list of bead IDs per scope in .beads/queue.yaml.
Scope resolution: --scope flag → $BD_TIMEW_SCOPE env var → 'default'.
"""

# Section: _fetch_title
# Section: _resolve_scope
# Section: _load_all_queues / _save_all_queues
# Section: cmd_queue (dispatches push, unshift, pop, peek, list, remove, clear)
