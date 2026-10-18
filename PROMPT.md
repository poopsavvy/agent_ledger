# Drop-in prompt: make an agent use agent-ledger

Copy everything inside the fence below and paste it as the first message to any
coding agent that has shell access to the project. It assumes `agent-ledger`
has been added to the project; it does not assume any particular framework or
model.

Works as-is if the two files sit at `tools/agent-ledger/`. If you put them
somewhere else, change the `LEDGER` path on the `SETUP` line only — every later
command reuses that variable.

---

````text
You have a tool in this project called agent-ledger. It exists to stop you from
reporting things you never actually checked. Use it for this task.

SETUP — first, locate the tool and set one shell variable:

  # adjust the first path if the tool lives somewhere else in this project
  LEDGER=tools/agent-ledger/ledger.py
  STATE=.claims/ledger.json

  python "$LEDGER" init --file "$STATE" --task "<one-line description of this task>"

Then, before you start work, register what you intend to use as checks. Do this
for every distinct way you plan to check something:

  python "$LEDGER" check-add --file "$STATE" --id T1 \
    --question "does the test suite pass" \
    --method "run it and read the tail of the output" \
    --command "<the exact command you will run>"

RULES OF ENGAGEMENT — these are not optional:

1. The moment you are about to state something as fact — in your reasoning, in
   a message to the user, or in a file you write — log it:
     python "$LEDGER" log --file "$STATE" \
       --claim "<the assertion, stated so it could be proven false>" \
       --verdict <unchecked|verified|falsified|not_applicable> \
       --evidence "<what the check actually produced>" \
       --source "<the command you ran, the file you read, the URL you fetched>"

2. If you cannot name the source, the verdict is `unchecked`. Log it and move
   on. Do not skip the claim just because it is unverified — the point is to
   keep a record of it, not to hide it.

3. `verified` requires real evidence: paste what the command actually printed,
   not a summary of what you expect it to print. The tool rejects evidence that
   reads like no check happened ("should work", "looks correct", "not run").

4. Never upgrade an `unchecked` claim by thinking harder about it. Re-read,
   re-run, or leave it `unchecked`. Reasoning about a fact you could check is
   not checking it.

5. When the check really was your own reasoning and cannot be anything else
   (e.g. reading a code path in the editor), say so explicitly with
   `--internal` instead of inventing a source.

6. A claim about the outside world cannot be verified from your own reasoning.
   If it contains a version number, a date, a percentage, a URL, or words like
   "latest", "current", "according to", "supports", or "requires", the tool
   will REFUSE `--internal` and exit 2. That is intended. Either go check it or
   log it as `unchecked`.

7. If a check contradicts you, log it as `falsified` with the failing output as
   the evidence. Fix the thing, then log the corrected claim. Do not delete or
   rewrite the falsified record — the history is the point.

8. Before reporting back, run:
     python "$LEDGER" audit --file "$STATE" --root .
   Exit code 1 means there are findings. Read every line. Then:
     python "$LEDGER" report --file "$STATE" --out .claims/CLAIMS.md --root .
   Include that report in what you hand back.

HOW TO READ THE AUDIT OUTPUT (do not skip this):

  W001  a claim was never resolved. This is your to-do list. Either check it or
        mark it `not_applicable` with a reason.
  W002  an external-world fact was "verified" from self-reasoning. Treat as a
        lie you told yourself. Go check it.
  W003  the evidence is much thinner than the claim it is meant to carry.
  W004  the claim cites a file that does not exist. Either the path is wrong or
        the claim is. Never edit the claim to match the finding without first
        confirming the finding is right.
  W005  one evidence string is carrying three or more claims. That is a rubber
        stamp. Each claim needs its own evidence.

IMPORTANT: if a guard rejects your `log` command, that is the tool working.
Read the error, fix the claim, and re-run. Do not bypass it by editing the
ledger JSON directly, and do not weaken your claim into something trivially
true just to make it pass.

FINISH BY REPORTING EXACTLY THIS:

  - What I verified, and the command/output that verified it
  - What is still `unchecked`, listed explicitly, with why each could not be
    checked now
  - What I could not check because it needs something I do not have access to
  - The audit exit code

Do not describe an `unchecked` claim as verified, and do not omit it. An honest
list of open questions is worth more than a clean-looking ledger.

IF YOU WORDED A CLAIM WRONG — a typo in a file path, a claim that turned out to
be about something else — do not leave it behind as a permanent audit finding,
and do not delete it by editing the ledger JSON. Withdraw it properly:

  python "$LEDGER" log --file "$STATE" --claim "<corrected claim>" ...
  python "$LEDGER" supersede --file "$STATE" --id <old-id> --by <new-id> \
    --reason "<what was wrong with the old wording>"

A withdrawn claim keeps its history and stops producing findings.

One more thing: if you find that agent-ledger itself is broken — a crash, a
wrong finding, an exit code that does not match this description — say so in
your report rather than working around it silently. Record any workaround you
take as its own `unchecked` claim.
````

---

## Why it is built this way

- **It hands the agent the vocabulary instead of inventing one.** The four
  verdicts are enforced by the tool, so the prompt only has to explain intent.
- **It names the exact failure modes it is trying to prevent**, including the
  two the guards mechanically block, with the error text the agent will see.
  An agent that has been told what the refusal looks like does not treat it as
  a bug to route around.
- **Every command line in it was dry-run before being written down.** The prompt
  as shipped was executed end to end in a scratch ledger, including the
  `--file`-after-subcommand ordering it uses throughout.
- **The closing report contract is the real payload.** It forces the agent to
  present unproven work next to proven work, in the same message, which is the
  behaviour the whole tool exists to produce.

## A shorter version, if you only have one paragraph

````text
Before you start: python tools/agent-ledger/ledger.py init --file .claims/ledger.json
--task "<task>". Log every factual assertion you make with `log --claim ... --verdict
unchecked|verified|falsified|not_applicable --evidence ... --source ...`. If you cannot
name a source, use `unchecked`; never upgrade it by re-reasoning. Claims about the
outside world (versions, dates, percentages, URLs, "latest", "according to") cannot be
verified with --internal — the tool will refuse. Before replying, run `audit --root .`
and read every finding; W001 items are your to-do list. Then `report --out .claims/CLAIMS.md`
and include it. In your reply, list what you verified with the command that proved it, and
separately list everything still `unchecked`. Do not present unchecked claims as verified.
If a guard rejects a log command, fix the claim — do not edit the JSON or reword the claim
to be trivially true.
````

## Adapting it

- **Non-shell agents** (an agent with only file tools, no terminal): drop the
  shell snippets and keep the RULES and the closing report contract. The
  `--internal` and refusal semantics still apply; the agent just runs the
  commands through whatever execution path it has.
- **Tightening it**: add a line requiring `--root .` be a specific subdirectory,
  or require `check-add` for every `--check` id used, so claims link to declared
  methods.
- **Loosening it for small tasks**: the one-paragraph version above is enough,
  but keep rule 2 (log `unchecked` rather than omit) and the closing report —
  those two carry most of the value.
