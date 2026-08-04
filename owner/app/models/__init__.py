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
from app.models import licensing_service  # noqa: F401
from app.models import commercial_ops  # noqa: F401
from app.models import activation_governance  # noqa: F401
# Phase 9.5A -- commercial-operations foundation. Import order matters only
# in that every module referencing another module's table by ForeignKey
# STRING (never a Python import) is safe regardless of order -- SQLAlchemy
# resolves string-form ForeignKey targets against Base.metadata once every
# module below has been imported, not at each individual import statement.
from app.models import employees  # noqa: F401
from app.models import leads  # noqa: F401
from app.models import commercial_sales  # noqa: F401
from app.models import commissions  # noqa: F401
from app.models import expenses  # noqa: F401
from app.models import management_notes  # noqa: F401
from app.models import daily_reports  # noqa: F401
from app.models import cash_closing  # noqa: F401
from app.models import report_snapshots  # noqa: F401
from app.models import release_distribution  # noqa: F401

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
from app.models.commercial_ops import (  # noqa: F401
    RenewalRequest,
    RenewalRequestStatusHistory,
    PaymentCorrectionHistory,
    CommercialPolicy,
    InternalNotification,
    PilotRecord,
    PilotStatusHistory,
    PilotExtension,
    EmergencyExtension,
)
from app.models.activation_governance import (  # noqa: F401
    ActivationPolicy,
    PendingActivation,
    DeviceSlotException,
    DevicePolicyProfile,
    DevicePolicyPlatformRule,
    SubscriptionDevicePolicyOverride,
)
from app.models.licensing_service import (  # noqa: F401
    SigningKey,
    DevicePublicKey,
    ActivationRequest,
    SignedAssertion,
    EntitlementSnapshot,
    ExternalIdempotencyRecord,
    OfflinePolicy,
    LicenseOfflinePolicyAssignment,
    SecurityNonceRecord,
    KeyRotationEvent,
    RateLimitCounter,
    ServiceHealthEvent,
)
from app.models.employees import EmployeeProfile, EmployeePresenceSession  # noqa: F401
from app.models.leads import (  # noqa: F401
    Lead,
    LeadProductInterest,
    LeadStatusHistory,
    LeadAssignment,
    LeadInteraction,
    LeadFollowup,
    LeadNote,
    CustomerLocation,
    CustomerInteraction,
    CustomerFollowup,
)
from app.models.commercial_sales import (  # noqa: F401
    Quote,
    QuoteLine,
    SalesOrder,
    SalesOrderLine,
    CommercialInvoice,
    CommercialInvoiceItem,
    CommercialRefund,
    CommercialOperationsIdempotencyKey,
)
from app.models.commissions import (  # noqa: F401
    CommissionPlan,
    CommissionRuleVersion,
    EmployeeCommissionPlanAssignment,
    CommissionLedgerEntry,
    CommissionPayoutBatch,
    CommissionPayoutLine,
)
from app.models.expenses import (  # noqa: F401
    ExpenseCategory,
    Expense,
    Payee,
    ExpenseApproval,
    ExpensePayment,
    ExpenseAttachment,
)
from app.models.management_notes import (  # noqa: F401
    SharedManagementNote,
    ManagementNoteVisibilityGrant,
    ManagementNoteComment,
)
from app.models.daily_reports import DailyActivitySnapshot  # noqa: F401
from app.models.cash_closing import (  # noqa: F401
    CashClosing,
    CashClosingAdjustment,
    CashClosingReopenEvent,
)
from app.models.report_snapshots import ReportSnapshot  # noqa: F401
from app.models.release_distribution import ReleaseDownloadAuthorization  # noqa: F401
