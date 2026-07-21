"""Import every model module so Base.metadata is complete for Alembic autogenerate
and for create_all() in tests. Nothing here is imported by products/* or commercial_runtime/*."""
from app.models.base import Base, TimestampMixin, UUIDPKMixin  # noqa: F401
from app.models import staff  # noqa: F401
from app.models import catalog  # noqa: F401
from app.models import customers  # noqa: F401
from app.models import subscriptions  # noqa: F401
from app.models import licensing  # noqa: F401
from app.models import installations  # noqa: F401
from app.models import audit  # noqa: F401

from app.models.staff import (  # noqa: F401
    Role,
    Permission,
    RolePermission,
    StaffUser,
    StaffRoleAssignment,
    StaffSession,
    StaffInvitation,
    MfaCredential,
    MfaRecoveryCode,
    LoginAttempt,
)
from app.models.catalog import (  # noqa: F401
    Product,
    Platform,
    ProductPlatform,
    ReleaseChannel,
    ProductVersion,
    Plan,
    PlanPrice,
    Addon,
    EntitlementDefinition,
    PlanEntitlement,
    AddonEntitlement,
)
from app.models.customers import Customer, CustomerContact, CustomerAddress, CustomerNote  # noqa: F401
from app.models.subscriptions import (  # noqa: F401
    Subscription,
    SubscriptionItem,
    SubscriptionAddon,
    SubscriptionStatusHistory,
    RenewalRecord,
    PaymentRecord,
)
from app.models.licensing import (  # noqa: F401
    License,
    LicenseStatusHistory,
    LicenseEntitlement,
    LicenseKeyIssuanceEvent,
)
from app.models.installations import (  # noqa: F401
    Installation,
    InstallationStatusHistory,
    DeviceRecord,
    ActivationEvent,
)
from app.models.audit import AuditLog, SecurityEvent, SystemSetting, DatabaseBackupRecord  # noqa: F401
