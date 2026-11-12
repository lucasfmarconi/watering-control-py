# Copy to config.py and fill in. config.py is gitignored.

WIFI_SSID = "your-ssid"
WIFI_PASSWORD = "your-password"
WIFI_TIMEOUT_S = 20

MQTT_BROKER = "broker.example.com"
MQTT_PORT = 8883
MQTT_USER = "user"
MQTT_PASSWORD = "password"
MQTT_CLIENT_ID = "higrow-01"
MQTT_TOPIC_BASE = "watering/higrow-01"
MQTT_TOPIC_DEVICES_SUB = "watering/higrow-01/control/#"

TZ_OFFSET = -3  # hours from UTC, display only

PUBLISH_INTERVAL_S = 60
PUMP_MAX_ON_S = 30  # safety cutoff
