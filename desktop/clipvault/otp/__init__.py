"""In-memory primitives and integration ports for the OTP relay."""

from .capture import (
    CaptureAuthorizationRejected,
    CaptureSource,
    IsolatedOtpCandidate,
    OtpCaptureAuthorization,
    OtpCapturePort,
)

from .channel import (
    AckRejected,
    AuthenticatedOtpDelivery,
    EncryptedOtpEnvelope,
    EnvelopeAuthenticationFailed,
    EnvelopeExpired,
    OtpDeliveryAck,
    OtpPairChannelPort,
    OtpReceiveResult,
)

from .coordinator import (
    CaptureRejected,
    CrossDeviceSecurity,
    E2eeRequired,
    OtpCapturePolicy,
    OtpRelayCoordinator,
    PairingRequired,
    TransportUnavailable,
)

from .relay import (
    CapacityExceeded,
    ClaimContextMismatch,
    EventState,
    InvalidOtp,
    InvalidTransition,
    OtpClaim,
    OtpClaimContext,
    OtpEventView,
    OtpNotFound,
    OtpRelayError,
    OtpRelayStore,
    OtpSinkKind,
    OtpUseFailed,
    ReplayRejected,
    SenderMismatch,
    SessionMismatch,
    StoreClosed,
    TargetMismatch,
    TargetRevoked,
)

from .pipeline import (
    OtpInboundDelivery,
    OtpRelayProducer,
    OtpRelayReceiver,
    OtpSendReceipt,
)

from .transport import OtpEnvelopeTransportPort, TransportStateError

from .windows import (
    WindowsContextStale,
    WindowsInsertFailed,
    WindowsOtpConsumer,
    WindowsOtpContext,
    WindowsOtpInsertPort,
)

__all__ = [
    "AckRejected",
    "AuthenticatedOtpDelivery",
    "CapacityExceeded",
    "CaptureAuthorizationRejected",
    "CaptureSource",
    "ClaimContextMismatch",
    "CaptureRejected",
    "CrossDeviceSecurity",
    "E2eeRequired",
    "EncryptedOtpEnvelope",
    "EnvelopeAuthenticationFailed",
    "EnvelopeExpired",
    "EventState",
    "InvalidOtp",
    "InvalidTransition",
    "IsolatedOtpCandidate",
    "OtpCaptureAuthorization",
    "OtpCapturePolicy",
    "OtpCapturePort",
    "OtpClaim",
    "OtpClaimContext",
    "OtpEventView",
    "OtpInboundDelivery",
    "OtpNotFound",
    "OtpDeliveryAck",
    "OtpEnvelopeTransportPort",
    "OtpPairChannelPort",
    "OtpReceiveResult",
    "OtpRelayCoordinator",
    "OtpRelayError",
    "OtpRelayProducer",
    "OtpRelayReceiver",
    "OtpRelayStore",
    "OtpSinkKind",
    "OtpSendReceipt",
    "OtpUseFailed",
    "PairingRequired",
    "ReplayRejected",
    "SenderMismatch",
    "SessionMismatch",
    "StoreClosed",
    "TargetMismatch",
    "TargetRevoked",
    "TransportUnavailable",
    "TransportStateError",
    "WindowsContextStale",
    "WindowsInsertFailed",
    "WindowsOtpConsumer",
    "WindowsOtpContext",
    "WindowsOtpInsertPort",
]
