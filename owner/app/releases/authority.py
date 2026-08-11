"""Phase 9R M10 -- product release authority.

Publication is a deliberate, separate, human-authorized act, distinct from
import/upload (app/catalog/services.py::import_release_manifest, which
only ever creates a DRAFT row). Once PUBLISHED, a release's identifying
metadata (version, checksum, artifact path/size, build number) is
immutable -- a correction is a new ProductVersion row, never an edit to a
published one. This is what "immutable or versioned" (the governing
instruction's own phrase) means here: versioned, because a fixed release
is superseded by a new row rather than mutated in place.
"""
from __future__ import annotations

import uuid

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.models.base import utcnow
from app.models.catalog import ProductVersion


class ReleaseAuthorityError(ValueError):
    pass


def publish_release(release: ProductVersion, *, actor_staff_user_id: uuid.UUID) -> ProductVersion:
    if release.publication_state != "DRAFT":
        raise ReleaseAuthorityError(f"Cannot publish a release in state {release.publication_state!r} -- only DRAFT releases may be published.")
    if not release.artifact_checksum_sha256:
        raise ReleaseAuthorityError("Cannot publish a release with no artifact checksum recorded.")
    if not release.artifact_path:
        raise ReleaseAuthorityError("Cannot publish a release with no artifact path recorded.")
    if (
        release.forced_upgrade_threshold
        and release.min_supported_version
        and release.forced_upgrade_threshold < release.min_supported_version
    ):
        # Lexicographic compare is intentionally NOT a real semver compare
        # (that needs a real parser this module doesn't have yet) -- this
        # catches the common, obviously-wrong case (equal-length dotted
        # numeric versions) without claiming full semver correctness.
        raise ReleaseAuthorityError("forced_upgrade_threshold must not be lower than min_supported_version.")

    before = {"publication_state": release.publication_state}
    release.publication_state = "PUBLISHED"
    release.published_by_staff_user_id = actor_staff_user_id
    release.published_at = utcnow()
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None,
        action_code="RELEASE_PUBLISHED", entity_type="product_version", entity_public_id=str(release.id),
        before_state=before,
        after_state={"publication_state": "PUBLISHED", "version": release.version, "artifact_checksum_sha256": release.artifact_checksum_sha256},
    )
    return release


def withdraw_release(release: ProductVersion, *, actor_staff_user_id: uuid.UUID, reason: str) -> ProductVersion:
    """Withdraws a PUBLISHED release -- no new download authorization or
    activation may reference it afterward (M11's authorize_download() and
    the licensing service's own release-channel checks both check
    publication_state == "PUBLISHED"). Installations already running a
    withdrawn version are NOT retroactively broken -- withdrawal blocks new
    downloads/activations against this version, it does not reach into any
    already-issued signed lease (M9) or already-completed download."""
    if release.publication_state != "PUBLISHED":
        raise ReleaseAuthorityError(f"Cannot withdraw a release in state {release.publication_state!r} -- only PUBLISHED releases may be withdrawn.")
    if not reason or not reason.strip():
        raise ReleaseAuthorityError("A withdrawal reason is required.")

    before = {"publication_state": release.publication_state}
    release.publication_state = "WITHDRAWN"
    release.withdrawn_at = utcnow()
    release.withdrawn_reason = reason
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None,
        action_code="RELEASE_WITHDRAWN", entity_type="product_version", entity_public_id=str(release.id),
        before_state=before, after_state={"publication_state": "WITHDRAWN"}, reason=reason,
    )
    return release
