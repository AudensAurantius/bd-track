# bd-timew

A bridge between [Beads](https://github.com/steveyegge/beads) (issue tracker) and [Timewarrior](https://timewarrior.net/) (time tracker), with project lifecycle management and scoped execution queues.

`bd-timew` resolves a Beads issue's labels to a billing tuple `(client, case, svc)` via a per-project sidecar (`.beads/bd-timew.yaml`), then starts or stops a Timewarrior interval tagged accordingly. It also provides project maintenance commands (`init-project`, `cleanup`), Dolt server lifecycle commands, and named bead-execution queues.

## Status

Alpha. The tool was extracted from a personal time-tracking workflow and may have rough edges for general use. The `(client, case, svc)` tuple maps to whatever vocabulary your billing system uses; configure it per-project in `.beads/bd-timew.yaml`.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/AudensAurantius/bd-timew/main/install.sh | sh
```

The installer pulls system dependencies (`timew`, `pipx`) via your platform's package manager, then installs `bd-timew` from a pinned tag via `pipx`.

Manual install (if you already have `timew`, `bd`, and `pipx`):

```bash
pipx install git+https://github.com/AudensAurantius/bd-timew.git
```

## Quick start

```bash
# Scaffold a per-project sidecar
cd ~/Source/your-project
bd-timew config init

# Start tracking a bead
bd-timew start <bead-id>

# Check what's tracking
bd-timew status

# Stop
bd-timew stop <bead-id>

# Queue beads for later execution
bd-timew push <bead-id> [<bead-id>...]
bd-timew queue --titles
bd-timew start "$(bd-timew pop)"
```

## Subcommand reference

| Command | Purpose |
|---|---|
| `start <id>` | Claim a bead and start a tagged Timewarrior interval |
| `stop [<id>]` | Stop the active interval |
| `switch <id>` | Stop current and start new |
| `status` | Show active interval, bead, billing tuple, elapsed time |
| `resolve <id>` | Print resolved billing tuple without starting |
| `push <id...>` | Append beads to a queue scope |
| `unshift <id>` | Prepend a bead to a queue scope |
| `pop` | Remove and print head of a queue scope |
| `peek` | Print head of a queue scope without removing |
| `queue` | List queue contents (all scopes, or one with `--scope`) |
| `remove <id...>` | Remove beads from a queue scope |
| `clear` | Empty a queue scope (`--scope all` to clear every scope) |
| `cleanup` | Run Beads/Dolt maintenance (commit, compact, GC) |
| `init-project` | Configure a Beads project for billing tuple resolution and registered cleanup |
| `config init` | Scaffold a per-project sidecar with annotated defaults |
| `servers` | List registered repos and their Dolt server status |
| `server-stop [--path]` | Stop Dolt servers for one or all registered repos |
| `idle-stop --hours N` | Stop Dolt servers idle longer than threshold |

All queue subcommands accept `--scope <name>` (or `$BD_TIMEW_SCOPE`) and `--titles`/`-t` to fetch and display bead titles inline.

## Per-project sidecar (`.beads/bd-timew.yaml`)

Maps Beads issue labels to a billing tuple. Run `bd-timew config init` to scaffold an annotated template:

```yaml
default:
  client: ""        # default client when no pattern matches
  case: ""          # default case
  svc: ""           # default service category

patterns:
  - match: "area:billable"
    client: "AcmeCo"
    case: "Sprint Q2"
    svc: "Engineering"
  - match: "area:internal"
    client: "internal"
    svc: "(none)"

# Per-issue override: a bead with `case:special-case` label uses
# that value for `case` regardless of pattern matches.
```

## Platform support

- **Linux**: full support (systemd timers for cleanup and idle-stop).
- **WSL2**: full support (treated as Linux).
- **macOS**: not yet — see issue tracker.
- **Windows native**: not yet — see issue tracker.

## License

MIT
