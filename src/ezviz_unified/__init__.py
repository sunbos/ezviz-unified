"""Third-party EZVIZ SDK: response, HTTP transport, and regional authentication."""

from importlib.metadata import version

from .alarms import AlarmQuery, Alarms, AsyncAlarms
from .arming import ActiveDefenceAction, Arming, ArmingSchedule, AsyncArming
from .auth import AsyncTokenManager, TokenManager
from .auth_models import RegionConfig, Token, parse_token
from .client import AsyncClient, Client
from .cloud_recording import AsyncCloudRecording, CloudRecording
from .cloud_recording_plans import CloudRecordingDevice
from .cloud_storage import AsyncCloudStorage, CloudStorage, StorageOrderOptions
from .cruise import AsyncCruise, Cruise, CruisePeriod
from .delegation import AsyncDelegation, Delegation
from .devices import AsyncDevices, Devices
from .diagnostics import AsyncDiagnostics, Diagnostics
from .errors import (
    ApiError,
    AuthenticationError,
    DeviceError,
    EzvizError,
    PermissionDeniedError,
    ProtocolError,
    TransportError,
)
from .illumination import AsyncIllumination, FillLightMode, Illumination
from .imaging import AsyncImaging, ImageSetting, Imaging
from .inspection import (
    DeviceInspection,
    InspectionResult,
    inspect_device,
    inspect_device_async,
)
from .maintenance import AsyncMaintenance, Maintenance
from .models import ApiResponse, ResponsePolicy
from .operations import Operation
from .otap import AsyncOtap, Otap
from .ownership import AsyncOwnership, Ownership
from .playback import AsyncPlayback, Playback, PlaybackOptions
from .providers import (
    AsyncStaticTokenProvider,
    AsyncTokenProvider,
    StaticTokenProvider,
    TokenProvider,
)
from .ptz import AsyncPtz, Ptz
from .ram_models import RamStatement, ram_password_digest
from .recordings import AsyncRecordings, Recordings
from .response import decode_response
from .security import AsyncSecurity, Security
from .settings import AsyncSettings, DeviceSwitch, Settings
from .snapshots import AsyncSnapshots, Snapshots
from .storage import AsyncStorage, Storage
from .storage_models import StorageFileQuery
from .subaccounts import AsyncSubaccounts, Subaccounts
from .transport import AsyncTransport, SyncTransport
from .video import AsyncVideo, PreviewEncoding, Video, VideoSwitch
from .webhooks import (
    WebhookMessage,
    parse_unverified_webhook,
    parse_webhook,
    verify_webhook_signature,
)

__version__ = version("ezviz-unified")

__all__ = [
    "ApiError",
    "AlarmQuery",
    "ActiveDefenceAction",
    "Arming",
    "ArmingSchedule",
    "AsyncArming",
    "AsyncSecurity",
    "Alarms",
    "AsyncAlarms",
    "AsyncCloudStorage",
    "AsyncStorage",
    "AsyncCloudRecording",
    "AsyncSubaccounts",
    "AsyncIllumination",
    "AsyncMaintenance",
    "ApiResponse",
    "AsyncClient",
    "AsyncCruise",
    "AsyncDelegation",
    "AsyncDevices",
    "AsyncDiagnostics",
    "AsyncImaging",
    "AsyncVideo",
    "AsyncPlayback",
    "AsyncSnapshots",
    "AsyncRecordings",
    "AsyncSettings",
    "AsyncPtz",
    "AsyncOtap",
    "AsyncOwnership",
    "AsyncStaticTokenProvider",
    "AsyncTokenProvider",
    "AsyncTransport",
    "AsyncTokenManager",
    "AuthenticationError",
    "Client",
    "CloudStorage",
    "Storage",
    "StorageFileQuery",
    "CloudRecording",
    "CloudRecordingDevice",
    "Cruise",
    "CruisePeriod",
    "Delegation",
    "DeviceError",
    "DeviceInspection",
    "DeviceSwitch",
    "Devices",
    "Diagnostics",
    "EzvizError",
    "ImageSetting",
    "FillLightMode",
    "Illumination",
    "Maintenance",
    "Imaging",
    "InspectionResult",
    "Operation",
    "Otap",
    "Ownership",
    "PermissionDeniedError",
    "ProtocolError",
    "PreviewEncoding",
    "Playback",
    "PlaybackOptions",
    "Ptz",
    "RegionConfig",
    "RamStatement",
    "Recordings",
    "ResponsePolicy",
    "Settings",
    "Security",
    "StorageOrderOptions",
    "Subaccounts",
    "Snapshots",
    "SyncTransport",
    "StaticTokenProvider",
    "Token",
    "TokenManager",
    "TokenProvider",
    "TransportError",
    "Video",
    "VideoSwitch",
    "WebhookMessage",
    "__version__",
    "decode_response",
    "inspect_device",
    "inspect_device_async",
    "parse_token",
    "parse_unverified_webhook",
    "parse_webhook",
    "ram_password_digest",
    "verify_webhook_signature",
]
