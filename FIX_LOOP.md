Do not just analyze. Act on the repository.

You are Claude Code, serving as a cautious senior maintainer. Perform 20 iterative repository cleanup cycles.

Goals:
- remove provably unused code
- remove dead code
- remove obsolete legacy code
- fix real bugs
- update tests
- update docs

Constraints:
- conservative changes only
- no speculative deletions
- no cosmetic churn
- preserve intended behavior unless fixing a bug
- verify references across the repo before deleting anything
- account for dynamic loading, framework conventions, config-based wiring, public APIs, plugins, templates, tests, migrations, and generated code
- if uncertain, leave it and mark for manual review

Each cycle must:
- re-scan the entire repo
- identify candidates
- choose a safe batch
- make edits
- update affected tests
- update affected docs
- run relevant validation
- fix regressions from your own changes
- create exactly one git commit for that cycle
- output a structured cycle report with exact file paths changed and why each change was safe

Git commit requirements for every cycle:
- make one commit at the end of each successful cycle
- do not skip commits
- do not squash multiple cycles into one commit
- commit only the changes made in that cycle
- use a clear commit message in this format:
  - `cycle N: remove dead code in <area>`
  - `cycle N: fix bug in <area>`
  - `cycle N: clean legacy paths in <area>`
  - `cycle N: update tests and docs for <area>`
- if a cycle contains mixed changes, use the most representative summary
- before committing, ensure validation for that cycle has passed as far as possible
- if some checks cannot run, state that explicitly in the cycle report and still make the commit only if the repository is left in the safest validated state possible

Deletion safety rules:
- never delete code without checking for references across the whole repository
- explicitly consider non-obvious usage, including:
  - string references
  - reflection
  - decorators
  - registration tables
  - CLI wiring
  - routes
  - serializers
  - dependency injection
  - config-driven loading
- if safety cannot be established, do not delete; defer it to manual review

Change policy:
- prefer small, high-confidence edits over broad refactors
- do not rewrite working code just to modernize it
- when fixing bugs, prefer root-cause fixes over symptom patches
- keep behavior stable unless a bug requires a behavior change
- keep the repo buildable/testable after every cycle

For each cycle, output this structure:

### Cycle N

#### Findings
- high-confidence cleanup or bug-fix candidates found this cycle

#### Changes made
- exact file paths changed
- what was removed, fixed, or updated
- why each change was safe

#### Tests updated
- exact test files changed
- what was added, removed, or adjusted

#### Docs updated
- exact documentation files changed
- what was updated and why

#### Validation
- commands/checks run
- pass/fail results
- any regressions found and fixed

#### Git commit
- commit hash
- commit message

#### Deferred / manual review
- suspicious items intentionally left untouched due to insufficient confidence

At the end of 20 cycles, provide:
- a final summary
- removed code grouped by category
- bug fixes made
- tests/docs updated
- deferred manual-review items
- a list of all 20 commit hashes with their commit messages

Important:
- re-scan the whole repo every cycle, because earlier removals may reveal newly unused code
- do not stop early
- do not collapse multiple cycles into one summary
- do not collapse multiple cycles into one commit
- complete all 20 cycles