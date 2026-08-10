"""Phase 9R M10 -- product release authority. Publication is a separate,
human-authorized act from import/upload; once PUBLISHED, a release's
identifying metadata is immutable (a correction is a new row)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.extensions import db_session
from app.models.catalog import Platform, Product, ProductVersion, ReleaseChannel
from app.releases.authority import ReleaseAuthorityError, publish_release, withdraw_release
from tests.conftest import make_staff


def _draft_release(app, **overrides):
    with app.app_context():
        product = db_session.execute(select(Product)).scalars().first()
        platform = db_session.execute(select(Platform)).scalars().first()
        channel = db_session.execute(select(ReleaseChannel)).scalars().first()
        attrs = dict(
            product_id=product.id, platform_id=platform.id, release_channel_id=channel.id,
            version="1.2.3", artifact_checksum_sha256="a" * 64, artifact_path="releases/1.2.3/app.exe",
            artifact_size_bytes=12345,
        )
        attrs.update(overrides)
        row = ProductVersion(**attrs)
        db_session.add(row)
        db_session.commit()
        return row.id


class TestPublish:
    def test_publish_moves_draft_to_published(self, app, seeded):
        staff_id = make_staff(app, "releaser@example.com")
        release_id = _draft_release(app)
        with app.app_context():
            release = db_session.get(ProductVersion, release_id)
            assert release.publication_state == "DRAFT"
            publish_release(release, actor_staff_user_id=staff_id)
            assert release.publication_state == "PUBLISHED"
            assert release.published_by_staff_user_id == staff_id
            assert release.published_at is not None

    def test_cannot_publish_already_published_release(self, app, seeded):
        staff_id = make_staff(app, "releaser2@example.com")
        release_id = _draft_release(app, version="1.2.4")
        with app.app_context():
            release = db_session.get(ProductVersion, release_id)
            publish_release(release, actor_staff_user_id=staff_id)
            with pytest.raises(ReleaseAuthorityError, match="only DRAFT releases may be published"):
                publish_release(release, actor_staff_user_id=staff_id)

    def test_cannot_publish_without_checksum(self, app, seeded):
        staff_id = make_staff(app, "releaser3@example.com")
        release_id = _draft_release(app, version="1.2.5", artifact_checksum_sha256=None)
        with app.app_context():
            release = db_session.get(ProductVersion, release_id)
            with pytest.raises(ReleaseAuthorityError, match="no artifact checksum"):
                publish_release(release, actor_staff_user_id=staff_id)

    def test_cannot_publish_without_artifact_path(self, app, seeded):
        staff_id = make_staff(app, "releaser4@example.com")
        release_id = _draft_release(app, version="1.2.6", artifact_path=None)
        with app.app_context():
            release = db_session.get(ProductVersion, release_id)
            with pytest.raises(ReleaseAuthorityError, match="no artifact path"):
                publish_release(release, actor_staff_user_id=staff_id)

    def test_forced_upgrade_threshold_below_min_supported_rejected(self, app, seeded):
        staff_id = make_staff(app, "releaser5@example.com")
        release_id = _draft_release(app, version="1.2.7", min_supported_version="2.0.0", forced_upgrade_threshold="1.0.0")
        with app.app_context():
            release = db_session.get(ProductVersion, release_id)
            with pytest.raises(ReleaseAuthorityError, match="forced_upgrade_threshold"):
                publish_release(release, actor_staff_user_id=staff_id)


class TestWithdraw:
    def test_withdraw_requires_published_state(self, app, seeded):
        staff_id = make_staff(app, "withdrawer@example.com")
        release_id = _draft_release(app, version="1.3.0")
        with app.app_context():
            release = db_session.get(ProductVersion, release_id)
            with pytest.raises(ReleaseAuthorityError, match="only PUBLISHED releases may be withdrawn"):
                withdraw_release(release, actor_staff_user_id=staff_id, reason="test")

    def test_withdraw_requires_a_reason(self, app, seeded):
        staff_id = make_staff(app, "withdrawer2@example.com")
        release_id = _draft_release(app, version="1.3.1")
        with app.app_context():
            release = db_session.get(ProductVersion, release_id)
            publish_release(release, actor_staff_user_id=staff_id)
            with pytest.raises(ReleaseAuthorityError, match="reason is required"):
                withdraw_release(release, actor_staff_user_id=staff_id, reason="")

    def test_withdraw_published_release_succeeds(self, app, seeded):
        staff_id = make_staff(app, "withdrawer3@example.com")
        release_id = _draft_release(app, version="1.3.2")
        with app.app_context():
            release = db_session.get(ProductVersion, release_id)
            publish_release(release, actor_staff_user_id=staff_id)
            withdraw_release(release, actor_staff_user_id=staff_id, reason="regression found")
            assert release.publication_state == "WITHDRAWN"
            assert release.withdrawn_reason == "regression found"
            assert release.withdrawn_at is not None


class TestImportCreatesDraftOnly:
    def test_import_release_manifest_never_auto_publishes(self, app, seeded):
        from app.catalog.services import import_release_manifest

        with app.app_context():
            product = db_session.execute(select(Product)).scalars().first()
            result = import_release_manifest(
                {"version": "9.9.9", "windows": [{"product_code": product.product_code, "sha256": "b" * 64, "path": "x"}]},
                actor_staff_user_id=None,
            )
            assert result["created"], result
            row = db_session.execute(
                select(ProductVersion).where(ProductVersion.version == "9.9.9")
            ).scalars().first()
            assert row.publication_state == "DRAFT"
