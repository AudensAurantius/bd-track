# Session checkpoint — CLI cutover + bd-track rename — 2026-06-03

Resume context after **`bd-timew-9hn`** (the JSONL CLI cutover + `bd-timew → bd-track`
rename) landed as **0.5.0**. Supersedes `checkpoint-2026-06-02-jsonl-rewrite.md`
for "where we left off"; that file still holds the pre-cutover design rationale.

## What landed (0.5.0)

The timew backend is **gone**. Commands re-derive state by folding the per-session
JSONL logs (`events.py` + `aggregate.py`) — no shared active interval, so the
originating concurrency bug (`bd-timew-nfr`) is structurally impossible.

Commit layers (all on `main`):
1. `feat(cli)` — re-wire start/stop/switch/status onto JSONL; add `active` + `report`.
2. `refactor(rename)!` — `bd_timew`→`bd_track` module, `timew.py`→`track.py`, package,
   exe, `BD_TRACK_*` env, `.beads/bd-track.yaml`, dirs, templates, install.sh.
3. `feat(compat)` — old-name read-fallbacks (`env_compat`/`path_compat` in `util.py`).
4. `chore(release)` — 0.5.0 + CHANGELOG.

Key invariants (tested in `tests/test_track.py`, `tests/test_compat.py`):
- **single-active-per-session**: `start` ends the caller's own open interval first,
  never another session's. `stop` (no arg) can only close the caller's own ULIDs.
- **`active`** = all open intervals across ALL sessions (the multi-active view).
- **`report`** = `--by <dim>` / `--policy billing|machine|wallclock` / `--since/--until`.
- **rename preserved the bd issue prefix** `bd-timew-` (a negative-lookahead in the
  rename pass); only the *tool* renamed. GitHub repo URLs still point at the
  (un-renamed) `bd-timew` remote — see "Open decisions".
- **deprecated `bd-timew` alias** (`cli.main_deprecated`) warns to stderr then
  dispatches, so existing wrappers/.envrc keep working until migration.

## Next — `refactor` queue: `73v`, `jy9`, `4g4`

- **`bd-timew-jy9`** (P1, in-repo) — `bd-track migrate rename` subcommand: rename
  sidecar, migrate `~/.config|cache|state/bd-timew` + `<beads>/bd-timew/sessions`,
  rewrite `BD_TIMEW_*`→`BD_TRACK_*` in `.envrc`/`.env`/`.envrc.local`/`mise.toml`.
  **Dry-run by default**; **skip+warn on chezmoi-managed files** (don't desync source).
  Leaves the `migrate` namespace open for 73v.
- **`bd-timew-4g4`** (P2, **chezmoi repo**) — rename `bd-timew`→`bd-track` + fix stale
  "Timewarrior" wording across `/start /stop /status /switch /work-queue /time-report`
  + the time-tracking/work-queue/auto-session/beads-migration/python-scripting skills;
  update wrappers for per-session model (DELETE the obsolete "no-arg stop halts ALL
  sessions" danger notes — that bug is fixed); add a dated migration PSA to global
  CLAUDE.md. PSA decision: **alias stderr-warning is primary; no blocking hook**
  during transition (would break wrappers still emitting bd-timew). Edit chezmoi
  SOURCE then `chezmoi apply` — never `cp`.
- **`bd-timew-73v`** (after jy9) — import existing timew export → JSONL.

## Open decisions for the operator

- **GitHub repo rename?** Package/exe are `bd-track` but the repo is still
  `AudensAurantius/bd-timew`; pyproject URLs + install.sh point at the real remote.
  Rename the repo (then update URLs) or leave as-is — operator's call.
- **Cosmetic**: `start`/`stop` log `interval[:8]`, which collides across ULIDs minted
  seconds apart (shared timestamp prefix). `active` disambiguates by session+bead, so
  low priority; file a polish bead if it annoys.

## Working notes

- Build/test: `uv run pytest -q` (136 passing), `uv run ruff check src/ tests/` (clean).
- Dev exe: `uv run bd-track …`. The installed `~/.local/bin/bd-timew` (pipx) is still
  the OLD timew-backed 0.4.x until the operator re-installs from this repo.
- Code: `src/bd_track/{track,events,aggregate,session,billing,util,queue,...}.py`.
