# Third-party components and licensing notes

This file records the third-party components reviewed for the v0.1.0-rc1 source prerelease. It is not legal advice. Keep each upstream license and copyright notice intact; re-audit after changing pins or distributing built images.

## Major components

| Component | Version/source | License / redistribution note |
|---|---|---|
| GoogleFindMyToolsWebUi collector | Git commit `8ad438190beea726228b52bc35d732ff25e7dc91`; `deploy/collector-device-deletion.patch` applies the project change | Upstream GPL-3.0 with upstream copyright preserved. The build recipe retrieves this immutable commit and applies the included patch. The source tree and patch are therefore available from the pinned source link plus this repository; this release does not include a prebuilt collector image. |
| GoogleFindMyTools | Library included by the pinned collector source | GPL-3.0 under its upstream notices; retain the pinned collector source and its notices. |
| React / React DOM | 19.1.1 | MIT |
| Leaflet | 1.9.4 | BSD-2-Clause |
| Tailwind CSS / Vite plugin | 4.1.12 | MIT |
| Vite | 6.4.6 | MIT |
| TypeScript | 5.9.2 | Apache-2.0 |
| FastAPI | 0.116.1 | MIT |
| Uvicorn | 0.35.0 | BSD-3-Clause |
| Psycopg | 3.2.9 | LGPL-3.0-or-later; retain its bundled license/exception notice if redistributing installed binaries. LGPL-3.0 is compatible with GPL-3.0. |
| HTTPX | 0.28.1 | BSD-3-Clause |
| PyYAML | 6.0.3 | MIT |
| argon2-cffi | 25.1.0 | MIT |
| PostgreSQL | 17 Alpine image | PostgreSQL License; see image/source notice |
| Caddy (optional) | 2.10 Alpine image | Apache-2.0; see image/source notice |
| Node / Python base images | Node 22 Bookworm slim; Python 3.12 slim | Include distribution and bundled-library notices when shipping built images. This ZIP supplies source/build instructions only. |
| Blue Oak Model License dependency family | Transitive packages in the pinned npm lockfile (platform-dependent) | Permissive notice license; preserve the upstream license text or link when redistributing the packages/bundles. The license steward states compatibility with GPLv3; see its FAQ. |

Original Find Hub Tracker code is GPL-3.0-only, Copyright (C) 2026 Waleed Khan. This does not relicense third-party material or claim its copyright. See `COPYRIGHT.md` for scope and `LICENSE` for the complete official GPL text.

## Source links

- [Pinned collector source and README](https://github.com/P6g9YHK6/GoogleFindMyToolsWebUi/tree/8ad438190beea726228b52bc35d732ff25e7dc91)
- [Pinned collector LICENSE](https://github.com/P6g9YHK6/GoogleFindMyToolsWebUi/blob/8ad438190beea726228b52bc35d732ff25e7dc91/LICENSE)
- [GoogleFindMyTools repository and GPL-3.0 notice](https://github.com/leonboe1/GoogleFindMyTools)
- [GNU GPL-3.0 text](https://www.gnu.org/licenses/gpl-3.0.html)
- [GNU GPL compatibility guidance](https://www.gnu.org/licenses/gpl-faq.html)
- [Blue Oak Model License FAQ](https://blueoakcouncil.org/license-faq)
- [Blue Oak Model License text](https://blueoakcouncil.org/license/1.0.0)
- [Docker Official Image: PostgreSQL](https://hub.docker.com/_/postgres)
- [Caddy license](https://github.com/caddyserver/caddy/blob/master/LICENSE)

The frontend lockfile pins the complete npm dependency graph. The collector's pinned `requirements.lock` contains 80 exact Python package pins; metadata for all 80 pinned versions was checked against PyPI in this review. Direct tracker Python dependencies are pinned in `requirements.txt`. Package managers retrieve dependency source/binaries at install/build time; dependencies are not copied into this source ZIP.

The `licenses/` directory preserves the full GPL-3.0 texts supplied by the pinned collector and upstream library. The collector source is cloned from the exact public commit during image build, and the included patch reconstructs the modified collector source. GNU GPLv3 requires preserving notices and making corresponding source available when conveying covered binaries; if publishing a built image, provide the exact patched source and all applicable build/license notices. The current source ZIP itself does not contain built images.

## Compatibility review (v0.1.0-rc1)

The pinned collector's 80 Python dependency versions all returned license metadata from PyPI. Reviewed families include MIT, BSD, Apache-2.0, PSF, MPL-2.0, LGPL-2.1-or-later, GPL-3.0, wxWindows Library Licence 3.1, and dual/permissive notices. `python-dateutil` declares Apache or BSD; `pycryptodomex` declares BSD or public domain; each provides a permissive option. The GPL-3.0 package is compatible with the project license. GNU identifies LGPL-2.1, LGPL-3.0, Apache-2.0, compatible MPL versions, and wxWindows Library License as GPLv3-compatible. The Blue Oak license steward states GPLv3 compatibility; its notice requirement and license text are linked above.

The complete locked npm graph (124 package/version entries, including optional platform packages) has license metadata for all entries. Families include MIT, ISC, 0BSD, Blue Oak Model 1.0.0, Apache-2.0, BSD-2/3-Clause, and MPL-2.0. Blue Oak Model License 1.0.0 is described by its license steward as GPLv3-compatible; preserve its required text or link when redistributing packages. The source release does not bundle `node_modules`; optional platform packages are downloaded by npm. The source-only package contains no prebuilt container images or dependency binaries, so base-image OS package notices are not part of this ZIP.

No license incompatibility was identified in this source/build configuration review. This is a documented technical review, not legal advice or a guarantee about every future platform-specific build. Re-run a lockfile/license scan and provide corresponding-source notices before distributing any built image or changing dependency pins.
