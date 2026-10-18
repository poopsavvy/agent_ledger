#!/usr/bin/env python3
"""agent-ledger - a claim ledger for AI agents.

An agent's most expensive failure is not being wrong; it is being confidently
unchecked. This tool makes the difference visible and auditable:

  * every claim an agent makes is recorded with a verdict, evidence and source;
  * a claim may only call itself "verified" if it names where the check came
    from, so self-reasoning cannot masquerade as a check;
  * a claim may only call itself "falsified" if the evidence actually reads
    like a failure report;
  * `audit` re-reads the ledger and flags what was never resolved, what was
    asserted without a source, and what cites files that do not exist.

Verdict vocabulary (fixed, do not invent more):
  unchecked       - asserted, nobody has looked yet
  verified        - a check was run/read that would have contradicted it, and did not
  falsified       - a check contradicted it
  not_applicable  - the claim does not need a check (with a reason)

Exit codes: 0 ok, 1 audit found findings, 2 usage or ledger error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

SCHEMA_VERSION = 1
DEFAULT_LEDGER = ".ledger.json"

VERDICTS = ("unchecked", "verified", "falsified", "not_applicable")

ALIASES = {
    "true": "verified", "supported": "verified", "pass": "verified",
    "passed": "verified", "proven": "verified", "confirmed": "verified",
    "false": "falsified", "refuted": "falsified", "fail": "falsified",
    "failed": "falsified", "disproven": "falsified",
    "unknown": "unchecked", "todo": "unchecked", "unverified": "unchecked",
    "open": "unchecked",
    "na": "not_applicable", "n/a": "not_applicable", "skip": "not_applicable",
    "n_a": "not_applicable",
}

INTERNAL_SOURCES = {"internal", "self", "internal:self", "reasoning",
                    "intuition", "memory", "recall", "plausible", "probably"}

# Words that make a claim an external-world fact, which self-reasoning cannot settle.
EXTERNAL_PATTERNS = (
    r"\b(19|20)\d{2}\b",                 # a year
    r"\b\d+(?:\.\d+)?\s?%",              # a percentage
    r"\bv?\d+\.\d+(?:\.\d+)*\b",         # a version number
    r"\b\d+\s?(?:mb|gb|kb|ms|s|seconds|minutes|hours|users|records|rows|lines)\b",
    r"https?://",                        # a URL
    r"\b(?:latest|current|newest|as of|as of today|released|release|changelog|"
    r"documentation|docs|api|spec|specification|standard|rfc|license|"
    r"according to|reportedly|supports|compatible|requires|deprecated|"
    r"benchmark|statistic|study|survey|law|regulation|statute)\b",
    r"(?:法规|条例|法条|标准|版本|官方|文档|据统计|研究表明|截至|最新)",
)

# Words that mark evidence as a failure report rather than a success report.
NEGATIVE_MARKERS = (
    "fail", "failed", "failing", "error", "errors", "contradict", "wrong",
    "incorrect", "not true", "false", "refuted", "mismatch", "does not match",
    "did not", "doesn't", "does not", "unable to", "cannot", "can't",
    "exit code 1", "traceback", "no such", "missing", "not found", "0 matches",
    "0 files", "empty", "timeout", "denied", "rejected", "mismatch",
    "不一致", "失败", "报错", "无法", "为空", "不存在", "未找到",
)

# Words that admit the check never actually happened.
NO_CHECK_MARKERS = (
    "should work", "should pass", "should be fine", "presumably", "probably",
    "likely works", "seems fine", "looks correct", "i assume", "assume that",
    "not run", "not tested", "did not run", "didn't run", "haven't run",
    "no test", "by inspection only", "trust me", "obviously",
    "应该没问题", "大概", "估计", "未运行", "没测",
)

# Counter-signals: text that shows a command really was run and produced output.
# A marker phrase inside genuine output (for example an agent quoting this tool's
# own refusal message) must not be mistaken for "no check happened".
RUN_SIGNAL_RE = re.compile(
    r"(?:\bexit(?:ed|s|ing)?\b[^.]{0,30}?\d"
    r"|\b(?:ran|reran|re-ran|executed|invoked|opened|fetched|queried|walked|launched)\b"
    r"|\b\d+\s*(?:tests?|matches|files?|rows?|lines?|claims?|results?|findings?)\b"
    r"|\b(?:stdout|stderr|traceback|assertion|returned|printed|output|wrote)\b)"
)

FILE_TOKEN_RE = re.compile(
    r"(?<![\w:/\\-])(?:[\w.\-]+[/\\])*[\w.\-]+\.(?:py|md|json|txt|js|mjs|ts|tsx|"
    r"html|css|scss|csv|tsv|yaml|yml|toml|ini|cfg|sh|ps1|go|rs|java|rb|sql|xml)"
)


class LedgerError(Exception):
    """Bad input or unusable ledger file."""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_verdict(raw: str) -> str:
    key = (raw or "").strip().lower().replace("-", "_")
    if key in VERDICTS:
        return key
    if key in ALIASES:
        return ALIASES[key]
    raise LedgerError(
        "unknown verdict %r; use one of: %s" % (raw, ", ".join(VERDICTS))
    )


def normalize_claim(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def claim_id(task: str, claim: str) -> str:
    digest = hashlib.sha1(("%s\x00%s" % (task, normalize_claim(claim))).encode("utf-8"))
    return digest.hexdigest()[:10]


def load(path: str) -> dict:
    if not os.path.exists(path):
        raise LedgerError(
            "no ledger at %s; create one with the 'init' command" % path
        )
    try:
        with open(path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except json.JSONDecodeError as exc:
        raise LedgerError("ledger %s is not valid JSON: %s" % (path, exc))
    if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION:
        raise LedgerError(
            "ledger %s has unsupported schema_version %r (expected %d)"
            % (path, state.get("schema_version") if isinstance(state, dict) else None,
               SCHEMA_VERSION)
        )
    for key, default in (("checks", {}), ("records", {}), ("log", []), ("task", "")):
        state.setdefault(key, default)
    return state


def save(path: str, state: dict) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    for line in payload.get("lines", []):
        print(line)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_init(args) -> int:
    if os.path.exists(args.file) and not args.force:
        raise LedgerError("%s already exists (use --force to reset it)" % args.file)
    state = {
        "schema_version": SCHEMA_VERSION,
        "task": args.task or "",
        "created": now_iso(),
        "updated": now_iso(),
        "checks": {},
        "records": {},
        "log": [],
    }
    save(args.file, state)
    emit({"ok": True, "file": args.file, "task": state["task"],
          "lines": ["initialized %s (task: %s)" % (args.file, state["task"] or "unnamed")]},
         args.json)
    return 0


def cmd_check_add(args) -> int:
    state = load(args.file)
    cid = args.id.strip()
    if not cid:
        raise LedgerError("--id must not be empty")
    if not args.question.strip():
        raise LedgerError("--question must not be empty")
    if not args.method.strip():
        raise LedgerError("--method must not be empty")
    state["checks"][cid] = {
        "question": args.question.strip(),
        "method": args.method.strip(),
        "command": (args.command or "").strip(),
        "added": now_iso(),
    }
    state["updated"] = now_iso()
    save(args.file, state)
    emit({"ok": True, "check": cid,
          "lines": ["check %s registered: %s" % (cid, args.question.strip())]},
         args.json)
    return 0


def _reject_uncheckable_evidence(claim: str, verdict: str, evidence: str,
                                 source: str) -> None:
    """The teeth: stop the two ways a verdict gets faked."""
    low_claim = claim.lower()
    low_ev = evidence.lower()
    low_src = source.lower()

    if verdict in ("verified", "falsified"):
        if not evidence.strip():
            raise LedgerError(
                "verdict %r needs --evidence describing what the check actually "
                "produced" % verdict
            )
        if len(evidence.strip()) < 8:
            raise LedgerError(
                "evidence %r is too short to describe a check; paste what the "
                "check actually produced (or record the claim as 'unchecked')"
                % evidence
            )

    if verdict == "verified":
        if not source:
            raise LedgerError(
                "verdict 'verified' needs --source (where the check came from: a "
                "command, a file you read, a page you fetched) or --internal to "
                "declare that the check was your own reasoning"
            )
        if any(marker in low_ev for marker in NO_CHECK_MARKERS) and not \
                RUN_SIGNAL_RE.search(low_ev):
            raise LedgerError(
                "evidence reads like the check was skipped (%r); a skipped check "
                "is 'unchecked', not 'verified'" % evidence
            )
        if any(marker in low_src for marker in INTERNAL_SOURCES):
            marker = _first_external(low_claim)
            if marker:
                raise LedgerError(
                    "this claim looks like an external-world fact (matched %r) but "
                    "the source is %r, i.e. your own reasoning; go check it, or "
                    "record it as 'unchecked'" % (marker, source)
                )

    if verdict == "falsified":
        if not any(marker in low_ev for marker in NEGATIVE_MARKERS):
            raise LedgerError(
                "verdict 'falsified' but the evidence contains no failure signal "
                "(%s); paste the failing output, or record it as 'verified'"
                % ", ".join(repr(m) for m in NEGATIVE_MARKERS[:6])
            )

    if verdict == "not_applicable" and len(evidence.strip()) < 5:
        raise LedgerError(
            "verdict 'not_applicable' needs --evidence to say why no check is needed"
        )


def _first_external(low_claim: str):
    for pattern in EXTERNAL_PATTERNS:
        match = re.search(pattern, low_claim)
        if match:
            return match.group(0)
    return None


def cmd_log(args) -> int:
    state = load(args.file)
    claim = " ".join((args.claim or "").split())
    if len(claim) < 10:
        raise LedgerError(
            "claim is too short to be checkable (%r); state the actual assertion"
            % claim
        )
    verdict = normalize_verdict(args.verdict)
    evidence = " ".join((args.evidence or "").split())
    source = " ".join((args.source or "").split())
    if args.internal:
        if source:
            raise LedgerError("use either --source or --internal, not both")
        source = "internal:self"

    if args.check and args.check not in state["checks"]:
        raise LedgerError(
            "unknown --check %r; register it first with check-add" % args.check
        )
    _reject_uncheckable_evidence(claim, verdict, evidence, source)

    cid = claim_id(state["task"], claim)
    existing = state["records"].get(cid)
    entry = {
        "id": cid,
        "claim": claim,
        "verdict": verdict,
        "evidence": evidence,
        "source": source,
        "check": args.check or "",
        "updated": now_iso(),
        "revision": 1,
        "history": [],
    }
    if existing:
        entry["revision"] = int(existing.get("revision", 1)) + 1
        entry["history"] = list(existing.get("history", [])) + [{
            "verdict": existing.get("verdict"),
            "evidence": existing.get("evidence", ""),
            "source": existing.get("source", ""),
            "at": existing.get("updated", ""),
        }]
        if existing.get("verdict") == verdict and existing.get("evidence") == evidence:
            emit({"ok": True, "id": cid, "unchanged": True,
                  "lines": ["%s already recorded with this verdict and evidence" % cid]},
                 args.json)
            return 0
    state["records"][cid] = entry
    state["log"].append({"at": entry["updated"], "id": cid, "verdict": verdict,
                         "revision": entry["revision"]})
    state["updated"] = now_iso()
    save(args.file, state)
    emit({"ok": True, "id": cid, "verdict": verdict, "revision": entry["revision"],
          "lines": ["%s [%s] %s" % (cid, verdict, claim[:72])]}, args.json)
    return 0


def cmd_supersede(args) -> int:
    """Withdraw a claim that was worded wrong.

    Claim ids are derived from the claim text, so correcting a typo or a bad
    path creates a *new* record and leaves the wrong one behind as a permanent
    audit finding. This marks the old record withdrawn and points at the
    replacement, so the finding disappears without deleting the history.
    """
    state = load(args.file)
    old_id = (args.id or "").strip()
    new_id = (args.by or "").strip()
    if old_id not in state["records"]:
        raise LedgerError(
            "no claim %r in this ledger; run 'inspect' to see the ids" % old_id
        )
    if not args.reason.strip():
        raise LedgerError(
            "--reason is required so the withdrawal is auditable, not silent"
        )
    if new_id:
        if new_id not in state["records"]:
            raise LedgerError(
                "replacement claim %r is not in this ledger; log it first with "
                "'log', then supersede" % new_id
            )
        if new_id == old_id:
            raise LedgerError("a claim cannot supersede itself")

    rec = state["records"][old_id]
    rec["superseded_by"] = new_id
    rec["supersede_reason"] = args.reason.strip()
    rec["superseded_at"] = now_iso()
    state["updated"] = now_iso()
    state["log"].append({"at": state["updated"], "id": old_id,
                         "verdict": "superseded", "revision": rec.get("revision", 1)})
    save(args.file, state)
    emit({"ok": True, "id": old_id, "superseded_by": new_id,
          "lines": ["%s withdrawn%s" % (
              old_id, (" in favour of %s" % new_id) if new_id else "")]},
         args.json)
    return 0


def _findings(state: dict, root: str | None) -> list:
    findings = []
    records = state["records"]
    evidence_use = {}

    for cid in sorted(records):
        rec = records[cid]
        if "supersede_reason" in rec:
            continue  # withdrawn: the history stays, the findings do not
        claim = rec.get("claim", "")
        verdict = rec.get("verdict", "unchecked")
        evidence = rec.get("evidence", "")
        source = rec.get("source", "")
        low_claim = claim.lower()
        low_src = source.lower()

        if verdict == "unchecked":
            findings.append({
                "code": "W001", "level": "warn", "id": cid,
                "message": "never resolved: no check was ever run for this claim",
                "claim": claim,
            })
            continue

        external = _first_external(low_claim)
        self_sourced = (not source) or any(m in low_src for m in INTERNAL_SOURCES)
        if external and self_sourced and verdict == "verified":
            findings.append({
                "code": "W002", "level": "warn", "id": cid,
                "message": "external-world claim (matched %r) verified only from "
                           "self-reasoning (%s)" % (external, source or "no source"),
                "claim": claim,
            })

        if verdict == "verified" and len(evidence) < max(20, len(claim) // 6):
            findings.append({
                "code": "W003", "level": "info", "id": cid,
                "message": "evidence much thinner than the claim it is meant to "
                           "carry (%d chars of evidence for %d chars of claim)"
                           % (len(evidence), len(claim)),
                "claim": claim,
            })

        if verdict == "verified" and evidence:
            evidence_use.setdefault(normalize_claim(evidence), []).append(cid)

        cited = set(FILE_TOKEN_RE.findall(claim)) | set(FILE_TOKEN_RE.findall(evidence))
        for path_fragment in sorted(cited):
            if root and not _path_exists(root, path_fragment):
                findings.append({
                    "code": "W004", "level": "warn", "id": cid,
                    "message": "cites %r which does not exist under %s"
                               % (path_fragment, root),
                    "claim": claim,
                })

    for text, ids in evidence_use.items():
        if len(ids) > 2:
            findings.append({
                "code": "W005", "level": "warn", "id": ",".join(ids),
                "message": "the same evidence text carries %d separate 'verified' "
                           "claims (possible rubber stamp)" % len(ids),
                "claim": "",
            })
    return findings


def _path_exists(root: str, path_fragment: str) -> bool:
    # Strip a literal leading "./" only. str.lstrip("./") removes any leading run
    # of dots and slashes, which mangles dot-directories: ".self-audit/x.json"
    # would become "self-audit/x.json" and be reported as a missing file.
    cleaned = path_fragment.replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return os.path.exists(os.path.join(root, cleaned))


def cmd_audit(args) -> int:
    state = load(args.file)
    findings = _findings(state, args.root)
    counts = {}
    for rec in state["records"].values():
        counts[rec.get("verdict")] = counts.get(rec.get("verdict"), 0) + 1
    payload = {
        "ok": not findings,
        "total_claims": len(state["records"]),
        "counts": counts,
        "findings": findings,
        "lines": [],
    }
    if not findings:
        payload["lines"].append(
            "audit clean: %d claims, %s" % (len(state["records"]), counts or "none recorded")
        )
    else:
        payload["lines"].append(
            "audit: %d finding(s) across %d claims (%s)"
            % (len(findings), len(state["records"]), counts or "none recorded")
        )
        for item in findings:
            payload["lines"].append(
                "  %s [%s] %s :: %s" % (item["code"], item["level"], item["id"],
                                        item["message"])
            )
    emit(payload, args.json)
    return 1 if findings else 0


def cmd_report(args) -> int:
    state = load(args.file)
    findings = _findings(state, args.root)
    records = state["records"]
    order = {"verified": 0, "falsified": 1, "unchecked": 2, "not_applicable": 3}
    lines = []
    lines.append("# Claim ledger: %s" % (state.get("task") or "unnamed task"))
    lines.append("")
    lines.append("Generated %s. %d claim(s) on record."
                 % (now_iso(), len(records)))
    lines.append("")
    counts = {}
    for rec in records.values():
        counts[rec.get("verdict")] = counts.get(rec.get("verdict"), 0) + 1
    lines.append("| verdict | claims |")
    lines.append("| --- | --- |")
    for verdict in VERDICTS:
        lines.append("| %s | %d |" % (verdict, counts.get(verdict, 0)))
    lines.append("")

    for verdict in VERDICTS:
        group = [records[c] for c in sorted(records)
                 if records[c].get("verdict") == verdict
                 and "supersede_reason" not in records[c]]
        if not group:
            continue
        lines.append("## %s (%d)" % (verdict, len(group)))
        lines.append("")
        for rec in sorted(group, key=lambda r: order.get(r.get("verdict"), 9)):
            lines.append("- **%s** — %s" % (rec["id"], rec.get("claim", "")))
            if rec.get("source"):
                lines.append("  - source: `%s`" % rec["source"])
            if rec.get("evidence"):
                lines.append("  - evidence: %s" % rec["evidence"])
            if rec.get("check"):
                lines.append("  - check: %s" % rec["check"])
            if rec.get("revision", 1) > 1:
                lines.append("  - revision %d (earlier verdicts kept in the ledger)"
                             % rec["revision"])
        lines.append("")

    lines.append("## Mechanical audit")
    lines.append("")
    if not findings:
        lines.append("No findings. Every recorded claim carries a verdict, and "
                     "nothing self-verified was counted as checked.")
    else:
        for item in findings:
            lines.append("- `%s` %s: %s" % (item["code"], item["id"], item["message"]))
    lines.append("")
    withdrawn = [records[c] for c in sorted(records)
                 if "supersede_reason" in records[c]]
    if withdrawn:
        lines.append("## Withdrawn claims (kept for the record)")
        lines.append("")
        for rec in withdrawn:
            lines.append("- **%s** — %s" % (rec["id"], rec.get("claim", "")))
            lines.append("  - withdrawn because: %s" % rec.get("supersede_reason", ""))
            if rec.get("superseded_by"):
                lines.append("  - replaced by: %s" % rec["superseded_by"])
        lines.append("")
    unresolved = [records[c]["claim"] for c in sorted(records)
                  if records[c].get("verdict") == "unchecked"
                  and "supersede_reason" not in records[c]]
    lines.append("## Unproven claims (do not describe as verified)")
    lines.append("")
    if unresolved:
        for claim in unresolved:
            lines.append("- %s" % claim)
    else:
        lines.append("- none")
    text = "\n".join(lines) + "\n"

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        emit({"ok": True, "out": args.out, "findings": len(findings),
              "lines": ["wrote %s" % args.out]}, args.json)
    else:
        sys.stdout.write(text)
    return 0


def cmd_inspect(args) -> int:
    state = load(args.file)
    counts = {}
    for rec in state["records"].values():
        counts[rec.get("verdict")] = counts.get(rec.get("verdict"), 0) + 1
    lines = ["ledger %s" % args.file,
             "task: %s" % (state.get("task") or "unnamed"),
             "claims: %d  %s" % (len(state["records"]), counts or "none"),
             "checks registered: %d" % len(state["checks"])]
    for cid in sorted(state["records"]):
        rec = state["records"][cid]
        marker = " [withdrawn]" if "supersede_reason" in rec else ""
        lines.append("  %s [%s]%s %s" % (cid, rec.get("verdict"), marker,
                                         rec.get("claim", "")))
    emit({"ok": True, "counts": counts, "checks": len(state["checks"]),
          "task": state.get("task", ""), "lines": lines}, args.json)
    return 0


# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-ledger",
        description="Record what an agent claims, how it was checked, and flag "
                    "the claims nobody ever checked.",
    )
    parser.add_argument("--file", default=DEFAULT_LEDGER,
                        help="ledger path (default: %s)" % DEFAULT_LEDGER)
    parser.add_argument("--json", action="store_true", help="machine-readable output")

    # The same two switches must also be accepted *after* the subcommand, which is
    # where a caller naturally writes them. SUPPRESS keeps the sub-level copy from
    # overwriting a value already given before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--file", default=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("init", parents=[common], help="create an empty ledger")
    p.add_argument("--task", default="", help="name of the task this ledger covers")
    p.add_argument("--force", action="store_true", help="overwrite an existing ledger")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("check-add", parents=[common],
                       help="register a way to check something")
    p.add_argument("--id", required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--method", required=True,
                   help="how the check is carried out, concretely")
    p.add_argument("--command", default="", help="exact command, if any")
    p.set_defaults(func=cmd_check_add)

    p = sub.add_parser("log", parents=[common],
                       help="record one claim with its verdict")
    p.add_argument("--claim", required=True)
    p.add_argument("--verdict", required=True,
                   help="one of: %s" % ", ".join(VERDICTS))
    p.add_argument("--evidence", default="",
                   help="what the check actually produced (paste real output)")
    p.add_argument("--source", default="",
                   help="where the check came from: command, file, URL")
    p.add_argument("--internal", action="store_true",
                   help="declare the check was your own reasoning")
    p.add_argument("--check", default="", help="id of a registered check")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("audit", parents=[common],
                       help="flag unresolved and unsupported claims")
    p.add_argument("--root", default=None,
                   help="workspace root, to verify cited file paths")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("report", parents=[common],
                       help="render the ledger as a report")
    p.add_argument("--out", default="", help="write markdown to this path")
    p.add_argument("--root", default=None)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("supersede", parents=[common],
                       help="withdraw a mis-worded claim, keeping its history")
    p.add_argument("--id", required=True, help="id of the claim to withdraw")
    p.add_argument("--by", default="",
                   help="id of the corrected replacement claim, if any")
    p.add_argument("--reason", required=True,
                   help="why the claim is being withdrawn")
    p.set_defaults(func=cmd_supersede)

    p = sub.add_parser("inspect", parents=[common],
                       help="list every recorded claim")
    p.set_defaults(func=cmd_inspect)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except LedgerError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except OSError as exc:
        # Never dump a traceback at the caller: report which path failed.
        print("error: cannot use %s: %s" % (args.file, exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
