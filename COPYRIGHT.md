# Copyright and license scope

**SPDX-License-Identifier: GPL-3.0-only**
**Copyright (C) 2026 Waleed Khan**

The original Find Hub Tracker application source, first-party tests, and project documentation in this repository are licensed under the GNU General Public License version 3 only. The complete, unmodified GPL-3.0 text follows the copyright and SPDX notice in [`LICENSE`](LICENSE).

Source files use SPDX headers where the file format supports comments. JSON metadata declares `GPL-3.0-only` in `package.json`; formats that do not support comments are covered by this scope statement.

## Third-party material

Third-party code retains its own copyright and license. In particular, the pinned GoogleFindMyToolsWebUi collector source and the project patch applied to it are GPL-3.0 under their upstream attribution; they are not claimed as Waleed Khan's original code. License texts, pinned source links, dependency notices, and redistribution notes are listed in [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md). Google, Find Hub, OpenStreetMap, and other third-party names and marks remain with their owners.

The source release contains the collector build recipe, exact upstream commit, patch, and applicable GPL text. It does not distribute prebuilt application/collector images or installed npm/Python dependencies. Builds retrieve dependencies according to the pinned manifests. Anyone distributing built images must provide the corresponding source and notices required by the applicable component licenses.
