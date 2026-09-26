# Failures, Incidents & the Guards They Built

Live log of known failures and the permanent guards that prevent recurrence.
**A known failure without a written guard is not "learned from"** — it is a waiting
repeat. Every entry names the failure, the root cause, and the guard that now makes the
tap physically/mechanically impossible to reopen (a failing test, a blocking hook, a
fail-closed path).

Follow-on lesson: *a passing test proves behavior, not that the code you pushed still has
it.* A guard that reverted silently once (see #004) is why pushes re-run the gates.

---

## #004 — "Closed" safety gate silently reverted by a stray `git checkout`

- **When / guard:** added to this log 2026-09-25 (pre-commit leak-scan hook #003 landed same day)
- **Failure:** the fail-closed sensitive-terms change (#001) was reported as CLOSED while its
  code had actually been reverted from the working tree by an unrelated `git checkout -- .`.
  The unit tests passed — because they ran *before* the revert — so the green tick was a lie.
- **Root cause:** guarded-processes trust the working tree state at test time; a later
  VCS op silently rewound it; reporting "done" trusted a stale test run, not the pushed tree.
- **Guard (mechanical):** pre-push leak-scan + a rule that a board gate is closed only when
  the **pushed commit** carries the change (`git show HEAD:<file> | grep <marker>`), not
  when a local test passed. Verify the artifact on origin, not in the worktree.
- **Proof it prevents:** the fold was re-checked against `origin/main` before this log closed.

## #003 — Script's leak-scan let a lowercase secret through (case-sensitivity)

- **When / guard:** 2026-09-25 / committed `scripts/leak-scan.sh`
- **Failure:** the `SK-` pattern was case-sensitive; `sk-proj-...` (lowercase) staged and
  committed under it. A test that "proved" the gate used a UPPERCASE token and so passed.
- **Root cause:** assumed a token shape from docs instead of testing both reported forms.
- **Guard:** `grep -Piq` (case-insensitive) + patterns widened to allow internal `-`/`.`/`_`
  (`sk-[A-Za-z0-9._-]{24,}`). Proven to block a real lowercase `sk-proj` secret.
- **Proof:** the probe commit was blocked (exit 1) and the working tree preserved.

## #002 — Grader false-corrected via `"INCORRECT"`-contains-`"CORRECT"`

- **When / guard:** 2026-09-25 / `scripts/judge_equivalence.py` + capability tests
- **Failure:** classification normalizer matched `"CORRECT" in s.upper()`; `"INCORRECT"`
  contains `"CORRECT"` as a substring, so every "incorrect" grade became "correct", yielding
  Jev acc 0.5. A live re-check caught it before the number was trusted.
- **Root cause:** substring matching with a positive-then-negative ordering trap.
- **Guard:** the negative keyword is checked FIRST; ordered + tested against both `CORRECT`
  and `INCORRECT`, and the Jev result re-derived (acc 1.0, κ 0.714) after the fix.
- **Proof:** failing-then-passing test; corrected number recorded in `config/thresholds.yaml`.

## #001 — Grader false-corrected every text-only answer (numeric-coalesce trap)

- **When / guard:** 2026-09-25 / capability harness grading + regression tests
- **Failure:** numeric "coalesce and compare equality" shortcut: a pure-text reply has no
  digits → coalesces to `""` → `"" == ""` → **True**. A model answering "User Safety: safe"
  to every prompt scored "correct." Inflated accuracy across the free-tier snapshot.
- **Root cause:** comparing empty digit-streams as a pass condition.
- **Guard:** a numeric key now requires the key's digit string as a **substring** of the
  reply's digit stream — a pure-text reply (no digits) can never satisfy it. Text keys
  match by substring only, never via numeric coalesce.
- **Proof:** regression tests assert `"User Safety: safe"` fails for `Paris`, `Mars`, and
  `56`, and that `7 * 8 = 56` passes for `56`.

## #000 — Client-identifiers file published to a public repo (history rewritten)

- **When / guard:** detected 2026-07-30 / committed leak-scan gate #003
- **Failure:** `config/sensitive_terms.yaml` (client identifiers) was committed to a public
  repo. The runbook's original note is the record; history was force-pushed to purge it.
- **Root cause:** the client roster was tracked in `config/` without a gitignore/hook guard.
- **Guard (permanent):** the roster is gitignored; `pre-commit`+`pre-push` leak-scan refuses
  any staged/pushed file carrying a credential shape or the roster; the loader is
  fail-closed (refuses to shadow-score real traffic against an empty/example roster).
- **Owner disposition (2026-09-25):** the identifiers were throwaway **test data**, not real
  clients — confirmed a non-issue for exposure, but the guard remains because the *pattern*
  is the lesson.

---

## Standing rules that emerged

- A board gate closes only when the **pushed commit** carries the change — never on a local
  green test.
- Every known failure gets a **mechanical guard** (hook, failing test, fail-closed path),
  not just a note. A note is documentation; a guard is protection.
- When adding a guard, **prove it fires** — stage a violating input and show it blocked.
- A project that watches for silent failure must never hide its own. This log is the proof.