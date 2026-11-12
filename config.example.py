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

PUBLISH_INTERVAL_S = 600  # deep-sleep time between cycles
DEEP_SLEEP = True  # set False while developing so ampy/REPL stay reachable

ADC_SAMPLES = 15  # readings per ADC sample, median taken (allowed 10-20)
ADC_SAMPLE_DELAY_MS = 5  # pause between those readings
SENSOR_SETTLE_MS = 300  # wait after powering the sensors (GPIO4) before reading the ADCs
ADC_MAX_SPREAD = 250  # soil readings whose samples differ by more than this (max-min) are rejected as noisy

# Soil calibration, raw 12-bit ADC values read from the soil/raw topic. A capacitive
# probe reads HIGHER when dry, so SOIL_RAW_DRY should be larger than SOIL_RAW_WET.
# The defaults are placeholders; measure your own:
#  1. Dry point: read soil/raw when the top layer is dry and the leaves are just
#     starting to relax (not bone-dry soil).
#  2. Wet point: water until it drains from the pot, wait ~15 minutes, read soil/raw.
#  3. Do not use water in a glass for the wet point: it reads far outside what soil
#     can reach.
# Values near the ends of the ADC range (below ~100 or above ~3900) are less reliable.
SOIL_RAW_DRY = 2800  # raw value when the plant needs water (0%)
SOIL_RAW_WET = 1200  # raw value after a deep watering, once drained (100%)

# salt/raw is only comparable between readings at similar soil moisture. salt/valid is
# "true" only while the soil moisture percent is inside this band; chart only those.
SALT_MOISTURE_MIN = 55
SALT_MOISTURE_MAX = 75
