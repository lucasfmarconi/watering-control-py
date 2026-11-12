# watering-control-py

MicroPython watering controller for the [LilyGO HiGrow](https://github.com/Xinyuan-LilyGO/LilyGo-HiGrow) (ESP32). Reads soil moisture, DHT11, BH1750 light and battery voltage, publishes them over MQTT/TLS, and drives a water pump via MQTT control messages.

## Requirements

- LilyGO HiGrow with MicroPython flashed
- Python 3 on the host, `adafruit-ampy`
- An MQTT broker (TLS)

## Project layout

| File | Purpose |
|---|---|
| `boot.py` | Wi-Fi (STA, with timeout) + NTP sync |
| `main.py` | Init peripherals, connect MQTT, non-blocking loop |
| `config.py` | Secrets and tunables (gitignored) |
| `config.example.py` | Committed template |
| `deploy.sh` | ampy wrapper |
| `.ampy` | Serial settings |

## Configuration

```sh
cp config.example.py config.py
```

| Key | Description |
|---|---|
| `WIFI_SSID`, `WIFI_PASSWORD`, `WIFI_TIMEOUT_S` | Wi-Fi credentials and connect timeout |
| `MQTT_BROKER`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASSWORD`, `MQTT_CLIENT_ID` | Broker settings |
| `MQTT_TOPIC_BASE` | Base for published topics |
| `MQTT_TOPIC_DEVICES_SUB` | Control subscription (`<base>/control/#`) |
| `TZ_OFFSET` | Hours from UTC (display only) |
| `PUBLISH_INTERVAL_S` | Seconds between publishes |
| `PUMP_MAX_ON_S` | Safety cutoff for the pump |

> TLS: the broker certificate is verified against `emqxsl-ca.der` (DigiCert Global Root G2, the EMQX Cloud CA). Verification needs correct time, so NTP in `boot.py` must succeed. Regenerate the DER with `openssl x509 -in emqxsl-ca.crt -outform der -out emqxsl-ca.der`.

## Usage

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
set -a; source .ampy; set +a
./deploy.sh              # all files
./deploy.sh main.py      # single file
```

One-time `umqtt` install, on the board: `import mip; mip.install("umqtt.simple")` (needs Wi-Fi), or put `umqtt/simple.py` on the board with ampy.

## MQTT

Published (`<base>/...`):

| Topic | Notes |
|---|---|
| `status` | retained: `online` / `offline` (last will) |
| `dht/temperature`, `dht/humidity`, `dht/data-timestamp` | DHT11; timestamp is ISO-8601 UTC |
| `soil/moisture`, `soil/data-timestamp` | percent |
| `battery/voltage` | volts |
| `light/lux` | BH1750 |
| `pump/state` | `off` when the safety cutoff fires |

Control (`<base>/control/<cmd>`):

| Command | Payload |
|---|---|
| `pump` | `on` / `off` |
| `interval` | seconds between publishes |
| `pump-max-on` | pump cutoff in seconds |

## Pinout (HiGrow)

| Function | GPIO | Notes |
|---|---|---|
| DHT11 | 16 | |
| Soil moisture | 32 | ADC |
| Salt | 34 | ADC, not used yet |
| Battery | 33 | ADC |
| Sensor power | 4 | drive HIGH to power sensors |
| Pump drive | 19 | shared with LoRa MISO; only without LoRa shield |
| I2C SDA / SCL | 25 / 26 | BH1750 at 0x23 |

## Extras

```sh
screen /dev/tty.usbserial-0001 115200   # serial monitor
ampy ls | ampy put f | ampy get f | ampy run f | ampy rm f
```

## License

See `LICENSE`.
