#!/usr/bin/env python3
"""Run an evidence-driven Codex review and emit a structured report."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence


SKILL_DIR = Path(__file__).resolve().parent.parent
PROMPT_FILE = SKILL_DIR / "references" / "review_prompt.md"
METHOD_FILE = SKILL_DIR / "references" / "review_method.md"
SCHEMA_FILE = SKILL_DIR / "references" / "review.schema.json"
MAX_CONTEXT_BYTES = 1_500_000
MAX_CONTEXT_FILE_BYTES = 500_000


class HarnessError(RuntimeError):
    """A concise error suitable for display to the user."""


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise HarnessError(f"cannot run {command[0]!r}: {exc}") from exc


def _git(repo: Path, *arguments: str) -> str:
    result = _run(["git", *arguments], cwd=repo)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise HarnessError(f"git {' '.join(arguments)} failed: {detail}")
    return result.stdout.strip()


def resolve_repo(value: str) -> Path:
    candidate = Path(value).expanduser().resolve()
    if not candidate.is_dir():
        raise HarnessError(f"repository directory does not exist: {candidate}")
    root = _git(candidate, "rev-parse", "--show-toplevel")
    return Path(root).resolve()


def ensure_commit(repo: Path, ref: str) -> str:
    try:
        return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    except HarnessError as exc:
        raise HarnessError(
            f"review target {ref!r} is not available locally; fetch it first"
        ) from exc


def _bounded_text(value: Any, limit: int = 8_000) -> str:
    text = value if isinstance(value, str) else ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated by review harness]"


def _actor_login(value: Any) -> str:
    if isinstance(value, dict):
        login = value.get("login")
        if isinstance(login, str):
            return login
    return ""


def compact_pr_context(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep review-relevant PR data while bounding discussion size."""

    result: dict[str, Any] = {
        key: raw.get(key)
        for key in (
            "number",
            "title",
            "url",
            "isDraft",
            "reviewDecision",
            "baseRefName",
            "baseRefOid",
            "headRefName",
            "headRefOid",
        )
    }
    result["body"] = _bounded_text(raw.get("body"), 20_000)
    result["closingIssuesReferences"] = [
        {
            "number": item.get("number"),
            "title": item.get("title"),
            "url": item.get("url"),
        }
        for item in raw.get("closingIssuesReferences", [])
        if isinstance(item, dict)
    ][:50]
    result["files"] = [
        {
            "path": item.get("path"),
            "additions": item.get("additions"),
            "deletions": item.get("deletions"),
        }
        for item in raw.get("files", [])
        if isinstance(item, dict)
    ][:1_000]
    result["comments"] = [
        {
            "author": _actor_login(item.get("author")),
            "body": _bounded_text(item.get("body")),
            "url": item.get("url"),
            "createdAt": item.get("createdAt"),
        }
        for item in raw.get("comments", [])
        if isinstance(item, dict)
    ][-100:]
    result["reviews"] = [
        {
            "author": _actor_login(item.get("author")),
            "state": item.get("state"),
            "body": _bounded_text(item.get("body")),
            "submittedAt": item.get("submittedAt"),
        }
        for item in raw.get("reviews", [])
        if isinstance(item, dict)
    ][-100:]
    result["statusCheckRollup"] = [
        {
            key: item.get(key)
            for key in (
                "__typename",
                "name",
                "context",
                "workflowName",
                "status",
                "conclusion",
                "state",
                "detailsUrl",
                "targetUrl",
                "startedAt",
                "completedAt",
            )
            if item.get(key) is not None
        }
        for item in raw.get("statusCheckRollup", [])
        if isinstance(item, dict)
    ][:200]
    return result


def collect_pr_context(
    repo: Path, pr: str, gh_bin: str
) -> tuple[list[str], str, str]:
    fields = ",".join(
        (
            "number",
            "title",
            "url",
            "body",
            "isDraft",
            "reviewDecision",
            "baseRefName",
            "baseRefOid",
            "headRefName",
            "headRefOid",
            "closingIssuesReferences",
            "comments",
            "reviews",
            "files",
            "statusCheckRollup",
        )
    )
    result = _run([gh_bin, "pr", "view", pr, "--json", fields], cwd=repo)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise HarnessError(f"cannot read PR {pr!r} with gh: {detail}")
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HarnessError("gh returned invalid PR JSON") from exc

    head_oid = str(raw.get("headRefOid") or "")
    base_oid = str(raw.get("baseRefOid") or "")
    if not head_oid or not base_oid:
        raise HarnessError("PR metadata did not include base/head commit IDs")

    current_head = ensure_commit(repo, "HEAD")
    if current_head != head_oid:
        raise HarnessError(
            "the current checkout is not the PR head "
            f"({current_head[:12]} != {head_oid[:12]}); run "
            f"`gh pr checkout {raw.get('number') or pr}` first"
        )
    ensure_commit(repo, base_oid)

    compact = compact_pr_context(raw)
    context = json.dumps(compact, ensure_ascii=False, indent=2)
    label = (
        f"PR #{compact.get('number')}: {compact.get('title')} "
        f"({compact.get('url')})"
    )
    return ["--base", base_oid], label, context


def read_context_files(values: Sequence[str]) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    total = 0
    for value in values:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise HarnessError(f"context file does not exist: {path}")
        size = path.stat().st_size
        if size > MAX_CONTEXT_FILE_BYTES:
            raise HarnessError(
                f"context file exceeds {MAX_CONTEXT_FILE_BYTES} bytes: {path}"
            )
        total += size
        if total > MAX_CONTEXT_BYTES:
            raise HarnessError(
                f"combined context exceeds {MAX_CONTEXT_BYTES} bytes"
            )
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise HarnessError(f"cannot read context file {path}: {exc}") from exc
        sections.append((str(path), content))
    return sections


def build_prompt(
    *,
    target: str,
    claim: str,
    context_sections: Sequence[tuple[str, str]],
    extra_instruction: str,
) -> str:
    try:
        template = PROMPT_FILE.read_text(encoding="utf-8")
        method = METHOD_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        raise HarnessError(f"cannot read review prompt resources: {exc}") from exc

    rendered_sections = []
    for source, content in context_sections:
        safe_content = content.replace("</review-context>", "<\\/review-context>")
        rendered_sections.append(
            f'<review-context source="{source}">\n{safe_content}\n</review-context>'
        )
    context = "\n\n".join(rendered_sections) or "(no external context supplied)"
    return (
        template.replace("{{TARGET}}", target)
        .replace("{{CLAIM}}", claim or "(no separate claim supplied)")
        .replace("{{CONTEXT}}", context)
        .replace("{{REVIEW_METHOD}}", method)
        .replace(
            "{{EXTRA_INSTRUCTION}}",
            extra_instruction or "(no additional instruction)",
        )
    )


def select_target(
    args: argparse.Namespace, repo: Path
) -> tuple[list[str], str, list[tuple[str, str]]]:
    context: list[tuple[str, str]] = []
    if args.pr:
        selector, label, pr_context = collect_pr_context(repo, args.pr, args.gh_bin)
        context.append((f"GitHub PR {args.pr}", pr_context))
        return selector, label, context
    if args.base:
        base_oid = ensure_commit(repo, args.base)
        head_oid = ensure_commit(repo, "HEAD")
        return (
            ["--base", args.base],
            f"branch diff {base_oid[:12]}...{head_oid[:12]} (base {args.base})",
            context,
        )
    if args.commit:
        commit_oid = ensure_commit(repo, args.commit)
        return (
            ["--commit", args.commit],
            f"commit {commit_oid}",
            context,
        )
    if args.uncommitted:
        status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
        if not status:
            raise HarnessError("there are no uncommitted changes to review")
        return ["--uncommitted"], "uncommitted working-tree changes", context
    raise AssertionError("argparse requires one review target")


def build_codex_command(
    *,
    codex_bin: str,
    selector: Sequence[str],
    output_path: Path,
    model: str | None,
) -> list[str]:
    command = [
        codex_bin,
        "exec",
        "review",
        "--ephemeral",
        "--output-schema",
        str(SCHEMA_FILE),
        "-o",
        str(output_path),
    ]
    if model:
        command.extend(["--model", model])
    command.extend(selector)
    command.append("-")
    return command


def invoke_codex(
    *,
    repo: Path,
    selector: Sequence[str],
    prompt: str,
    codex_bin: str,
    model: str | None,
    verbose: bool,
) -> dict[str, Any]:
    if not SCHEMA_FILE.is_file():
        raise HarnessError(f"output schema is missing: {SCHEMA_FILE}")
    with tempfile.TemporaryDirectory(prefix="review-harness-") as temp_dir:
        result_path = Path(temp_dir) / "report.json"
        command = build_codex_command(
            codex_bin=codex_bin,
            selector=selector,
            output_path=result_path,
            model=model,
        )
        result = _run(command, cwd=repo, input_text=prompt)
        if verbose:
            if result.stdout:
                print(result.stdout, file=sys.stderr, end="")
            if result.stderr:
                print(result.stderr, file=sys.stderr, end="")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            if len(detail) > 4_000:
                detail = detail[-4_000:]
            raise HarnessError(f"Codex review failed: {detail}")
        if not result_path.is_file():
            raise HarnessError("Codex completed without writing a review report")
        try:
            report = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HarnessError(f"Codex wrote an invalid review report: {exc}") from exc
    if not isinstance(report, dict):
        raise HarnessError("Codex review report must be a JSON object")
    return report


def _bullets(values: Sequence[Any], empty: str = "- None.") -> str:
    lines = [f"- {value}" for value in values if str(value).strip()]
    return "\n".join(lines) if lines else empty


def render_markdown(report: dict[str, Any]) -> str:
    verdict = str(report.get("verdict", "comment")).replace("_", " ").title()
    confidence = str(report.get("confidence", "unknown")).title()
    problem = report.get("problem_assessment") or {}
    solution = report.get("solution_assessment") or {}
    lines = [
        "# Review report",
        "",
        f"**Reviewed scope:** {report.get('target', '')}",
        "",
        "## Background and normal flow",
        "",
        str(problem.get("background", "")).strip(),
        "",
        _bullets(problem.get("normal_flow") or []),
        "",
        "## Claimed problem",
        "",
        str(problem.get("claimed_problem", "")).strip(),
        "",
        "**Trigger and preconditions**",
        "",
        _bullets(problem.get("preconditions") or []),
        "",
        "## Does the problem exist?",
        "",
        f"**Assessment:** `{problem.get('status', 'unknown')}`",
        "",
        str(problem.get("root_cause", "")).strip(),
        "",
        "## Is it necessary to solve?",
        "",
        str(problem.get("necessity", "")).strip(),
        "",
        "## Is the solution correct?",
        "",
        f"**Assessment:** `{solution.get('status', 'unknown')}`",
        "",
        str(solution.get("summary", "")).strip(),
        "",
        "**Covered paths**",
        "",
        _bullets(solution.get("covered_paths") or []),
        "",
        "**Uncovered paths**",
        "",
        _bullets(solution.get("uncovered_paths") or []),
        "",
        "**Invariants**",
        "",
        _bullets(
            [
                (
                    f"`{item.get('status', 'uncertain')}` "
                    f"{item.get('name', '')} — {item.get('evidence', '')}"
                )
                for item in solution.get("invariants") or []
            ]
        ),
        "",
    ]

    coverage = report.get("review_coverage")
    if coverage is not None:
        lines.extend(["## Review coverage", ""])
        if not coverage:
            lines.extend(["No review coverage recorded.", ""])
        for item in coverage:
            lines.extend(
                [
                    f"### {item.get('scope', '')}",
                    "",
                    f"**Review status:** `{item.get('status', 'not_reviewed')}`",
                    "",
                    "**Paths traced**",
                    "",
                    _bullets(item.get("reviewed_paths") or []),
                    "",
                    f"**Validation evidence:** {item.get('validation', '')}",
                    "",
                    "**Coverage limits**",
                    "",
                    _bullets(item.get("limitations") or []),
                    "",
                ]
            )

    lines.extend(["## Findings", ""])
    findings = report.get("findings") or []
    if not findings:
        lines.extend(["No actionable findings.", ""])
    for finding in findings:
        location = str(finding.get("path") or "")
        start = finding.get("line_start")
        end = finding.get("line_end")
        if location and start:
            location += f":{start}"
            if end and end != start:
                location += f"-{end}"
        lines.extend(
            [
                (
                    f"### [{finding.get('priority', 'P?')}] "
                    f"{finding.get('title', '')}"
                ),
                "",
                (
                    f"- Blocking: `{str(bool(finding.get('blocking'))).lower()}`"
                    f"; confidence: `{finding.get('confidence', 'unknown')}`"
                ),
                f"- Location: `{location or 'not localized'}`",
                (
                    "- Introduced by change: "
                    f"`{finding.get('introduced_by_change', 'uncertain')}`"
                ),
                "",
                str(finding.get("evidence", "")).strip(),
                "",
                "**Failure timeline**",
                "",
                _bullets(finding.get("failure_timeline") or []),
                "",
                f"**Impact:** {finding.get('impact', '')}",
                "",
                f"**Recommended fix:** {finding.get('recommendation', '')}",
                "",
                f"**Regression test:** {finding.get('regression_test', '')}",
                "",
            ]
        )

    notes = report.get("non_blocking_notes") or []
    if notes:
        lines.extend(["## Non-blocking notes", ""])
        for note in notes:
            lines.extend(
                [
                    f"### {note.get('title', '')}",
                    "",
                    str(note.get("evidence", "")).strip(),
                    "",
                    f"**Suggestion:** {note.get('recommendation', '')}",
                    "",
                ]
            )

    lines.extend(["## Validation", ""])
    validations = report.get("validation") or []
    if validations:
        for item in validations:
            lines.append(
                f"- `{item.get('status', 'not_run')}` "
                f"`{item.get('command', '')}` — {item.get('result', '')}"
            )
    else:
        lines.append("- No validation commands reported.")

    lines.extend(
        [
            "",
            "## Missing evidence",
            "",
            _bullets(report.get("missing_evidence") or []),
            "",
            "## Final verdict",
            "",
            f"**Verdict:** {verdict}  ",
            f"**Confidence:** {confidence}",
            "",
            str(report.get("executive_summary", "")).strip(),
            "",
            "## Suggested review",
            "",
            f"**Decision:** `{(report.get('suggested_review') or {}).get('decision', 'comment')}`",
            "",
            str((report.get("suggested_review") or {}).get("body", "")).strip(),
            "",
        ]
    )
    return "\n".join(lines)


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            if not content.endswith("\n"):
                stream.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Review a PR or local diff by first proving the problem, then "
            "checking whether the fix closes the real failure path."
        )
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="Git repository to review (default: current directory)",
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--pr",
        help=(
            "GitHub PR number/URL; the current checkout must already be at "
            "the PR head"
        ),
    )
    target.add_argument("--base", help="review HEAD against this base ref")
    target.add_argument("--commit", help="review the change introduced by a commit")
    target.add_argument(
        "--uncommitted",
        action="store_true",
        help="review staged, unstaged, and untracked changes",
    )
    parser.add_argument(
        "--claim",
        default="",
        help="the problem and fix claimed by the author",
    )
    parser.add_argument(
        "--context",
        action="append",
        default=[],
        metavar="FILE",
        help="attach issue/RFC/design context; may be repeated",
    )
    parser.add_argument(
        "--extra-instruction",
        default="",
        help="additional review focus without replacing the core gates",
    )
    parser.add_argument("--model", help="optional Codex review model")
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="report format (default: markdown)",
    )
    parser.add_argument("--output", help="write the report to this file")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the generated command and prompt without invoking Codex",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="stream captured Codex diagnostics after the run",
    )
    parser.add_argument("--codex-bin", default="codex", help=argparse.SUPPRESS)
    parser.add_argument("--gh-bin", default="gh", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    try:
        repo = resolve_repo(args.repo)
        selector, target_label, implicit_context = select_target(args, repo)
        explicit_context = read_context_files(args.context)
        prompt = build_prompt(
            target=target_label,
            claim=args.claim,
            context_sections=[*implicit_context, *explicit_context],
            extra_instruction=args.extra_instruction,
        )
        if args.dry_run:
            preview_result = Path("<temporary-report.json>")
            command = build_codex_command(
                codex_bin=args.codex_bin,
                selector=selector,
                output_path=preview_result,
                model=args.model,
            )
            print(f"# cwd: {repo}")
            print(f"# command: {shlex.join(command)}")
            print("# prompt:")
            print(prompt)
            return 0

        report = invoke_codex(
            repo=repo,
            selector=selector,
            prompt=prompt,
            codex_bin=args.codex_bin,
            model=args.model,
            verbose=args.verbose,
        )
        content = (
            json.dumps(report, ensure_ascii=False, indent=2)
            if args.format == "json"
            else render_markdown(report)
        )
        if args.output:
            atomic_write(Path(args.output).expanduser().resolve(), content)
        else:
            print(content)
        return 0
    except HarnessError as exc:
        print(f"review-harness: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
