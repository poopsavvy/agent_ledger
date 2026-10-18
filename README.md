# agent-ledger

A claim ledger for AI agents.

An agent's most expensive failure is not being wrong. It is being confidently
*unchecked* — asserting a thing it never verified, in exactly the same tone it
uses for a thing it did verify. `agent-ledger` makes the difference between
those two visible, and then refuses to let an agent paper over it.

## The idea in one line

Every claim gets a verdict. A claim may only call itself `verified` if it can
name where the check came from, and a claim about the outside world may not be
verified by the agent's own reasoning.

## Verdict vocabulary

Exactly four, and you must pick one. Do not invent more.

| verdict | means | requires |
| --- | --- | --- |
| `unchecked` | asserted, nobody has looked yet | nothing |
| `verified` | a check was run or read that would have contradicted it, and did not | `--evidence` and (`--source` or `--internal`) |
| `falsified` | a check contradicted it | `--evidence` containing a real failure signal |
| `not_applicable` | this claim does not need a check | `--evidence` saying why |

Common words are mapped onto these four, so `true`/`supported`/`pass` become
`verified`, `false`/`refuted` become `falsified`, and so on.

## The three guards

1. **No source, no `verified`.** A `verified` verdict with no `--source` is
   refused. If the check really was your own reasoning, you must say so out
   loud with `--internal`, and that declaration is recorded as the source.

2. **External facts cannot be self-verified.** If the claim looks like a fact
   about the world — it contains a version number, a percentage, a year, a URL,
   or words like *latest*, *current*, *according to*, *supports*, *requires* —
   then `--internal` is refused. You either go check it or you record it as
   `unchecked`. This is the guard that has teeth.

3. **`falsified` needs a failure.** The evidence must actually read like a
   failure report — *failed*, *error*, *mismatch*, *0 matches*, *traceback*,
   exit code 1, and similar. "It looked wrong when I skimmed it" is not a
   falsification.

Evidence that admits no check happened — *should work*, *looks correct*,
*not run* — is rejected outright under any verdict.

## Install

No dependencies, no install. Python 3.9+ standard library only.

```bash
python ledger.py --help
```

## Use

```bash
# 1. open a ledger for the task
python ledger.py init --task "migrate billing service"

# 2. register how you intend to check things (optional, but it links claims to methods)
python ledger.py check-add --id T1 \
  --question "does the suite pass" \
  --method "run it and read the tail" \
  --command "python -m pytest"

# 3. record claims as you work
python ledger.py log \
  --claim "the export writes a header row" \
  --verdict verified \
  --evidence "first line was a,b,c on two consecutive runs" \
  --source "python export.py --sample" --check T1

python ledger.py log \
  --claim "the migration is safe to run twice" \
  --verdict unchecked

# 4. ask what is still unsupported
python ledger.py audit --root .
python ledger.py report --out CLAIMS.md --root .

# 5. if you worded a claim wrong, withdraw it — do not leave a zombie finding
python ledger.py supersede --id 561cfa57a3 --by 1728f2dd56 \
  --reason "cited a bare filename; the real path is tools/agent-ledger/PROMPT.md"
```

`--file` and `--json` work both before and after the subcommand.

### Exit codes

| code | meaning |
| --- | --- |
| 0 | ok, or audit found nothing |
| 1 | audit found findings |
| 2 | usage error, bad verdict, or a refused write |

Exit code 1 is deliberate: it makes the tool usable as a gate. If your agent
hands work back with unresolved claims, the check fails.

## The audit

`audit` re-reads the ledger and reports mechanically:

| code | finding |
| --- | --- |
| `W001` | claim never resolved — no check was ever run |
| `W002` | external-world claim verified only from self-reasoning |
| `W003` | evidence much thinner than the claim it is meant to carry |
| `W004` | claim or evidence cites a file that does not exist under `--root` |
| `W005` | one evidence string rubber-stamping three or more claims |

Claims withdrawn with `supersede` keep their history and their reason, but stop
producing findings — so a typo cannot leave a permanent false alarm in the audit.

`W001` is the one that matters most: it is the honest column of everything the
agent asserted and never checked.

## What it stores

A single JSON ledger. Claims are keyed by a hash of the task plus normalized
claim text, so re-logging the same claim is a no-op and changing a verdict
keeps the earlier one in `history`. Writes go through a temp file and
`os.replace`, so an interrupted write cannot leave a half-written ledger.
Missing parent directories are created on demand.

## Tests

```bash
python test_ledger.py
```

51 behavioral tests, all passing. They cover the guards (each refusal path has
its own test), revision history, claim identity, dot-directory path handling,
withdrawal, and the audit rules. Run them with `-v` to see the refusal cases by
name.

## Applying it to yourself

The point of the tool is that you turn it on your own work. The pattern:

1. `init` a ledger for the task.
2. Log a claim the moment you would otherwise state it as fact.
3. If you cannot name a source, log it `unchecked`. Do not upgrade it later by
   remembering harder.
4. Run `audit` before you report back, and read `W001` as your to-do list.
5. Hand over the report. Say what is unproven instead of hoping nobody asks.

This repository's own build was recorded this way; see `SELF-AUDIT.md` for the
ledger, the two claims that stayed unresolved, and the three real bugs that
applying the tool to its own build surfaced.

## The drop-in prompt

`PROMPT.md` contains a paste-ready instruction block for other agents, plus a
one-paragraph short version. It hands the agent the verdict vocabulary, tells it
exactly what the guards will refuse and why, requires it to list everything
still `unchecked` when it reports back, and tells it to report a broken tool
rather than silently working around one.

```bash
# paste-ready version lives in PROMPT.md
python ledger.py --help
```
