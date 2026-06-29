# R-102 reference patches

Each patch is a minimal source-only solution against the pinned upstream ref in
`../r102_manifest.json`. Cursor should verify every active task independently:

1. the probe fails on the clean pinned ref;
2. `git apply <task_id>.patch` applies cleanly;
3. the probe passes after applying the patch; and
4. the manifest's existing test command remains green.

Tasks originally excluded for probe/design reasons intentionally have no
reference patch.

The combined v7 active reference patches are:

`C-01.patch`, `C-03.patch`, `C-12.patch`, `T-01.patch`, `T-03.patch`,
`T-07.patch`, `T-13.patch`, `T-14.patch`, `T-15.patch`, `T-16.patch`,
`T-17.patch`, `T-18.patch`, `T-19.patch`, and `T-20.patch`.

All fourteen apply independently to their pinned baseline, pass their external
probe, and keep the manifest's existing test command green. Edit mode is not
encoded by task ID: R-112 selects whole-file or SEARCH/REPLACE from the pinned
file's estimated rewrite size and the configured threshold.

`T-02.patch` and `T-05.patch` are retained only as audit artifacts. Their
corrected probes already pass on pinned Tablib v3.8.0, so neither is a valid
repair task or capability datapoint. `T-05.patch` documents the corrected
full-datetime ISO behavior (`datetime.isoformat()`), not a necessary baseline
fix. The former `SIZE_EXCLUDED` patches are active now that R-112 supports large
files.
