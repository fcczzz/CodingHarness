# Review methodology

This harness was distilled from recurring patterns in recent Mooncake reviews.
It is designed for reviews that must answer more than “does the diff look
reasonable?”

## Core question

Establish three conclusions independently:

1. Is the reported problem real?
2. Does the proposed change close the real failure path?
3. Is the change appropriately scoped and ready to approve?

Do not let a correct diagnosis imply a correct implementation, or a useful
partial fix imply a complete fix.

## Why the evidence gates matter

### Scope and snapshot identity

A review conclusion is about one immutable program state, not a branch name
that may move. Recording the base, head, merge base, checkout, and dirty state
where available prevents code inspection from one revision, tests from
another, and a verdict about a third. Diff-only and uncommitted reviews can use
an explicit artifact boundary instead of pretending unavailable SHA metadata
exists. If the target moves, only unaffected evidence survives.

### Independent specification and test oracles

Current code shows what the system does; it does not by itself establish what
the system must do. Likewise, a patch and its new tests can encode the same
mistaken assumption and agree perfectly. Material behavioral claims therefore
need provenance: explicit contracts are direct specification evidence,
existing callers and tests are behavioral evidence, author statements are
claims, and reviewer reasoning is inference. Material disagreement between
them must be resolved rather than hidden. Asking whether a plausible wrong
implementation would still pass the regression test exposes circular tests
without requiring heavyweight mutation testing.

### Reviewer self-disproof

A candidate finding is a hypothesis to attack, not prose to defend. Searching
for upstream guards, serialization, ownership guarantees, unreachable state
transitions, protocol guarantees, and drain behavior is the review equivalent
of a negative control. A concern becomes actionable only after those checks
leave a reachable invariant violation; incomplete evidence should reduce
confidence or become a question, not a confident bug report.

### Source-level proof and reproduction

Dynamic reproduction is valuable because it falsifies assumptions about
reachability, but it is not the only proof. Deterministic ordering,
state-transition, ownership/lifetime, protocol, or interface evidence can prove
a reachable invariant violation even when recreating a production failure is
impractical. This proves the code defect, not every proposed story about how a
field symptom arose.

### Production-sensitive correctness

In Mooncake-style systems, an extra copy, unbounded queue, global lock, rollout
incompatibility, or irreversible state transition can violate the effective
service contract even if a unit test returns the right value. Performance and
deployment checks should therefore follow plausible changes to hot paths,
scale, state, or version boundaries. They are not generic demands for a
benchmark or rollout plan on every patch.

### CI reuse and proportional local validation

CI and local validation answer the same question only when they run equivalent
commands against the same immutable head and relevant configuration. Reusing
green matching-head CI avoids spending review time on a duplicate full build.
Local validation should instead buy new evidence: a focused regression path, a
counterexample, a missing platform or feature variant, or investigation of a
failed or stale check. Passing CI remains bounded evidence; it cannot establish
semantic correctness for behavior the jobs do not exercise.

## Patterns that shaped the harness

### Configuration propagation (#3077)

Trace a setting from input through every wrapper/config object to the final
consumer. Check HA and non-HA construction paths, defaults, invalid-value
fallbacks, and overrides. Require tests to traverse the path that actually
dropped the setting.

### Architecture and contracts (#3043)

Separate provider interfaces from backend policy. Check whether an abstraction
forces one storage system to emulate capabilities it does not naturally have.
Review exact-size reads, per-object failure reporting, pagination/backpressure,
binary key encoding, lifecycle/GC, and compatibility with parallel PRs.

### Fail-fast and stale members (#3138)

Check repeated initialization, not only first initialization. A failed attempt
must not leave an older backend or half-initialized member active. Prefer tests
through real production entry points. Treat public API expansion as a separate
tradeoff from the minimal crash fix.

### Asynchronous I/O counterexamples (#3073)

Attack aggregate proofs. A correct total byte count does not prove every chunk
is complete. Check short I/O, partial submission, interruption, cancellation,
draining, and whether an old in-flight request can still write to a reused
buffer. Keep confirmed API behavior separate from unproven kernel or stale-CQE
causal stories.

### Mirrored state and scope (#3160)

List every representation of an asynchronous task and every transition:
create, enqueue, dispatch, remove, expire, complete, rollback, restart, and
re-register. Use event timelines to find late completion. A narrow
pre-dispatch fix may be mergeable even when a post-dispatch race remains, but
the PR must not claim a complete lifecycle fix.

## Finding quality bar

An actionable finding forms a falsifiable chain from a supported path at the
reviewed revision, through a concrete invariant or contract violation, to an
observable impact and a clear relation to the patch. A proportionate fix and a
regression path through the real production entry point close that chain. The
canonical acceptance gate is in `review_method.md`; this methodology does not
define a second checklist.

Severity describes impact; confidence describes proof strength. Trying to
disprove reachability before a finding crosses the actionable threshold keeps
missing guards or evidence as scoped questions instead of asserted defects.
