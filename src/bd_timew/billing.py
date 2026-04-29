"""Billing tuple resolution.

Reads the per-project sidecar (.beads/bd-timew.yaml), fetches the active
issue from `bd`, and resolves a (client, case, svc) tuple via:

  1. Per-issue `case:<string>` label (escape hatch)
  2. First matching `patterns:` rule (regex on space-joined label list)
  3. `default:` block in the sidecar
  4. None for any unset field — the caller decides how to display
"""

# Section: load_sidecar
# Section: get_issue
# Section: resolve_tuple
