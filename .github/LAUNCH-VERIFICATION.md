# Public launch verification

This checklist is for release maintainers. Customer installation and operating
instructions are in the repository's `docs` directory. Keep site identities,
credentials, raw logs, scanner reports, and acceptance records outside Git.

## Freeze the candidate

Record the source commit, all three package checksums, source manifest, deployed
cluster identities, and executable image digests. Preserve earlier evidence and
failure journals. A change to a packaged file produces a new package and manifest;
do not silently attach results from the previous package to it.

Repository-only maintainer documentation does not change installation payloads.
Confirm that by comparing the generated package inputs and manifests with the
frozen candidate before deciding which checks to repeat.

## Use a bounded verification pass

1. Review existing evidence against the frozen candidate. Reuse completed checks
   only when their build, site, scope, and integrity bindings still match. Record
   the remaining checks before starting cloud jobs.
2. Run local publication, package, and release-gate regression checks. Obtain the
   final source security review and assess every High/Critical image finding
   against the executable digest and actual configuration. Refresh scans when
   required by the gate's freshness limit.
3. Complete outstanding browser and authorization checks through an authorized
   human session. Record exclusions accurately; an unexecuted check is pending.
4. Complete the independent application recovery procedure below. Review each
   infrastructure plan before applying it. After a failure or observation timeout,
   inspect the existing job and preserved state before submitting another job.
5. Assemble the reviewed private evidence and run
   `all-in-one/scripts/release-gate.py check --help` for the required inputs, then
   run `check` against the actual candidate records. Read the resulting blockers;
   a successful regression suite does not establish release eligibility.
6. Before changing visibility or merging for launch, review repository history,
   branches, pull requests, attachments, release assets, and automation output for
   private information. Confirm that published checksums and documentation match
   the artifacts selected for release.

Run only the missing or invalidated checks. Poll accepted jobs instead of
resubmitting them. Stop a failed phase for diagnosis; retain its checkpoint and
original replica settings. Define the next bounded action before consuming more
cloud or review capacity.

## Independent application recovery

A verified database and artifact restore is a prerequisite to application
recovery. Preserve the destination's original state before reconciliation and
keep both sites quiescent through the data cutover.

Inventory source and destination resource identities, metadata storage keys,
ownership references, finalizers, and configuration. Catalogs can differ after
independent workflow runs. Similar generated names do not establish a mapping.
Review shared, source-only, and destination-only entries explicitly. Preserve
source history and destination rollback material; stop when a mapping or dependent
resource is unreviewed. Do not bypass the catalog comparison, remove arbitrary
finalizers, or overwrite original replica counts to force progress.

Reconcile only the reviewed inventory with identity and version preconditions.
Activate the protected recovery connection profile after consistency and writer
checks pass. Run the normal installer, verify historical access and all included
model workflows, then rerun installation and verify data and connection
persistence. Record full recovery only after these phases complete.

## Completion

Keep readiness decisions in the private evidence record. All required acceptance,
recovery, source security, image disposition, provenance, and confidentiality
checks must pass for the exact release. Do not weaken the release gate to obtain
a passing result. Merge and publication follow the verified result and the
repository owner's release authorization.
