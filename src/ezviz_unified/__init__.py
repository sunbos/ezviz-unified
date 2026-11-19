"""Third-party EZVIZ SDK: response, HTTP transport, and regional authentication."""

from importlib.metadata import version

from ._request_values import JSON_NULL
from .alarms import AlarmQuery, Alarms, AsyncAlarms
from .arming import ActiveDefenceAction, Arming, ArmingSchedule, AsyncArming
from .auth import AsyncTokenManager, TokenManager
from .auth_models import RegionConfig, Token, parse_token
from .client import AsyncClient, Client
from .cloud_capture import AsyncCloudCapture, CloudCapture
from .cloud_recording import AsyncCloudRecording, CloudRecording
from .cloud_recording_plans import CloudRecordingDevice
from .cloud_recording_v1 import AsyncCloudRecordingV1, CloudRecordingV1
from .cloud_storage import AsyncCloudStorage, CloudStorage, StorageOrderOptions
from .components import AsyncComponents, ComponentInfo, Components
from .cruise import AsyncCruise, Cruise, CruisePeriod
from .ddns import AsyncDdns, Ddns
from .delegation import AsyncDelegation, Delegation
from .device_algorithms import AsyncDeviceAlgorithms, DeviceAlgorithms
from .devices import AsyncDevices, Devices
from .diagnostic_report import DiagnosticRecorder
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
from .hubs import AsyncHubs, Hubs
from .illumination import AsyncIllumination, FillLightMode, Illumination
from .imaging import AsyncImaging, ImageSetting, Imaging
from .inspection import (
    DeviceInspection,
    InspectionResult,
    inspect_device,
    inspect_device_async,
)
from .maintenance import AsyncMaintenance, Maintenance
from .message_subscriptions import AsyncMessageSubscriptions, MessageSubscriptions
from .models import ApiResponse, ResponsePolicy
from .operations import Operation
from .otap import AsyncOtap, Otap
from .ownership import AsyncOwnership, Ownership
from .passenger_flow import AsyncPassengerFlow, PassengerFlow
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
from .traffic import AsyncTraffic, Traffic
from .transport import AsyncTransport, SyncTransport
from .video import AsyncVideo, PreviewEncoding, Video, VideoSwitch
from .vod import AsyncVod, Vod
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
    "AsyncCloudCapture",
    "AsyncCloudRecording",
    "AsyncCloudRecordingV1",
    "AsyncComponents",
    "AsyncSubaccounts",
    "AsyncIllumination",
    "AsyncMaintenance",
    "AsyncMessageSubscriptions",
    "ApiResponse",
    "AsyncClient",
    "AsyncCruise",
    "AsyncDdns",
    "AsyncDelegation",
    "AsyncDeviceAlgorithms",
    "AsyncDevices",
    "AsyncDiagnostics",
    "AsyncHubs",
    "AsyncPassengerFlow",
    "AsyncTraffic",
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
    "AsyncVod",
    "AuthenticationError",
    "Client",
    "CloudCapture",
    "CloudStorage",
    "ComponentInfo",
    "Components",
    "Storage",
    "StorageFileQuery",
    "CloudRecording",
    "CloudRecordingDevice",
    "CloudRecordingV1",
    "Cruise",
    "CruisePeriod",
    "Ddns",
    "Delegation",
    "DeviceError",
    "DeviceInspection",
    "DeviceSwitch",
    "DeviceAlgorithms",
    "Devices",
    "DiagnosticRecorder",
    "Diagnostics",
    "EzvizError",
    "ImageSetting",
    "FillLightMode",
    "Hubs",
    "PassengerFlow",
    "Traffic",
    "Illumination",
    "Maintenance",
    "MessageSubscriptions",
    "Imaging",
    "InspectionResult",
    "JSON_NULL",
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
    "Vod",
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
