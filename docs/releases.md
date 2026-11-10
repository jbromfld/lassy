# Image releases and GitOps

Application source and Docker builds stay in this repository. Cluster deployment
configuration lives in [agent-platform-gitops](../../agent-platform-gitops/README.md).
Laptop infrastructure and [management links](../../local-agent-environment/README.md)
live in `local-agent-environment`. These instructions update the Argo-managed
installation; Compose rebuilds affect only separately run Compose instances.

```bash
# Start the local registry through local-agent-environment first.
python3 scripts/publish_images.py  # Default registry 127.0.0.1:5001
```

Use Python 3.13+, Docker Buildx, and the Colima context. The publisher archives
`HEAD` (or `--revision COMMIT`), builds that committed tree, pushes OCI images,
and writes ignored `build/release.json` with source revision and image digests.
Uncommitted edits are excluded. Tests remain a separate prerequisite to release.
Use `--context` and `--registry` for another authenticated build environment;
registry login/credentials remain external to this script.

Apply any required database upgrade before deploying the new code. Lassy's
[additive migration/backfill guide](documents.md) covers existing installations.

From the **agent-platform-gitops repository**, promote the release:

```bash
python3 scripts/promote.py --environment local --release ../lassy/build/release.json
python3 scripts/validate.py
```

Review, commit, and push the environment change through the normal merge workflow
onto `main`. Argo deploys the selected digests after reconciliation; it does not
build images. Check `local-lassy` in [Argo CD](https://127.0.0.1:8443/) after opening
its [forward](../../local-agent-environment/docs/ports.md#open-argo-cd), or use the
[environment status commands](../../local-agent-environment/README.md#open-and-inspect-the-local-stack).
Compare the observed Git revision and running image digests with your release
record. A successful build/push is not evidence that reconciliation has finished.

The chart uses one replica and `Recreate` updates. Expect a brief interruption
while old processes exit and new workers/models become ready. Routine releases
need no new bootstrap/cutover. Reuse image artifacts for promotion; do not rebuild
to recover a previously selected digest.
