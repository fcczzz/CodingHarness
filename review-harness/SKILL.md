---
name: review-harness
description: Evidence-driven, explanation-first code and pull-request review workflow. Use whenever Codex is asked to review a PR, branch, commit, diff, or uncommitted changes; explain the background of a reported problem; assess whether the problem is real or necessary to solve; judge a fix or architecture for correctness and necessity; decide approve versus request changes; or draft review comments. Trigger on requests such as “review this PR”, “看看这个 PR 的问题/合理性”, “这个解法正确吗/有必要吗”, and “我该 approve 吗”.
---

# Evidence-driven review

Read `references/review_method.md` completely before reviewing. Use it as the
canonical review gates and interactive output discipline.

Before substantive analysis, enforce its reviewed-snapshot gate. If the target
changes during review, invalidate stale evidence and rerun the affected gates
before carrying a verdict forward.

Read `references/methodology.md` when explaining the workflow, tailoring it to
Mooncake-style systems work, or evaluating whether a new review check belongs
in the harness.

## Choose the execution path

1. For an interactive Codex task, apply the canonical harness directly in the
   current task. Use the connected GitHub tools for remote PR metadata, diff,
   review threads, related issues, and prior PRs. Inspect the local checkout
   for full code context when available. Return normal prose in the user's
   language; never expose the wrapper's JSON report unless the user explicitly
   requests JSON.
2. For a reproducible or unattended local report, run:

   ```bash
   python3 <skill-dir>/scripts/review_harness.py \
     --repo <repo> \
     --pr <number>
   ```

   Resolve `<skill-dir>` from the path of this `SKILL.md`; do not assume the
   skill is installed in a particular home directory.
   Use `--base`, `--commit`, or `--uncommitted` when those scopes fit better.
   Use `--context` for issue/RFC/design files and `--claim` for the author's
   stated problem.
3. Do not start a nested Codex CLI review merely to duplicate work already
   being performed interactively. “Use the harness” means enforce its gates
   and output discipline; use the wrapper when a standalone report is useful.
   Do not read `references/review_prompt.md` during an interactive review; it
   is a machine-only template used by the wrapper.

## Required behavior

- Reconstruct the subsystem background and normal pre-change flow before
  judging the reported problem or patch.
- Establish the pre-change failure path before judging the patch.
- Separate confirmed defects from plausible but unproven causal stories.
- Trace the proposed fix from ingress to final consumer across all relevant
  variants.
- Write state invariants and explicit event timelines for asynchronous work.
- Try concrete counterexamples involving partial success, stale completion,
  cancellation, retry, timeout, restart, and repeated initialization.
- Check interface contracts, architecture boundaries, compatibility, scale,
  lifecycle closure, and related PR/RFC interaction when relevant.
- Distinguish a blocking defect from a pre-existing adjacent risk and a
  mergeable follow-up.
- Require code evidence and a regression path for every actionable finding.
- Prefer successful CI from the exact reviewed head over duplicating the same
  full build or test suite locally. Run local focused validation only when it
  addresses a concrete failure path, missing CI coverage, or stale CI evidence.
- Report tests and commands only when actually run.
- Treat the interactive output order as a hard acceptance criterion. Performing
  the background analysis internally is not sufficient: the visible response
  must present the explanatory stages before findings or a verdict.

Do not modify the code or post/approve a GitHub review unless the user
unambiguously authorizes that exact external action. Treat requests such as
"write a comment", "I want to comment", "comment on this", or “发表一下
comment” as requests for a draft unless the same message clearly identifies
the destination and either supplies the exact final text or confirms a shown
preview for immediate publication. When any doubt remains, provide a suggested
comment and ask for confirmation; do not infer publication authority from the
surrounding review conversation. For an uncertain concern, ask the author to
confirm the suspected ordering or guard instead of asserting a bug.

Default to an explanatory review. Unless the user explicitly requests a quick
verdict or findings-only review, do not lead with the conclusion or a severity
list. Explain the background and normal flow first, then establish whether the
problem exists, assess whether it is worth solving, trace whether the proposed
solution is correct and complete, and only then give findings, validation,
missing evidence, the final verdict, and a concise suggested review comment.

Before sending an interactive review, run the mandatory pre-send output gate in
`references/review_method.md`. If any gate fails, rewrite the response instead
of sending a compressed summary that jumps from a cursory introduction to
findings.
