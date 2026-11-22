# Project license recommendation — approval required

## Recommendation

Use GNU General Public License version 3.0 only (GPL-3.0-only) for the original Find Hub Tracker code if the maintainer wants the public project to remain free software with source available to recipients.

This recommendation aligns with the pinned collector integration and its project-specific patch, which are marked GPL-3.0 in the upstream source. The repository already carries the pinned license texts and source/patch notices in THIRD_PARTY_LICENSES.md.

## Not finalized

This document is a proposal, not a grant of license. No top-level LICENSE file is included until the maintainer explicitly approves the license and supplies the copyright-holder wording. Until then, public publication or distribution of the original project code should wait.

If approved, add an unmodified GPL-3.0 license text as LICENSE, include an appropriate copyright notice, and review that file-level notices and all dependency notices remain consistent. Re-run the complete release-content audit and tests after that change.

## Third-party scope

The recommendation does not replace third-party licenses. The pinned collector and patch remain subject to their upstream GPL terms. React and other dependencies retain their own licenses. See THIRD_PARTY_LICENSES.md; its platform-specific and transitive dependency notes should be reviewed before distributing built images.