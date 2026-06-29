# R-102 harness probes

Independent acceptance tests copied into each target repo before every trial.
They must **fail** on the pinned upstream baseline and **pass** after a correct fix.

Grading uses these probes plus `existing_tests` from the manifest — not only
model-authored tests.

The active set is the intersection of probe IDs and reference-patch IDs, minus
tasks marked `NEEDS_REDESIGN` in `r102_manifest.json`. The manifest's `tasks`
list is checked against that rule so the set cannot silently drift. Rejected
legacy probes and patches may remain as audit evidence but are never installed
by the harness. In particular, T-02 and T-05 have corrected probes that already
pass on the pinned baseline, so they remain `NEEDS_REDESIGN` rather than active.
