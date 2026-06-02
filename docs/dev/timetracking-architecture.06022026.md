# Architecture: Session lifecycle management and issue claims

## Problem statement

The existing system of timetracking implemented by `bd-timew` is not workable due to timewarrior's fundamental limitation of one-open-interval-at-a-time.  However, extending the model to support *multiple concurrent intervals* immediately raises several related architectural problems:
- How do we deal with merge conflicts and avoid concurrent-write issues?
- How do we balance **project-locality** (i.e., storing timetracking information specific to project X alongside beads and `bd-timew` data associated with project X) with optimizing support for **cross-project timetracking reporting**?
- How do we deal with "orphaned" timetracking intervals resulting from operator negligence, session crashes, and other anomalies?

In a previous turn, Claude suggested that we need to define **two additional primitives:**
1. **Session claims:** answers the question "what is/are the ID(s) of the session(s) with active intervals associated with bead X?"
2. **Session lifecycles:** answers the question "is the session with ID 'X' still alive?"

Following is my analysis of some possibilities for implementing these two primitives.  

## Preliminaries: Notes for Claude

- **Character escaping:** I will use backslash-escaping for syntactically significant tokens in this document, since I'm editing it in Neovim.  In particular, dollar signs are escaped as \$, and non-link-enclosing brackets are escaped as \[ ... \].
- **Conceptual approach:** Each of the two main primitives' sections records the *evolution of a chain of thought* rather than a static list of conclusions.  Accordingly, each contains various intermediate ideas that are considered but ultimately rejected.  Carefully consider not only the final proposals in each section, but also the reasoning that led me to reject each of these intermediate ideas.

## Primitive #1: session claims

Primitive #1, **session claims**, represents a way of *associating sessions to open intervals* in order to disambiguate situations in which multiple sessions possess open intervals against the same issue.

Session claims could be implemented in several ways, analyzed below as Approaches (A) - (D).

### Approach (A), beads-native: use --actor to track claims

The most "beads-native" way of implementing session claims is to use the `bd` CLI's global `--actor` parameter to ensure that on every beads operation, the "actor" attribute in the beads event log indicates which session (or human) last operated on the bead.  Claims on the bead then become resolvable by using beads (or the underlying Dolt DB) to obtain a list of actors that have operated on the bead; those with currently-active intervals against the bead are identified as those with active claims on it.

However, **this system is brittle** due to the strong couplings between the `bd` CLI, the beads / Dolt schema, and operators' usage of the `bd-timew` CLI: it assumes that every `bd` call uses `--actor` to mark the operation with the human or session operator's identity. As far as I can tell, there are no good enforcement mechanisms to ensure that this is the case.

### Approach (B): flat claims registry

Alternatively, we could maintain a flat, structured "claims registry" implementing a bead_id --> session_id lookup.  For instance, a single JSON or YAML file under .beads could consist of lines like "<bead_id>: <session_id>" indicating current claims; once a session releases its claim on a bead, the corresponding claim line would be removed from the registry.

However, this has the same concurrent-write / merge issues that led us to choose append-only logging for timetracking events in the first place.

### Approach (C): append-only claims registry
  
Building on the above, we could implement the claim registry as another JSONL-based, append-only event log alongside the timetracking event log.

The problem with this is that the timetracking event log *already* contains strictly more information than the claim registry, so an independent append-only claim registry is only valuable if there are significantly more types of timetracking events than there are claim-registry events.

For instance, if `bd-timew` exposes timetracking events "start", "stop", "pause", "resume", "cancel", and "edit", where the JSONL line for each event type carries the ID of the session (or operator) from which it originates, then each event implicitly carries a claim through that ID.  However, assessing from the event log whether a session still holds an *active* claim on a given issue depends on the claim-semantics of the timetracking event types, and if the workstream is "noisy" (i.e., many intermediate tracking events are issued between when a session opens a claim on an issue and when it releases it), then the claim registry could be significantly shorter and easier to traverse than the event log, making it a performance win.

### Approach (D): claims via lockfiles

Finally, after considering and rejecting the above possibilities, I'd suggest the following lockfile-based mechanism for implementing "claims" while avoiding (a) the creation of another, largely duplicative JSONL append-only log and (b) the merge and concurrency issues associated with a static, flat-file structured claims registry:

**Create a directory of "claimfiles" under .beads/ for each issue, containing files named after the session IDs of the claimant session(s) and/or operators.**

For example, if bead X currently has open claims from sessions A and B, then the claimfiles for bead X would be .beads/claims/X/A and .beads/claims/X/B.

#### Implications of Approach (D)

**Timetracking semantics:**
Each timetracking event type (open, close, edit, cancel, pause, resume, etc.) would be limited to three types of "claim semantics": 
  (a) create a claim file for the issuing session on the bead;
  (b) remove an existing claim file for the issuing session on the bead; and
  (c) check for the existence of a claim file for the issuing session on the bead and write an event log line depending on the outcome 

For example, "pause" semantics could be "check for the claim file and write a pause line if it exists").

**Persistence strategy:**
- The claim directory, .beads/claims, should be gitignored or added to .git/info/exclude (machine-specific state).
- Since file creation and removal are atomic (POSIX), there are no merge or reconciliation issues with this scheme.
- Furthermore, since .beads/claims is ignored by git, its state reflects the claims state across all branches.
  - This could be either an asset (since active work is tracked uniformly across all branches) or a liability.  For instance, what about sessions operating in worktrees?  Since .beads/claims is gitignored, they would have to have some pointer back to the parent repo's claims directory.
  - I'm unsure about the best way to resolve this tension, but would suggest putting the claims directory under a central location, like ~/.config/bd-timew/<project-ID>/claims or ~/.local/state/bd-timew/<project-ID>/claims, as a possible alternative to storing claims files under the project .beads/ directory.
  - Another, more architecturally adventurous possibility: **use a specialized git ref namespace**, .e.g. .git/refs/claims, to manage claims, similarly to how git-bug uses a bugtracking refs namespace to manage issue tracking.  I'm not really sure what the implementation effort for this would be, but it seems like an interesting possibility.

## Primitive #2: session lifecycle

The alternatives I considered for this primitive are similar to those I considered for the session claim primitive.  Fast-forwarding to my preferred approach ("Approach D" in the "Primitive #1" section): Session lifecycles could be managed using a collection of sentinel files alongside the claims directory, one for each active session.  Resolving the question of whether a session is "alive" would then consist of simply checking for the relevant sentinel file.

### Sentinel file location

Recall that for the claims directory's location, I considered a number of possibilities:
- .beads/claims 
- ~/.local/config/bd-timew/<project-ID>/claims
- ~/.local/state/bd-timew/<project-ID>/claims 
- a special git ref namespace within the project repository

For brevity, pending resolution of this location question, I'll refer to the claims directory as \$CLAIMS_DIR .  The obvious proposal for the location of the session-sentinel directory would then be \$CLAIMS_DIR/../sessions.  Alternatively, we could consider *another* special git-ref namespace, .git/refs/sessions, to decouple session lifecycle management from claims.  In order to remain agnostic on this question, I'll refer to the session sentinel directory as \$SENTINEL_DIR.

### Fundamental issues

The session-sentinel proposal has a few fundamental issues to resolve before adopting it:
- **Robustness:** Session sentinels are not robust against session crashes, one of the key motivating scenarios behind the session lifecycle primitive.
- **Operator sessions vs. Claude sessions:** Operator-driven or manual "sessions" have different failure modes and robustness requirements than Claude sessions.  This is important because `bd-timew` aims to offer *timetracking functionality*, which is fundamentally agent-agnostic--fully-manual work, operator-driven Claude sessions, and fully-autonomous Claude sessions should all be trackable.

### Layer 1: `bd-timew` CLI

Since the `bd-timew` CLI itself should be agent-agnostic with respect to timetracking and session lifecycle management, the CLI commands should expose session operations that can be used by either human or AI operators.  For example: `bd-timew session start --session-id <session-id> --type claude` could be the rough shape of a command to create a session sentinel file, and `bd-timew session end --session-id <session-id>` could be a corresponding command to remove a session sentinel file.

**Cost of this approach:** we would need to add a global `--session-id` parameter to the `bd-timew` CLI (perhaps not *entirely* global--e.g., queue-based operations could be considered session-independent).

**Open questions:**
- Should this `--session-id` parameter be *mandatory*, to force Claude sessions to specify it on every `bd-timew` invocation, or should it support auto-generating session IDs for the convenience of human operators?
- Should we add companion commands like "session validation", "sesasion cleanup", "list open sessions", "list open claims per session"?
  - Related: should we build in the ability to carry out *historical audits* by summarizing which sessions had claims against which beads at past moments in time?
- What should the behavior be on the following edge cases?
  - "session start" command issued with a `--session-id` parameter whose sentinel file *already exists*
  - "session end" command issued with a `--session-id` parameter whose sentinel file *doesn't currently exist*

### Claude session lifecycle management: per-project hooks

In order to make sentinel files for Claude sessions (operator-driven or fully autonomous) (a) responsive to normal Claude session events and (b) partially robust against abnormal session termination, I'd propose that **session sentinel files should be managed through Claude hooks,** defined locally in each project with `bd-timew` integration.  `bd-timew` could then optionally install these hooks (or a subset of them) as part of the existing `bd-timew init-project` functionality.

The most basic examples of such hooks would be `SessionStart` and `SessionEnd` hooks responsible for creating and removing session sentinel files by calling `bd-timew session start` / `bd-timew session end` with the appropriate session IDs.  **Open question:** Do `SessionEnd` Claude hooks fire on *anomalous* session termination (i.e., crashes)?  If not, could we use some kind of long-running process-watcher / `ps` poller to monitor the state of a session, ensuring that when its parent process terminates, the corresponding sentinel file is removed?  If so, what's the best way of integrating this functionality into the `bd-timew` CLI itself?

### Operator session lifecycle management: session caps

Unlike Claude sessions, "operator sessions" (i.e., manual human working sessions) don't need to deal with "crash" failure modes.  However, they do need to deal with the following *human* failure modes:
- **Humans forget to close sessions.**  This is the single biggest footgun associated with *any* timetracking software.
- **Humans have natural attention rhythms that effectively "cap" session length.**  Unlike AI agents, humans can only maintain continuous working context for so long before breaking, effectively ending the "session".

These two failure modes unique to humans suggest the following strategy: **Impose a configurable cap on human session length**.  Sentinel files corresponding to human sessions with durations longer than the cap, or alternatively with *periods of inactivity* longer than the cap (determined by examining the timetracking JSONL event logs), are considered invalid.

#### Session cap: justification and semantics

Imposing a cap on human-driven *session length* is strictly superior to imposing a cap directly on *timetracking intervals* recorded during human sessions:
- Session caps have the same benefit of limiting "interval overflow" due to human negligence.  If a human forgets to close an interval, the extra time logged to it is bounded by *the duration remaining on the session clock*.
- On the other hand, session caps are more flexible than direct interval caps: they allow the possibility of genuinely long task intervals (realistic given human variations in working-session duration), as long as these respect the overall session boundary.
  - This benefit is even more pronounced if the "session cap" is only sensitive to *inactive session time*, i.e., if the "session clock" resets on every timetracking event.
  - On the other hand, even if the "session clock" is *absolute*, the negative effects of artificially truncating active intervals could be dealt with by creating "successor sessions" and "successor intervals" to carry forward the time tracked by the expired session's active intervals, if required.  This is best explained by example: say task interval X is active in human session A, capped at 4 hours, and that session A's absolute 4-hour timer expired 2 hours ago.  Furthermore, suppose interval X was started 1 hour into session A's absolute timer.
    - If the human runs `bd-timew stop X --session-id A`, the following would occur:
      - `bd-timew` detects that session A is expired.
      - `bd-timew` **formally closes interval X**, since X belonged to the expired session A.  Total time tracked to X internally is capped at 3 hours given our example parameters.
      - `bd-timew` creates an internal *successor session* A', started retroactively 2 hours ago (the moment A expired).  Via some mechanism (TBD), `bd-timew` records the fact that whenever the human runs commands with `--session-id A`, the command should be forwarded to the successor session, A'.
        - Since we're using the filesystem to track sessions (sentinel files), one possible mechanism for this would be **symlinks**: the original session sentinel for A could be replaced with a symlink to the sentinel for A'.
        - If A had expired *more than 4 hours ago*, then the successor session A' would be started retroactively **4 hours ago exactly**.  In other words, the retroactive start time of the successor A' is *at most <session_cap> hours ago*.
      - `bd-timew` creates a corresponding "successor interval" X', scoped to the successor session A', to track continued work on the task corresponding to the now-closed interval X.
        - As with the successor session, `bd-timew` would need to record the forward link between X and X' to allow the human to continue issuing commands against the original interval X.  This could be done using a special timetracking event of type "interval-forward" (for example), logged to the JSONL timetracking event log for the new successor session A'.
      - `bd-timew` retroactively starts X' at the same start-time it assigned to A' (2 hours ago in our example).
      - Finally, `bd-timew` closes X', completing the user's original `bd-timew stop X --session-id A` command.  Total time logged to interval X from the human operator's POV: 3 hours for X + 2 hours for X' = 5 hours.

#### Session caps: decision points

A number of key design questions present themselves when considering the "session cap" proposal for human-driven sessions.  For clarity, I'll refer to the cap as a "session clock" responsible for terminating human sessions when the clock reaches some (configurable) maximum duration.

**Open questions:**
- Should the session clock restart on every timetracking event logged against the session, or should it be absolute?  In other words, should the cap apply to *inactive session time* or *total session time*?
- *Cleanup / session-reaping strategy:* Should sentinel files for expired human sessions be removed, replaced with symlinks to a successor session (see the previous section for a fuller explanation of this idea), or handled in some other way?
- How should human / operator sessions be distinguished from Claude sessions?
  - Idea: the first line of the session sentinel file could be the name of the *session type*, e.g. "claude", "human", "hybrid", etc.  Alternatively, instead of writing this information to the sentinel file itself, it could be spliced into the sentinel file's name.
  - Regardless of how human and AI agent session sentinels are distinguished, the `bd-timew` CLI will need to expose options allowing the caller to specify *what kind of session sentinel to create* (session type) for "session start" commands.
- How should the "clock" be implemented?
  - *Idea #1:* Use the sentinel file's mtime.  If we decide that the session clock should reset on timetracking events, then simply `touch` the file on every such event.
  - *Idea #2:* Write a timestamp symbolizing the clock's "start time" to a line in the sentinel file itself.  Resetting the clock would become a `sed` or file-overwrite operation.
  - *Idea #3:* Embed the clock's starting timestamp in the sentinel file's name.  Resetting the clock would become a file-move operation.

---

# Claude's analysis (transcript)

> The following is a lightly-condensed transcript of Claude's responses across the
> design conversation that produced and refined this document. It preserves the
> reasoning, recommendations, pushback, and open questions; session artifacts
> (permission prompts, tool plumbing) are omitted.

## Turn 1 — initial review of the epic and children

**The keystone gap: `$BEADS_SESSION` does not exist.** Verified that the variable
is unset and `bd` has no native session concept. The schema's `session` field and
`bd-timew-ahp`'s "session ID resolved from `$BEADS_SESSION`" are built on a primitive
that isn't there yet. Defining it dissolves most of the open questions.

**Q1 (interval IDs bleeding across sessions) — a feature, not a bug, if interval IDs
are the authority.** Interval IDs are global ULIDs carried *in the event payload*; the
aggregator pairs start↔stop by interval ID across all files. Per-session files are
write-sharding to dodge merge conflicts, not a correctness boundary. So operations
should be query-driven: `stop X` looks up the open interval for X across aggregate
state and appends a stop (in the issuing session's own file) referencing that ULID.
A new (post-context-clear) session resumes by querying open intervals, never by
remembering an ID. Recommendation: always write to the issuing session's own file —
the only file a process has a guaranteed-safe append handle to; the ULID join makes
file placement irrelevant to correctness.

**Q2 (cancel is always retroactive) — cancel is *reaping*, and needs a liveness
signal the design lacks.** The actor emitting a cancel is not the crashed session and
doesn't remember the dead ID, so cancel must be discovery-driven: an orphan is an open
interval whose owning session is no longer alive or whose age exceeds a cap. The
reaper emits cancel into *its own* file. This requires a per-session liveness signal
(unspecced). Sharper point: for crashes, read-time stale-capping may suffice with no
persisted event at all; reserve a materialized `cancel` for *explicit* "discard this
interval" intent. Which log: the issuing/reaper's file, never the crashed one.

**Storage location — push back on "decoupled location."** The design conflates
decoupled *implementation* (good — JSONL doesn't care about Dolt) with decoupled
*location* (questionable). The unsurfaced question is *what syncs the per-session
files across machines* — the "per-session file to avoid git conflicts" rationale
assumes the files live in a synced repo. Recommendation: let storage **follow
`find_beads_dir()`** (companion-to-beads; rides existing sync; work under the shared
`~/.beads` converges into one dir → centralized billing with zero registry). Fall
back to `~/.local/share/bd-timew/<project>/` only when there's no local beads dir
(server-mode). Reject `~/.cache` (XDG-disposable; billing data is not). Optionally
auto-maintain a central index so aggregation never needs hand-curation.

**Smaller gaps:** event ordering ≠ interval ordering (corrections need their own
ordering key); the <4096-byte O_APPEND atomicity guarantee is now belt-and-suspenders
given file-per-session but bounds line size; `active` is only as live as the last
sync; migration must partition global `timew export` data back to projects;
single-session overlap (forgot to stop A before B) belongs in the reporting-mode
decision.

**Graph:** the six children had no `blocks` edges but a real order exists, and two
"P2 decisions" are actually upstream blockers (`tq9` storage-location blocks the
appender; `blh` reporting-mode blocks any billing report). A missing foundational
bead — "define session identity + liveness signal" — should block `ahp`.

## Turn 2 — point-by-point on the two primitives

**Framing that resolves the location sub-questions: two storage *classes*.**
(1) Timetracking facts (the event log) — historical, source-of-truth, **synced**.
(2) Live coordination state (claims + sentinels) — ephemeral, machine-local,
**rebuildable from the log**, **never synced**. Once claims/sentinels are class 2,
they don't belong in-tree, aren't gitignored-in-`.beads`, and aren't git-refs (refs
are built to sync — wrong grain). They go in `~/.local/state/bd-timew/<project-id>/`.
Worktrees stop being a problem (shared `<project-id>`).

**Primitive #1 — session claims.** A claim is not new information; "which sessions
hold an open interval on X" is fully derivable from the log. Every approach is really
"do we materialize an index of open intervals, and how?"
- *(A) `--actor`* — reject, and for a deeper reason than brittleness: `--actor`
  records *who last touched the bead*, not *who holds an open interval* — wrong signal
  in principle. (Still worth setting opportunistically for provenance.)
- *(B) flat registry* — reject; RMW on one file is the timew race.
- *(C) append-only claim JSONL* — your reasoning (log strictly dominates) is right and
  points to (D): a claim-JSONL grows unboundedly and needs compaction, whereas (D)
  represents only the *current* open set in O(open claims) space and self-truncates.
  C's analysis is the argument *for* D.
- *(D) claimfiles* — adopt the mechanism, but as a **cache, not control-flow**: the log
  is truth; claimfiles are rebuildable; on divergence, rebuild from the log. Your own
  example (`claims/X/{A,B}` coexisting) confirms claims are a **set, not a lock**, so
  `O_EXCL` test-and-set isn't the value-add. Location: `~/.local/state/...`, not
  `.beads/`. Reject git-refs. State the consequence: claimfiles are **per-machine**;
  cross-machine truth stays in the synced log.

**Primitive #2 — session lifecycle.** Verified Claude Code hook facts (load-bearing):
- `SessionEnd` does **NOT** fire on crash (SIGKILL, OOM, terminal/SSH close, power
  loss) — only graceful reasons.
- `/clear` is **graceful**: fires `SessionEnd(reason=clear)` on the old session and
  `SessionStart(source=clear)` on a new one (new `session_id`).
- Compaction keeps the **same** `session_id` (`SessionStart(source=compact)`, no
  SessionEnd).
- **No PID** in the hook payload; `$PPID` undocumented/unreliable. Payload carries
  `session_id`, `cwd`, `transcript_path`.

Implications: the "context cleared mid-work" worry is graceful — `/clear` closes the
old session's intervals cleanly; only a true crash leaks. **Don't use PID for
liveness — use timestamp freshness, source differs by type:** Claude → freshness of
`transcript_path` mtime (crash-robust, no PID, no poller); human → last-activity idle
timeout. Janitor: **lazy reap + SessionStart sweep** (no daemon); existing
`bd-timew-idle-stop.timer` as backstop.

**Operator "session caps" — biggest pushback.** The absolute-cap →
successor-session/successor-interval/symlink-forwarding/`interval-forward` machinery is
over-engineered and solves a self-inflicted problem (the absolute cap truncates legit
long work; successors un-truncate it). Keep the *instinct* (cap inactivity, not
length) but express it with machinery the repo **already has** (`record_activity`,
idle threshold, systemd timer — today scoped to Dolt-server lifecycle in `server.py`).
Replacement: **idle auto-close at last-activity-ts** (bounds over-billing to the idle
threshold; long *active* work never trips it); **resume = a new interval on the same
bead** (the aggregator sums per bead — the bead is the continuity anchor; intervals
are work-segments). No successors, no symlinks, no forwarding. Decision points
answered: reset-on-activity (not absolute); lazy reap + sweep (no symlinks); `type`
as a *field inside* the sentinel (not in the filename); structured JSON sentinel (not
mtime; not filename-encoded state).

**CLI:** `--session-id` should be **optional with auto-generation + a current-session
pointer**, not mandatory (the CLI can't enforce "Claude must pass it" anyway; mandatory
is a UX regression for humans). Companion commands yes; **historical audit belongs to
the event log**, not the ephemeral store. Lifecycle ops should be **idempotent and
forgiving** (start-when-exists → refresh; end-when-absent → no-op).

**The two primitives interlock:** *live claims = claimfiles ∩ live sentinels*; an
orphaned claimfile (claim for a dead session) is the trigger to close that session's
open interval at its last-good timestamp. That pipeline (stale freshness → reap
session → orphan claims → close intervals) replaces both the `cancel` hand-wringing
and the successor machinery.

**Sequencing:** build event log + summary cache + freshness-liveness/reaping first;
claimfiles only if the live "who holds X" query proves hot. Keeps `ahp` lean and
defers two-sources-of-truth surface area until earned.

## Turn 3 — the swarm reframing

**Reporting mode becomes principled.** With swarm-vs-supervised semantics, the
aggregation mode is a *function of coordination context*, not a global flag. It
generalizes to **aggregate by billing group**: a swarm is one group whose intervals
are *unioned*; each supervised agent / human is its own group; total =
`Σ over groups of (union-within-group)`. This absorbs the AI-vs-human question (Q3):
different actor types are different groups. The continuity anchor refines to
**(bead × billing-group)**; the successor machinery still buys nothing.

**The one pre-merge carve-out: schema provenance must land now.** Billing-group
classification needs per-interval provenance, and the log is immutable. Omitting it
forces a backfill of un-rewritable history. Reserve low-regret nullable fields in v1
(`group_id`, an actor-kind field, optionally `role`) — capturable today
(human/supervised distinguishable now; `group_id` null until the coordinator sets it),
so pre-merge logs come out fully classifiable with zero backfill. Denormalize at write
time (same rationale as the `tags` snapshot); don't plan to reconstruct from ephemeral
coordinator state.

**The deferral is well-grounded, not just convenient.** Claimfiles/sentinels'
*correct design* depends on the coordinator/visibility model and the (Lua) hook
architecture, both of which live in claude-config: across containers/Firecracker VMs,
claimfiles aren't visible without a shared mount; `transcript_path` liveness needs a
coordinator-mediated heartbeat. So the operational layer is *correctly blocked* on
claude-config.

**Operational/authoritative cut is a module boundary → consider "depend," not
"merge."** bd-timew is agent-agnostic (manual humans, supervised, autonomous all
trackable); a solo human shouldn't pull in swarm scaffolding. Option: bd-timew stays a
standalone installable owning the authoritative log + schema + aggregator + CLI;
claude-config builds the coordinator layer *on top of* it, owning claimfiles,
sentinels, hooks, and swarm semantics, and setting the provenance fields. The Lua-perf
argument compels *integration*, which "depend" satisfies — it doesn't compel a
wholesale merge. Cold/hot path split aligns: hot path = operational state (fast,
cached, claude-config); cold path = log aggregation (slow, authoritative, bd-timew).

**Revised stance on claimfiles-as-gates:** for *coordination* decisions a coordinator
legitimately treats its own operational state as control-flow, and the latency budget
demands it — allowed, provided the state stays coordinator-private and rebuildable and
is **never** the billing truth (a stale/missing gate degrades to "re-derive from the
log," never to a wrong record).

**Summary cache (`zzl`) implication:** cache at the finest provenance granularity so
mode-dependent, config-driven aggregation can be applied at report time.
