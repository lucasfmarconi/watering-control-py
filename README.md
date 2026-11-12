# watering-control-py

MicroPython watering controller for the [LilyGO HiGrow](https://github.com/Xinyuan-LilyGO/LilyGo-HiGrow) (ESP32). Reads soil moisture, DHT11, BH1750 light and battery voltage, publishes them over MQTT/TLS, and is controlled over MQTT (sleep interval, listen window).

## Requirements

- LilyGO HiGrow with MicroPython flashed
- Python 3 on the host, `adafruit-ampy`
- An MQTT broker (TLS)

## Project layout

| File | Purpose |
|---|---|
| `boot.py` | Wi-Fi (STA, with timeout) + NTP sync |
| `calc.py` | Pure helpers (median, ...), unit-tested on the host |
| `main.py` | One awake cycle (connect, read control messages, publish), then deep sleep |
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
| `PUBLISH_INTERVAL_S` | Seconds of deep sleep between cycles |
| `ADC_SAMPLES`, `ADC_SAMPLE_DELAY_MS` | Readings per ADC value (10-20, default 15) and pause between them (default 5 ms). The median is used. |
| `SENSOR_SETTLE_MS` | Minimum wait after powering the sensors before ADC reads (default 300). The DHT11 waits 1 s and retries up to 3 times. |
| `ADC_MAX_SPREAD` | Largest max-min of the soil samples still trusted (default 250, an untuned guess; check the serial `spread` values) |
| `SOIL_RAW_DRY`, `SOIL_RAW_WET` | Soil calibration points (raw ADC): the 0% and 100% readings. See `config.example.py` for how to measure them. Defaults 2800 / 1200 are placeholders. |
| `SALT_MOISTURE_MIN`, `SALT_MOISTURE_MAX` | Soil moisture band (percent, default 55-75) in which `salt/valid` is `true` |
| `DEEP_SLEEP` | `True` (default) to deep-sleep between cycles; `False` keeps the board reachable while developing |

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
| `status` | retained: `online` while awake, `sleeping` before deep sleep, `offline` (last will) after an unclean drop |
| `dht/temperature`, `dht/humidity`, `dht/data-timestamp` | DHT11; timestamp is ISO-8601 UTC |
| `soil/moisture`, `soil/data-timestamp` | percent; `soil/moisture` is skipped when the reading is invalid |
| `soil/raw` | median raw 12-bit ADC value, published every cycle (even when the reading is invalid) for recalibration |
| `soil/error` | short reason instead of `soil/moisture` when the reading is invalid: `rail-low` (raw < 50), `rail-high` (raw > 4040), `noisy` (spread above `ADC_MAX_SPREAD`), `bad-calibration` (dry == wet) |
| `salt/raw` | median raw ADC value of the salt electrodes (GPIO34). Relative and uncalibrated, not a unit; see "Salt reading" |
| `salt/valid` | `true` / `false`: `true` only when the soil moisture percent is inside the `SALT_MOISTURE_*` band (always `false` if the soil reading was invalid). Chart only the valid `salt/raw` readings |
| `salt/last-fertilized` | retained; republished from the `fertilized` control message so a dashboard can annotate the chart. A marker only, it changes no readings |
| `battery/voltage` | volts |
| `light/lux` | BH1750 |

Control (`<base>/control/<cmd>`):

| Command | Payload |
|---|---|
| `interval` | seconds between publishes |
| `listen` | seconds to listen for control messages after connecting (min 1, default 3) |
| `fertilized` | any text, e.g. an ISO date (max 64 chars). Republished retained as `salt/last-fertilized` |
| `soil_dry`, `soil_wet` | raw ADC value (integer 0-4095) for the 0% / 100% soil point. Applied immediately and saved to `calibration.json` on the board, only when it changes. Dry and wet must differ; invalid payloads are ignored and logged |

Control messages should be published **retained**. The board sleeps between cycles and only listens for a few seconds after connecting (`CONTROL_LISTEN_S` in `main.py`, or the `listen` command), which is when retained messages are delivered and applied. `interval` is applied to the sleep that follows, with a 10 s minimum. Soil calibration precedence is `config.py` < `calibration.json` < retained `soil_dry` / `soil_wet`, so to go back to `config.py` values clear the retained messages and delete `calibration.json` (`ampy rm calibration.json`).

## Power saving

Each cycle: power the sensors (the wait for them to settle overlaps the Wi-Fi/MQTT connect), connect Wi-Fi/NTP (`boot.py`) and MQTT, apply control messages, publish, then cut sensor power and deep-sleep. The BH1750 is read in one-time mode. Soil and battery are each the median of `ADC_SAMPLES` readings (about 75 ms each).

With `DEEP_SLEEP = True` the board resets on every wake and its USB serial drops while asleep, so `ampy` only works during the awake window. Set `DEEP_SLEEP = False` in `config.py` while developing.

## Pinout (HiGrow)

| Function | GPIO | Notes |
|---|---|---|
| DHT11 | 16 | |
| Soil moisture | 32 | ADC |
| Salt | 34 | ADC, input-only |
| Battery | 33 | ADC |
| Sensor power | 4 | drive HIGH to power sensors |
| I2C SDA / SCL | 25 / 26 | BH1750 at 0x23 |

## Extras

```sh
screen /dev/tty.usbserial-0001 115200   # serial monitor
ampy ls | ampy put f | ampy get f | ampy run f | ampy rm f
```

## License

See `LICENSE`.
