# ADR-0001: Versioning via git + content-hash lockfile

**Status:** accepted

## Context
Schematics, firmware, drivers, component models, and scenarios all evolve independently. A sim result is meaningless unless it's traceable to the exact revision of every input. Question: adopt/build a bespoke versioning system, or compose existing ones?

## Decision
Git is the versioning substrate for all artifacts. embedded-twin adds a **content-addressed lockfile** (`twin.lock.json`) written on every run: SHA-256 of the netlist, BOM, firmware image, each bound component model source, the scenario file, and the twin package version. Reports embed the lock hash.

## Consequences
- Any report answers "which schematic rev / firmware build produced this?" without a new VCS to learn.
- Hardware teams already using Altium/Cadence vaults can export → the hash still pins the export.
- `twin diff <lockA> <lockB>` (future) can explain result deltas by input deltas.
- We revisit only if binary schematic formats make git storage painful (then: git-lfs, not a new system).
