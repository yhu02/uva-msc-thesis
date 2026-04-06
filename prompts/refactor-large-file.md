# Task: Iterative Analysis and Refactoring of an Entire Codebase

## Context
This codebase has grown organically and needs a comprehensive architectural review and refactoring. You will analyze and refactor the **entire codebase** across **5 iterative cycles**. Each cycle builds on the previous one, progressively improving the architecture, code structure, and maintainability. Do NOT skip ahead — complete each cycle fully before moving to the next.

## Codebase to analyze
[paste root directory path here]

## Loop Instruction
**Repeat this entire prompt (all 5 cycles below) 5 times.** After completing Cycle 5, go back to Cycle 1 and run through all cycles again, building on the improvements from the previous pass. Each full pass should find and fix issues that prior passes missed or introduced. Continue until you have completed **5 full passes** (25 total cycles).

---

## Cycle 1: Codebase-Wide Analysis & Discovery

Explore the entire codebase and produce a detailed report covering:

1. **Project structure** — map out all packages, modules, and their relationships
2. **Responsibility mapping** — for each file/module, summarize its purpose and identify files that handle multiple unrelated concerns
3. **Dependency graph** — map inter-module dependencies, identify tightly coupled components and circular imports
4. **Oversized files** — list files that are too large or contain too many responsibilities (flag anything over ~300 lines or with more than one clear concern)
5. **Code smells** — god classes/modules, duplicated logic across files, inconsistent patterns, mixed abstraction levels, dead code
6. **Metrics** — lines per file, number of functions/classes per file, max function length, overall package structure depth
7. **Inconsistencies** — naming conventions, error handling patterns, logging approaches, configuration access patterns that vary across the codebase

**Deliverable:** A written analysis document covering the full codebase. Do not change any code yet.

**Checkpoint:** Present findings and wait for confirmation before proceeding.

---

## Cycle 2: Architecture Redesign

Based on the Cycle 1 analysis, propose a new codebase architecture.

1. **High-level package structure** — define the top-level packages and their responsibilities
2. **Module breakdown** — for each package, define the modules within it:
   - **Name**: descriptive module name
   - **Responsibility**: single-sentence purpose (Single Responsibility Principle)
   - **Contents**: list of functions/classes that belong here
   - **Dependencies**: what it imports from other modules/packages
   - **Public API**: what it exports
3. **Migration map** — for each existing file, show where its contents move to in the new structure

Present the proposed structure as a tree, e.g.:
```
project/
├── core/
│   ├── __init__.py
│   ├── models.py       — Shared data models and types
│   └── config.py       — Configuration loading and validation
├── ingestion/
│   ├── __init__.py
│   ├── parsing.py      — Input parsing and normalization
│   └── validation.py   — Input validation rules
├── processing/
│   ├── __init__.py
│   ├── engine.py       — Core processing logic
│   └── transforms.py   — Data transformations
├── output/
│   ├── __init__.py
│   ├── formatting.py   — Output formatting
│   └── export.py       — Export to various formats
└── cli.py              — CLI entry point (thin orchestration layer)
```

Also identify:
- Which migrations are independent vs. which must move together
- Circular dependencies and how to break them
- Migration order (easiest/most independent first)
- Which existing APIs must be preserved for backward compatibility

**Deliverable:** Full architecture proposal + prioritized migration plan. No code changes yet.

**Checkpoint:** Present the proposed architecture and wait for confirmation before proceeding.

---

## Cycle 3: Foundation & Core Extractions

Execute the **foundational** portion of the migration plan from Cycle 2:

1. **Shared types & models** — extract shared data structures, types, constants, and configuration into their own modules
2. **Utility consolidation** — deduplicate shared helpers across files, place domain-specific helpers with their domain
3. **Independent modules** — extract modules with no or minimal inbound dependencies
4. For each extraction:
   - Create the new module/package
   - Move the relevant code
   - Update all imports across the entire codebase
   - Ensure backward compatibility where needed
5. Run tests / verify nothing breaks after each extraction

**Deliverable:** Working codebase with foundational modules extracted and all tests passing.

**Checkpoint:** Present what was extracted, confirm tests pass, and wait for confirmation before proceeding.

---

## Cycle 4: Deep Refactoring & Remaining Extractions

Execute the **remaining** migrations, tackling the more complex, tightly-coupled parts:

1. **Split oversized files** — break apart files that still handle multiple concerns
2. **Decouple tightly-bound modules** — use dependency injection, interfaces, or shared types modules to break circular dependencies
3. **Restructure packages** — move modules into their correct packages per the Cycle 2 design
4. **Slim down entry points** — CLI/main files should be thin orchestration layers that delegate to domain modules
5. Run tests / verify nothing breaks after each change

**Deliverable:** Working codebase matching the target architecture, all tests passing.

**Checkpoint:** Present the final structure, confirm tests pass, and wait for confirmation before proceeding.

---

## Cycle 5: Polish, Validate & Document

Final quality pass over the entire refactored codebase:

1. **Review each module** — does it have a single clear responsibility? Is anything misplaced?
2. **Clean up imports** — remove unused imports, sort and organize across all files
3. **Consistency pass** — ensure naming conventions, error handling, and patterns are consistent codebase-wide
4. **Dead code removal** — remove any orphaned code, unused re-exports, or compatibility shims that are no longer needed
5. **Check public APIs** — ensure all external entry points and interfaces still work
6. **Verify test coverage** — run full test suite, flag any gaps introduced by the refactor
7. **Document** — add module-level docstrings to each new/modified file explaining its purpose

**Deliverable:** Final refactored codebase, all tests passing, with a summary of all changes made across the 5 cycles including:
- Before/after directory tree comparison
- List of files created, moved, split, or deleted
- Any API changes or breaking changes introduced

---

## Constraints (apply to all cycles)
- Prefer composition over inheritance
- Each module should be testable in isolation
- Avoid creating modules with only 1-2 small functions unless they represent a clear distinct concern
- Don't create a `utils.py` dumping ground — if helpers are domain-specific, they belong with their domain
- Minimize circular dependencies; if unavoidable, use dependency injection or a shared types module
- Preserve existing public APIs where possible
- The codebase must remain functional after every cycle — no big-bang rewrites
- Run tests after every significant change, not just at the end of a cycle
- Commit after each cycle so changes can be reviewed and reverted independently
- After completing all 5 cycles, **loop back to Cycle 1** and repeat. Do this until you have completed **5 full passes** through all cycles. Each pass should catch issues the previous pass missed or introduced. Label each pass clearly (Pass 1/5, Pass 2/5, etc.).
