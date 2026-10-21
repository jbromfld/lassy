# Image releases and GitOps

Application source and Docker builds stay in this repository. Cluster deployment
configuration lives in [agent-platform-gitops](../../agent-platform-gitops/README.md).
Laptop infrastructure lives in `local-agent-environment`.

```bash
# Start the local registry through local-agent-environment first.
python3 scripts/publish_images.py --registry 127.0.0.1:5001
```

Use Python 3.13+, Docker Buildx, and the Colima context. The publisher archives
`HEAD` (or `--revision COMMIT`), builds that committed tree, pushes OCI images,
and writes ignored `build/release.json` with source revision and image digests.
Uncommitted edits are excluded. Tests remain a separate prerequisite to release.
Use `--context` and `--registry` for another authenticated build environment;
registry login/credentials remain external to this script.

Promote the release in the dedicated GitOps repository:

```bash
python3 scripts/promote.py --environment local --release ../lassy/build/release.json
python3 scripts/validate.py
```

Review, commit, and push the environment change. Argo deploys the selected image
digests; it does not build the images. An active environment requires automatic
sync enabled in its Application policy, or an explicit manual sync. The initial
bootstrap on a Compose-owned laptop starts paused until explicit cutover. Reuse image artifacts for promotion; rebuilding
source is not a substitute for preserving an existing digest.
