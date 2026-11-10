# Dependency security reconciliation

Verified on November 10, 2026. The default branch baseline contained **84 open
Dependabot alerts across 36 distinct advisories**: 16 alerts in the extension's
two lockfiles, 20 each in the Python project and generated requirements, and 28
in the uv lock. Findings repeated across manifests; they were not 84 distinct
vulnerabilities.

| Affected package | Distinct advisories | Replacement |
| --- | --- | --- |
| `brace-expansion` | 2 | Removed with the VS Code extension |
| `linkify-it` | 1 | Removed with the VS Code extension |
| `markdown-it` | 4 | Removed with the VS Code extension |
| `requests` | 2 | Upgraded to 2.34.2 |
| `setuptools` | 1 | Upgraded to 84.2.1 |
| `starlette` | 7 | Upgraded to 1.10.0 |
| `tmp` | 1 | Removed with the VS Code extension |
| `transformers` | 18 | Upgraded to 5.22.2 |

Every original advisory is accounted for below. Removed extension packages have no
remaining manifests/runtime in Lassy. Retained packages resolve beyond the patched
versions recorded in GitHub's advisory metadata; no alert was dismissed or ignored.

| Advisory | Package | Patched version recorded by GitHub | Resolution |
| --- | --- | --- | --- |
| [GHSA-253c-mchw-3w2r](https://github.com/advisories/GHSA-253c-mchw-3w2r) | `markdown-it` | 14.3.1 | Removed |
| [GHSA-29pf-2h5f-8g72](https://github.com/advisories/GHSA-29pf-2h5f-8g72) | `transformers` | 5.3.0 | 5.22.2 |
| [GHSA-2c2j-9gv5-cj73](https://github.com/advisories/GHSA-2c2j-9gv5-cj73) | `starlette` | 0.47.2 | 1.10.0 |
| [GHSA-37mw-44qp-f5jm](https://github.com/advisories/GHSA-37mw-44qp-f5jm) | `transformers` | 4.52.1 | 5.22.2 |
| [GHSA-3jxr-9vmj-r5cp](https://github.com/advisories/GHSA-3jxr-9vmj-r5cp) | `brace-expansion` | 1.1.16 | Removed |
| [GHSA-4w7r-h757-3r74](https://github.com/advisories/GHSA-4w7r-h757-3r74) | `transformers` | 4.53.0 | 5.22.2 |
| [GHSA-59p9-h35m-wg4g](https://github.com/advisories/GHSA-59p9-h35m-wg4g) | `transformers` | 4.53.0 | 5.22.2 |
| [GHSA-69w3-r845-3855](https://github.com/advisories/GHSA-69w3-r845-3855) | `transformers` | 5.0.0rc3 | 5.22.2 |
| [GHSA-6rvg-6v2m-4j46](https://github.com/advisories/GHSA-6rvg-6v2m-4j46) | `transformers` | 4.48.0 | 5.22.2 |
| [GHSA-6v5v-wf23-fmfq](https://github.com/advisories/GHSA-6v5v-wf23-fmfq) | `markdown-it` | 14.2.0 | Removed |
| [GHSA-7f5h-v6xp-fcq8](https://github.com/advisories/GHSA-7f5h-v6xp-fcq8) | `starlette` | 0.49.1 | 1.10.0 |
| [GHSA-82w8-qh3p-5jfq](https://github.com/advisories/GHSA-82w8-qh3p-5jfq) | `starlette` | 1.3.1 | 1.10.0 |
| [GHSA-86qp-5c8j-p5mr](https://github.com/advisories/GHSA-86qp-5c8j-p5mr) | `starlette` | 1.0.1 | 1.10.0 |
| [GHSA-9356-575x-2w9m](https://github.com/advisories/GHSA-9356-575x-2w9m) | `transformers` | 4.53.0 | 5.22.2 |
| [GHSA-9hjg-9r4m-mvj7](https://github.com/advisories/GHSA-9hjg-9r4m-mvj7) | `requests` | 2.32.4 | 2.34.2 |
| [GHSA-fgcw-684q-jj6r](https://github.com/advisories/GHSA-fgcw-684q-jj6r) | `transformers` | 5.5.0 | 5.22.2 |
| [GHSA-fpwr-67px-3qhx](https://github.com/advisories/GHSA-fpwr-67px-3qhx) | `transformers` | 4.50.0 | 5.22.2 |
| [GHSA-gc5v-m9x4-r6x2](https://github.com/advisories/GHSA-gc5v-m9x4-r6x2) | `requests` | 2.33.0 | 2.34.2 |
| [GHSA-hxxf-235m-72v3](https://github.com/advisories/GHSA-hxxf-235m-72v3) | `transformers` | 4.48.0 | 5.22.2 |
| [GHSA-jjph-296x-mrcr](https://github.com/advisories/GHSA-jjph-296x-mrcr) | `transformers` | 4.51.0 | 5.22.2 |
| [GHSA-jp82-jpqv-5vv3](https://github.com/advisories/GHSA-jp82-jpqv-5vv3) | `starlette` | 1.3.0 | 1.10.0 |
| [GHSA-ph9p-34f9-6g65](https://github.com/advisories/GHSA-ph9p-34f9-6g65) | `tmp` | 0.2.6 | Removed |
| [GHSA-phhr-52qp-3mj4](https://github.com/advisories/GHSA-phhr-52qp-3mj4) | `transformers` | 4.52.1 | 5.22.2 |
| [GHSA-q2hr-2g5m-vwhr](https://github.com/advisories/GHSA-q2hr-2g5m-vwhr) | `brace-expansion` | 1.1.21 | Removed |
| [GHSA-q2wp-rjmx-x6x9](https://github.com/advisories/GHSA-q2wp-rjmx-x6x9) | `transformers` | 4.51.0 | 5.22.2 |
| [GHSA-qq3j-4f4f-9583](https://github.com/advisories/GHSA-qq3j-4f4f-9583) | `transformers` | 4.50.0 | 5.22.2 |
| [GHSA-qxrp-vhvm-j765](https://github.com/advisories/GHSA-qxrp-vhvm-j765) | `transformers` | 4.48.0 | 5.22.2 |
| [GHSA-r7fv-28h4-cvq7](https://github.com/advisories/GHSA-r7fv-28h4-cvq7) | `markdown-it` | 14.3.2 | Removed |
| [GHSA-rcv9-qm8p-9p6j](https://github.com/advisories/GHSA-rcv9-qm8p-9p6j) | `transformers` | 4.53.0 | 5.22.2 |
| [GHSA-v245-v573-v5vm](https://github.com/advisories/GHSA-v245-v573-v5vm) | `linkify-it` | 5.0.2 | Removed |
| [GHSA-vfgh-jcmw-g37q](https://github.com/advisories/GHSA-vfgh-jcmw-g37q) | `markdown-it` | 14.3.3 | Removed |
| [GHSA-wqp7-x3pw-xc5r](https://github.com/advisories/GHSA-wqp7-x3pw-xc5r) | `starlette` | 1.1.0 | 1.10.0 |
| [GHSA-wrfc-pvp9-mr9g](https://github.com/advisories/GHSA-wrfc-pvp9-mr9g) | `transformers` | 4.48.0 | 5.22.2 |
| [GHSA-x746-7m8f-x49c](https://github.com/advisories/GHSA-x746-7m8f-x49c) | `starlette` | 1.1.0 | 1.10.0 |
| [GHSA-xmj3-9gf7-h52w](https://github.com/advisories/GHSA-xmj3-9gf7-h52w) | `setuptools` | 84.1.0 | 84.2.1 |
| [GHSA-xrqw-3rrv-vx5w](https://github.com/advisories/GHSA-xrqw-3rrv-vx5w) | `transformers` | 5.10.0 | 5.22.2 |

`python scripts/audit_lock.py` found **zero known vulnerabilities** across all
91 locked third-party package versions, including core, optional API, development,
and both supported platform branches. Nothing was suppressed. PyTorch's CPU local
version suffix is normalized only for advisory lookup; its CPU wheel remains locked.
The isolated build backend is pinned to the audited setuptools version as well.

The source of truth is `pyproject.toml` plus `uv.lock`. Removing the requirements
export and both extension locks reduces duplication; the audit verifies the actual
replacement resolution. GitHub's default-branch alerts remain open until these
changes are merged and its dependency graph is rescanned. No live service,
GitHub alert state, deployed image, or database was modified by this refactor.
