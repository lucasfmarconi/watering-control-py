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
