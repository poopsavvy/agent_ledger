#!/usr/bin/env python3
"""Behavioral tests for agent-ledger.

Run:  python tools/agent-ledger/test_ledger.py
The tests target the part that matters: the tool must refuse to let an agent
record a check it never performed, and the audit must surface claims nobody
resolved.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ledger  # noqa: E402


def run_capture(argv):
    """Run the CLI in-process; return (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ledger.main(argv)
    return code, out.getvalue(), err.getvalue()


class LedgerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = os.path.join(self.tmp.name, "ledger.json")
        code, _, err = run_capture(["--file", self.file, "init", "--task", "unit test"])
        self.assertEqual(code, 0, err)

    def tearDown(self):
        self.tmp.cleanup()

    def log(self, *extra):
        return run_capture(["--file", self.file, "log", *extra])


class TestInit(LedgerTestCase):
    def test_init_creates_schema_v1_ledger(self):
        with open(self.file, "r", encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertEqual(state["schema_version"], 1)
        self.assertEqual(state["task"], "unit test")
        self.assertEqual(state["records"], {})

    def test_init_refuses_to_clobber_without_force(self):
        code, _, err = run_capture(["--file", self.file, "init"])
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)

    def test_force_resets(self):
        code, _, err = run_capture(["--file", self.file, "init", "--force"])
        self.assertEqual(code, 0, err)

    def test_missing_ledger_is_an_error_not_a_crash(self):
        code, _, err = run_capture(["--file", os.path.join(self.tmp.name, "nope.json"),
                                    "audit"])
        self.assertEqual(code, 2)
        self.assertIn("no ledger", err)

    def test_unsupported_schema_fails_closed(self):
        with open(self.file, "w", encoding="utf-8") as fh:
            json.dump({"schema_version": 99, "records": {}}, fh)
        code, _, err = run_capture(["--file", self.file, "inspect"])
        self.assertEqual(code, 2)
        self.assertIn("schema_version", err)

    def test_nested_ledger_path_is_created(self):
        nested = os.path.join(self.tmp.name, "deep", "nested", "ledger.json")
        code, _, err = run_capture(["--file", nested, "init", "--task", "nested"])
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(nested))
        code, _, err = run_capture(["--file", nested, "log",
                                    "--claim", "nested paths are writable",
                                    "--verdict", "unchecked"])
        self.assertEqual(code, 0, err)

    def test_unwritable_path_reports_instead_of_traceback(self):
        target = os.path.join(self.tmp.name, "a_file")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("not a directory")
        code, out, err = run_capture(["--file", os.path.join(target, "ledger.json"),
                                      "init"])
        self.assertEqual(code, 2)
        self.assertIn("cannot use", err)
        self.assertNotIn("Traceback", err)

    def test_corrupt_json_fails_closed(self):
        with open(self.file, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        code, _, err = run_capture(["--file", self.file, "inspect"])
        self.assertEqual(code, 2)
        self.assertIn("not valid JSON", err)


class TestRefusesFakeVerification(LedgerTestCase):
    """The core mechanic: an unchecked assertion cannot be logged as checked."""

    def test_verified_without_source_is_refused(self):
        code, _, err = self.log("--claim", "the parser handles nested quoting",
                                "--verdict", "verified",
                                "--evidence", "ran the suite, 14 tests pass")
        self.assertEqual(code, 2)
        self.assertIn("needs --source", err)

    def test_verified_without_evidence_is_refused(self):
        code, _, err = self.log("--claim", "the parser handles nested quoting",
                                "--verdict", "verified",
                                "--source", "python -m unittest")
        self.assertEqual(code, 2)
        self.assertIn("needs --evidence", err)

    def test_uninformative_evidence_is_refused(self):
        code, _, err = self.log("--claim", "the parser handles nested quoting",
                                "--verdict", "verified", "--evidence", "ok",
                                "--source", "python -m unittest")
        self.assertEqual(code, 2)
        self.assertIn("too short", err)

    def test_evidence_admitting_no_check_is_refused(self):
        code, _, err = self.log("--claim", "the parser handles nested quoting",
                                "--verdict", "verified",
                                "--evidence", "should work, looks correct",
                                "--source", "python -m unittest")
        self.assertEqual(code, 2)
        self.assertIn("skipped", err)

    def test_evidence_quoting_real_output_with_a_marker_word_is_accepted(self):
        # Regression: the marker scan fired on genuine pasted output that happened
        # to contain a marker phrase, refusing a real check.
        code, _, err = self.log(
            "--claim", "the external-fact guard blocks self-verification",
            "--verdict", "verified",
            "--evidence",
            "error: this claim looks like an external-world fact (matched '3.1.0') "
            "but the source is 'internal:self'; error: evidence reads like the check "
            "was skipped ('should work'); both runs exited with code 2",
            "--source", "python ledger.py log --verdict verified")
        self.assertEqual(code, 0, err)

    def test_marker_word_without_any_run_signal_is_still_refused(self):
        code, _, err = self.log(
            "--claim", "the retry backoff is respected under load",
            "--verdict", "verified",
            "--evidence", "presumably fine, it should work in practice",
            "--source", "python -m pytest")
        self.assertEqual(code, 2)
        self.assertIn("skipped", err)

    def test_exited_with_code_phrasing_counts_as_a_run_signal(self):
        # Regression: the first version of the run-signal regex only matched
        # "exit 2", not "exited with code 2".
        code, _, err = self.log(
            "--claim", "the guard rejects a self-verified external fact",
            "--verdict", "verified",
            "--evidence", "the tool should work around this, but instead it "
                          "refused the record and exited with code 2",
            "--source", "python ledger.py log")
        self.assertEqual(code, 0, err)

    def test_aliases_map_onto_the_fixed_vocabulary(self):
        code, out, err = self.log("--claim", "the README documents the exit codes",
                                  "--verdict", "TRUE",
                                  "--evidence", "read README.md, exit codes section present",
                                  "--source", "README.md")
        self.assertEqual(code, 0, err)
        self.assertIn("[verified]", out)

    def test_external_fact_verified_from_self_reasoning_is_refused(self):
        code, _, err = self.log(
            "--claim", "the library's latest version is 2.4.1",
            "--verdict", "verified",
            "--evidence", "that is the version I remember being current",
            "--internal")
        self.assertEqual(code, 2)
        self.assertIn("your own reasoning", err)

    def test_external_percentage_from_self_reasoning_is_refused(self):
        code, _, err = self.log(
            "--claim", "roughly 80% of retries succeed on the second attempt",
            "--verdict", "verified",
            "--evidence", "in my experience this is the usual rate",
            "--internal")
        self.assertEqual(code, 2)
        self.assertIn("your own reasoning", err)

    def test_internal_reasoning_is_allowed_when_claim_is_not_external(self):
        code, _, err = self.log(
            "--claim", "the retry loop never closes its file handle",
            "--verdict", "verified",
            "--evidence", "walked the code path in the editor: the open() result is "
                          "never bound to a name that gets closed",
            "--internal")
        self.assertEqual(code, 0, err)

    def test_falsified_without_failure_signal_is_refused(self):
        code, _, err = self.log(
            "--claim", "the cache is invalidated on write",
            "--verdict", "falsified",
            "--evidence", "the cache looks fine to me",
            "--source", "read cache.py")
        self.assertEqual(code, 2)
        self.assertIn("no failure signal", err)

    def test_falsified_with_real_failure_output_is_accepted(self):
        code, _, err = self.log(
            "--claim", "the cache is invalidated on write",
            "--verdict", "falsified",
            "--evidence", "test_write_after_evict FAILED: assertion 0 != 1, exit code 1",
            "--source", "python -m pytest tests/test_cache.py")
        self.assertEqual(code, 0, err)

    def test_not_applicable_needs_a_reason(self):
        code, _, err = self.log("--claim", "the moon is made of cheese",
                                "--verdict", "not_applicable")
        self.assertEqual(code, 2)
        self.assertIn("why no check is needed", err)

    def test_one_word_claim_is_refused(self):
        code, _, err = self.log("--claim", "fine", "--verdict", "unchecked")
        self.assertEqual(code, 2)
        self.assertIn("too short", err)

    def test_unknown_verdict_is_refused(self):
        code, _, err = self.log("--claim", "the build is reproducible end to end",
                                "--verdict", "pretty-sure")
        self.assertEqual(code, 2)
        self.assertIn("unknown verdict", err)

    def test_source_and_internal_are_mutually_exclusive(self):
        code, _, err = self.log("--claim", "the build is reproducible end to end",
                                "--verdict", "verified",
                                "--evidence", "reran it twice, identical hashes",
                                "--source", "make build", "--internal")
        self.assertEqual(code, 2)
        self.assertIn("not both", err)

    def test_unchecked_is_accepted_without_evidence(self):
        code, out, err = self.log("--claim", "the migration is safe to run twice",
                                  "--verdict", "unchecked")
        self.assertEqual(code, 0, err)
        self.assertIn("unchecked", out)

    def test_aliases_map_onto_the_fixed_vocabulary(self):
        code, out, err = self.log("--claim", "the README documents the exit codes",
                                  "--verdict", "TRUE",
                                  "--evidence", "read README.md, exit codes section present",
                                  "--source", "README.md")
        self.assertEqual(code, 0, err)
        self.assertIn("[verified]", out)

    def test_check_id_must_be_registered_first(self):
        code, _, err = self.log("--claim", "the build is reproducible end to end",
                                "--verdict", "unchecked", "--check", "C9")
        self.assertEqual(code, 2)
        self.assertIn("unknown --check", err)


class TestRevisionAndRoundTrip(LedgerTestCase):
    def test_revision_history_is_kept_when_a_verdict_changes(self):
        claim = "the export writes a header row"
        code, _, err = self.log("--claim", claim, "--verdict", "unchecked")
        self.assertEqual(code, 0, err)
        code, _, err = self.log("--claim", claim, "--verdict", "verified",
                                "--evidence", "ran export twice, 'a,b,c' first line both times",
                                "--source", "python export.py --sample")
        self.assertEqual(code, 0, err)
        with open(self.file, "r", encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertEqual(len(state["records"]), 1)
        rec = next(iter(state["records"].values()))
        self.assertEqual(rec["verdict"], "verified")
        self.assertEqual(rec["revision"], 2)
        self.assertEqual(rec["history"][0]["verdict"], "unchecked")

    def test_identical_repeat_log_is_a_noop(self):
        claim = "the export writes a header row"
        self.log("--claim", claim, "--verdict", "unchecked")
        code, out, err = self.log("--claim", claim, "--verdict", "unchecked")
        self.assertEqual(code, 0, err)
        self.assertIn("already recorded", out)

    def test_claim_identity_is_stable_across_whitespace(self):
        a, _, _ = self.log("--claim", "the export writes a header row",
                           "--verdict", "unchecked")
        b, _, _ = self.log("--claim", "  the   export  writes a header row ",
                           "--verdict", "unchecked")
        with open(self.file, "r", encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertEqual(len(state["records"]), 1)
        self.assertEqual(state["log"][0]["id"], state["log"][-1]["id"])

    def test_ledger_round_trips_through_disk(self):
        self.log("--claim", "the export writes a header row", "--verdict", "unchecked")
        state = ledger.load(self.file)
        ledger.save(self.file, state)
        reloaded = ledger.load(self.file)
        self.assertEqual(state["records"], reloaded["records"])


class TestAudit(LedgerTestCase):
    def test_clean_ledger_audits_clean(self):
        self.log("--claim", "the export writes a header row", "--verdict", "not_applicable",
                 "--evidence", "no export exists in this repository yet")
        code, out, err = run_capture(["--file", self.file, "audit", "--json",
                                      "--root", self.tmp.name])
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["ok"])

    def test_unchecked_claim_is_flagged_and_exit_code_is_1(self):
        self.log("--claim", "the deployment script is idempotent",
                 "--verdict", "unchecked")
        code, out, err = run_capture(["--file", self.file, "audit", "--json",
                                      "--root", self.tmp.name])
        self.assertEqual(code, 1, err)
        payload = json.loads(out)
        self.assertEqual([f["code"] for f in payload["findings"]], ["W001"])
        self.assertIn("never resolved", payload["findings"][0]["message"])

    def test_citation_of_a_missing_file_is_flagged(self):
        self.log("--claim", "ghost_module.py defines the retry policy",
                 "--verdict", "verified",
                 "--evidence", "read ghost_module.py, retry policy is there",
                 "--source", "read ghost_module.py")
        code, out, _ = run_capture(["--file", self.file, "audit", "--json",
                                    "--root", self.tmp.name])
        self.assertEqual(code, 1)
        codes = [f["code"] for f in json.loads(out)["findings"]]
        self.assertIn("W004", codes)

    def test_citation_of_a_real_file_is_not_flagged(self):
        real = os.path.join("src", "real_module.py")
        os.makedirs(os.path.join(self.tmp.name, "src"), exist_ok=True)
        with open(os.path.join(self.tmp.name, real), "w", encoding="utf-8") as fh:
            fh.write("# retry policy lives here\n")
        self.log("--claim", "src/real_module.py defines the retry policy",
                 "--verdict", "verified",
                 "--evidence", "read the file: retry policy is defined at line 1",
                 "--source", "read src/real_module.py")
        code, out, _ = run_capture(["--file", self.file, "audit", "--json",
                                    "--root", self.tmp.name])
        codes = [f["code"] for f in json.loads(out)["findings"]]
        self.assertNotIn("W004", codes)

    def test_dot_directory_citation_is_resolved(self):
        # Regression: lstrip("./") mangled ".hidden/x.json" into "hidden/x.json".
        os.makedirs(os.path.join(self.tmp.name, ".hidden"), exist_ok=True)
        with open(os.path.join(self.tmp.name, ".hidden", "x.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{}\n")
        self.log("--claim", ".hidden/x.json holds the probe result",
                 "--verdict", "verified",
                 "--evidence", "opened .hidden/x.json and it contains {}",
                 "--source", "read .hidden/x.json")
        code, out, _ = run_capture(["--file", self.file, "audit", "--json",
                                    "--root", self.tmp.name])
        codes = [f["code"] for f in json.loads(out)["findings"]]
        self.assertNotIn("W004", codes)

    def test_dot_slash_prefix_is_ignored(self):
        os.makedirs(os.path.join(self.tmp.name, "src"), exist_ok=True)
        with open(os.path.join(self.tmp.name, "src", "y.py"), "w",
                  encoding="utf-8") as fh:
            fh.write("# y\n")
        self.log("--claim", "./src/y.py exists in the tree",
                 "--verdict", "verified",
                 "--evidence", "opened ./src/y.py, one comment line",
                 "--source", "read ./src/y.py")
        code, out, _ = run_capture(["--file", self.file, "audit", "--json",
                                    "--root", self.tmp.name])
        codes = [f["code"] for f in json.loads(out)["findings"]]
        self.assertNotIn("W004", codes)

    def test_thin_evidence_is_an_info_finding_not_a_failure(self):
        # Log through the guard first, then thin the evidence on disk: this models
        # a ledger written by another tool, and pins the audit-side rule.
        claim = ("the retry wrapper preserves the original exception type and "
                 "re-raises it unchanged after exhausting every configured attempt")
        code, _, err = self.log("--claim", claim, "--verdict", "verified",
                                "--evidence", "the pytest suite passed, exit code 0",
                                "--source", "python -m pytest")
        self.assertEqual(code, 0, err)
        state = ledger.load(self.file)
        rec = next(iter(state["records"].values()))
        rec["evidence"] = "ok"
        ledger.save(self.file, state)

        code, out, _ = run_capture(["--file", self.file, "audit", "--json",
                                    "--root", self.tmp.name])
        findings = json.loads(out)["findings"]
        codes = [f["code"] for f in findings]
        self.assertIn("W003", codes)
        self.assertEqual(code, 1)
        self.assertTrue(all(f["level"] in ("warn", "info") for f in findings))

    def test_one_evidence_string_cannot_carry_many_verdicts(self):
        shared = "ran the suite once, everything green"
        for i in range(3):
            self.log("--claim", "generated claim number %d about the pipeline" % i,
                     "--verdict", "verified", "--evidence", shared,
                     "--source", "python -m pytest")
        code, out, _ = run_capture(["--file", self.file, "audit", "--json",
                                    "--root", self.tmp.name])
        self.assertEqual(code, 1)
        self.assertIn("W005", [f["code"] for f in json.loads(out)["findings"]])

    def test_self_verified_external_claim_is_flagged_even_if_it_reached_the_ledger(self):
        # Defense in depth: build the bad record directly, bypassing the log guard.
        state = ledger.load(self.file)
        state["records"]["deadbeef00"] = {
            "id": "deadbeef00", "claim": "the API supports bulk inserts",
            "verdict": "verified", "evidence": "seems standard",
            "source": "internal:self", "check": "", "revision": 1, "history": [],
            "updated": ledger.now_iso(),
        }
        ledger.save(self.file, state)
        code, out, _ = run_capture(["--file", self.file, "audit", "--json",
                                    "--root", self.tmp.name])
        self.assertEqual(code, 1)
        self.assertIn("W002", [f["code"] for f in json.loads(out)["findings"]])


class TestReport(LedgerTestCase):
    def test_report_lists_unproven_claims_separately(self):
        self.log("--claim", "the migration is safe to run twice",
                 "--verdict", "unchecked")
        self.log("--claim", "the export writes a header row", "--verdict", "verified",
                 "--evidence", "first line of output was 'a,b,c' on two runs",
                 "--source", "python export.py --sample")
        out_path = os.path.join(self.tmp.name, "report.md")
        code, out, err = run_capture(["--file", self.file, "report", "--out", out_path,
                                      "--root", self.tmp.name])
        self.assertEqual(code, 0, err)
        with open(out_path, "r", encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("## Unproven claims", text)
        self.assertIn("the migration is safe to run twice", text)
        self.assertIn("| verified | 1 |", text)
        self.assertIn("| unchecked | 1 |", text)
        # the verified claim must not appear in the unproven section
        unproven = text.split("## Unproven claims", 1)[1]
        self.assertNotIn("the export writes a header row", unproven)

    def test_report_to_stdout_has_no_exit_failure(self):
        code, out, err = run_capture(["--file", self.file, "report",
                                      "--root", self.tmp.name])
        self.assertEqual(code, 0, err)
        self.assertIn("claim(s) on record", out)


class TestInspect(LedgerTestCase):
    def test_inspect_summarizes_verdict_counts(self):
        self.log("--claim", "the export writes a header row", "--verdict", "unchecked")
        self.log("--claim", "the retry loop leaks a file handle", "--verdict", "verified",
                 "--evidence", "walked the path: open() is never closed",
                 "--internal")
        code, out, err = run_capture(["--file", self.file, "inspect", "--json"])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["counts"], {"unchecked": 1, "verified": 1})

    def test_no_command_prints_help_and_exits_2(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = ledger.main([])
        self.assertEqual(code, 2)
        self.assertIn("agent-ledger", out.getvalue())


class TestSupersede(LedgerTestCase):
    """Withdrawing a mis-worded claim must clear its findings without erasing it."""

    def _only_id(self):
        with open(self.file, "r", encoding="utf-8") as fh:
            return next(iter(json.load(fh)["records"]))

    def test_withdrawn_claim_stops_producing_findings(self):
        code, _, err = self.log("--claim", "ghost.py holds the retry policy",
                                "--verdict", "verified",
                                "--evidence", "opened the file, policy is at line 3",
                                "--source", "read ghost.py")
        self.assertEqual(code, 0, err)
        code, out, _ = run_capture(["--file", self.file, "audit", "--json",
                                    "--root", self.tmp.name])
        self.assertIn("W004", [f["code"] for f in json.loads(out)["findings"]])

        code, _, err = run_capture(["--file", self.file, "supersede",
                                    "--id", self._only_id(),
                                    "--reason", "file name was a typo; it is real.py"])
        self.assertEqual(code, 0, err)
        code, out, _ = run_capture(["--file", self.file, "audit", "--json",
                                    "--root", self.tmp.name])
        self.assertEqual([f["code"] for f in json.loads(out)["findings"]], [])
        self.assertEqual(code, 0)

    def test_withdrawn_claim_is_absent_from_the_unproven_list(self):
        self.log("--claim", "the old endpoint still accepts v1 payloads",
                 "--verdict", "unchecked")
        run_capture(["--file", self.file, "supersede", "--id", self._only_id(),
                     "--reason", "the v1 endpoint was deleted last year"])
        out_path = os.path.join(self.tmp.name, "r.md")
        run_capture(["--file", self.file, "report", "--out", out_path,
                     "--root", self.tmp.name])
        with open(out_path, "r", encoding="utf-8") as fh:
            text = fh.read()
        unproven = text.split("## Unproven claims", 1)[1]
        self.assertNotIn("old endpoint still accepts", unproven)
        self.assertIn("Withdrawn claims", text)
        self.assertIn("v1 endpoint was deleted", text)

    def test_supersede_requires_a_reason(self):
        self.log("--claim", "the queue drains in under a second", "--verdict", "unchecked")
        code, _, err = run_capture(["--file", self.file, "supersede",
                                    "--id", self._only_id(), "--reason", "   "])
        self.assertEqual(code, 2)
        self.assertIn("--reason is required", err)

    def test_supersede_rejects_unknown_ids(self):
        code, _, err = run_capture(["--file", self.file, "supersede", "--id", "nope",
                                    "--reason", "because"])
        self.assertEqual(code, 2)
        self.assertIn("no claim", err)

    def test_supersede_points_at_the_replacement(self):
        self.log("--claim", "ghost.py holds the retry policy", "--verdict", "unchecked")
        self.log("--claim", "real.py holds the retry policy", "--verdict", "unchecked")
        with open(self.file, "r", encoding="utf-8") as fh:
            ids = sorted(json.load(fh)["records"])
        code, _, err = run_capture(["--file", self.file, "supersede", "--id", ids[0],
                                    "--by", ids[1], "--reason", "typo in the file name"])
        self.assertEqual(code, 0, err)
        state = ledger.load(self.file)
        self.assertEqual(state["records"][ids[0]]["superseded_by"], ids[1])
        code, _, err = run_capture(["--file", self.file, "supersede", "--id", ids[0],
                                    "--by", "deadbeef00", "--reason", "x"])
        self.assertEqual(code, 2)
        self.assertIn("not in this ledger", err)


class TestHelpers(unittest.TestCase):
    def test_normalize_verdict_rejects_junk(self):
        with self.assertRaises(ledger.LedgerError):
            ledger.normalize_verdict("mostly-true")

    def test_claim_id_is_stable_and_case_insensitive(self):
        a = ledger.claim_id("t", "The Build Is Reproducible")
        b = ledger.claim_id("t", "the build is   reproducible")
        self.assertEqual(a, b)
        self.assertNotEqual(a, ledger.claim_id("other task", "the build is reproducible"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
