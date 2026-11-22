# Third-party components and licensing notes

This file is an inventory of direct runtime/build components and upstream projects. It is not legal advice. Check the precise license and notice files in each pinned source/image before redistributing an updated package.

## Major components

| Component | Version/source | License / redistribution note |
|---|---|---|
| GoogleFindMyToolsWebUi collector | Git commit `8ad438190beea726228b52bc35d732ff25e7dc91`; `deploy/collector-device-deletion.patch` applies the project-specific collector changes | GPL-3.0. The Dockerfile retrieves the exact source at build time; preserve license/copyright notices and make the corresponding source and patch available with any binary/image distribution. This ZIP is source/build instructions, not a prebuilt collector image. |
| GoogleFindMyTools | Upstream library used by the collector | GPL-3.0 per upstream repository. Confirm the exact dependency version/license in the pinned collector lockfile. |
| React / React DOM | 19.1.1 | MIT |
| Leaflet | 1.9.4 | BSD-2-Clause |
| Tailwind CSS / Vite plugin | 4.1.12 | MIT |
| Vite | 6.4.6 | MIT |
| TypeScript | 5.9.2 | Apache-2.0 |
| FastAPI | 0.116.1 | MIT |
| Uvicorn | 0.35.0 | BSD-3-Clause |
| Psycopg | 3.2.9 | LGPL-3.0-or-later (review its bundled license/exception notices) |
| HTTPX | 0.28.1 | BSD-3-Clause |
| PyYAML | 6.0.3 | MIT |
| argon2-cffi | 25.1.0 | MIT |
| PostgreSQL | 17 Alpine image | PostgreSQL License; see image/source notice |
| Caddy (optional) | 2.10 Alpine image | Apache-2.0; see image/source notice |
| Node / Python base images | Node 22 Bookworm slim; Python 3.12 slim | Include distribution and bundled-library notices when shipping built images. This ZIP supplies source/build instructions only. |

The project source does not currently include a declared project-wide license file. Do not infer that source copyright is assigned or that this candidate grants broader reuse rights. Obtain the project owner's licensing decision before public redistribution. GPL components remain governed by their own licenses regardless.

## Source links

- [Pinned collector source and README](https://github.com/P6g9YHK6/GoogleFindMyToolsWebUi/tree/8ad438190beea726228b52bc35d732ff25e7dc91)
- [Pinned collector LICENSE](https://github.com/P6g9YHK6/GoogleFindMyToolsWebUi/blob/8ad438190beea726228b52bc35d732ff25e7dc91/LICENSE)
- [GoogleFindMyTools repository and GPL-3.0 notice](https://github.com/leonboe1/GoogleFindMyTools)
- [Docker Official Image: PostgreSQL](https://hub.docker.com/_/postgres)
- [Caddy license](https://github.com/caddyserver/caddy/blob/master/LICENSE)

The frontend lockfile pins the complete npm dependency graph. Python direct dependencies are pinned in `requirements.txt`; the collector's own locked dependencies are fetched from its pinned source commit during its image build.

The `licenses/` directory contains the full GPL-3.0 license texts retrieved from the pinned collector and its upstream library. Collector source is cloned from the pinned public commit during build rather than redistributed as a prebuilt image in this ZIP; the included patch and source URL/commit are part of the build recipe. Before sharing a built image or a modified collector outside this private candidate, verify current GPL corresponding-source and notice obligations and provide the exact patched source.

## Lockfile scan notes

The installed locked npm dependency tree reported declared licenses in these families: MIT (majority), ISC, Blue Oak Model License 1.0.0, Apache-2.0, BSD-2-Clause, BSD-3-Clause and MPL-2.0, with no undeclared license among the platform-installed packages. Platform-specific optional packages are selected by npm on the friend's OS during `npm ci`; this was not a platform-by-platform license audit.

Direct Python runtime metadata reports FastAPI MIT, Uvicorn BSD-3-Clause, Psycopg LGPLv3, HTTPX BSD-3-Clause, PyYAML MIT, and argon2-cffi MIT. Transitive Python and collector dependencies remain governed by their respective pinned package metadata/license files. Review bundled notices for the target platform before redistribution of built images.
