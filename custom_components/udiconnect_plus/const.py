"""Constants for the Udiconnect Plus integration."""

from typing import Final

DOMAIN: Final = "udiconnect_plus"
MANUFACTURER: Final = "Udinese"

API_BASE_URL: Final = "https://AA-Brands.yaleconnect-services.com/api/YaleConnect"
API_BRAND_PLATFORM_GUID: Final = (
    "883fe30e-6df9-4cc8-954c-f91aa9dadac5.3939e0f2-08e5-4bb1-b3a6-ddb1183666d3"
)
API_APP_VERSION: Final = "4.8.8"
API_TIMEOUT_SECONDS: Final = 15

CONF_DEVICE_UUID: Final = "device_uuid"

DEFAULT_SCAN_INTERVAL: Final = 60
MIN_SCAN_INTERVAL: Final = 10
MAX_SCAN_INTERVAL: Final = 600

# Faster polling while a cover is moving
MOVE_FOLLOW_UP_SECONDS: Final = 5
MOVE_TIMEOUT_SECONDS: Final = 90

ATTR_DEVICE_ID: Final = "device_id"
ATTR_HOME_ID: Final = "home_id"
ATTR_HOME_NAME: Final = "home_name"
ATTR_CATEGORY: Final = "category"
ATTR_IS_CALIBRATING: Final = "is_calibrating"
