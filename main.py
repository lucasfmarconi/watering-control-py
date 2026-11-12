import ssl
import time

import machine
import network
import dht
from umqtt.simple import MQTTClient

import calc
import config

# region constants
# LilyGO HiGrow pins
PIN_DHT = 16
PIN_SOIL = 32
PIN_SALT = 34
PIN_BAT = 33
PIN_POWER_CTRL = 4  # powers the sensors
PIN_I2C_SDA = 25
PIN_I2C_SCL = 26
BH1750_ADDR = 0x23
BH1750_MEASURE_MS = 180  # max high-res measurement time
CA_FILE = "emqxsl-ca.der"  # DigiCert Global Root G2, DER
ADC_MAX = 4095  # 12-bit ADC full scale
BATTERY_DIVIDER = 2  # the battery reaches the ADC pin through a divider that halves it
MS_PER_S = 1000
CONTROL_LISTEN_S = 3  # default awake window to receive retained control messages
MIN_LISTEN_S = 1  # lower bound for the "listen" command; 0 would make the board deaf to control
MIN_INTERVAL_S = 10  # lower bound for the "interval" command (protects the battery)
# Samples per ADC reading (10-20) and the pause between them
ADC_SAMPLES = max(10, min(20, getattr(config, "ADC_SAMPLES", 15)))
ADC_SAMPLE_DELAY_MS = getattr(config, "ADC_SAMPLE_DELAY_MS", 5)
SENSOR_SETTLE_MS = getattr(config, "SENSOR_SETTLE_MS", 300)  # after GPIO4 goes high, before ADC reads
DHT_SETTLE_MS = 1000  # DHT11 needs ~1 s after power-up
DHT_RETRY_MS = 1000  # and at most one read per second
DHT_ATTEMPTS = 3
ADC_MAX_SPREAD = getattr(config, "ADC_MAX_SPREAD", 250)  # largest max-min of the samples still trusted
DEEP_SLEEP = getattr(config, "DEEP_SLEEP", True)  # set False in config.py while developing

# Soil calibration (raw 12-bit ADC), set in config.py; see config.example.py for how
# to measure them. The defaults are placeholders for a typical capacitive probe.
SOIL_RAW_DRY = getattr(config, "SOIL_RAW_DRY", 2800)  # the "0%" point
SOIL_RAW_WET = getattr(config, "SOIL_RAW_WET", 1200)  # the "100%" point
# endregion

# region init
power_on_ms = None  # ticks when GPIO4 last went high
publish_interval_s = config.PUBLISH_INTERVAL_S
control_listen_s = CONTROL_LISTEN_S

power = machine.Pin(PIN_POWER_CTRL, machine.Pin.OUT, value=0)  # on only during a cycle


def setup_adc(pin_no):
    """Create an ADC on pin_no covering ~0-3.3 V at 12 bits.

    The ESP32 ADC is nonlinear near the rails (below ~0.1 V and above ~3.1 V at
    11 dB), so readings close to 0 or 4095 are less trustworthy.
    """
    adc = machine.ADC(machine.Pin(pin_no))
    adc.atten(machine.ADC.ATTN_11DB)
    adc.width(machine.ADC.WIDTH_12BIT)
    return adc


soil_adc = setup_adc(PIN_SOIL)
bat_adc = setup_adc(PIN_BAT)
salt_adc = setup_adc(PIN_SALT)  # input-only pin, fine for ADC
HAS_READ_UV = hasattr(bat_adc, "read_uv")  # factory-calibrated reading, if the firmware has it
# endregion

# region sensors
try:
    dht_sensor = dht.DHT11(machine.Pin(PIN_DHT))
except (OSError, ValueError) as e:
    print("DHT init failed:", e)
    dht_sensor = None

try:
    i2c = machine.I2C(0, sda=machine.Pin(PIN_I2C_SDA), scl=machine.Pin(PIN_I2C_SCL))
except (OSError, ValueError) as e:
    print("I2C init failed:", e)
    i2c = None


def read_adc_median(adc, samples=ADC_SAMPLES, delay_ms=ADC_SAMPLE_DELAY_MS, microvolts=False):
    """Sample an ADC repeatedly and return (median, spread).

    The median ignores outliers from Wi-Fi or power spikes. spread is
    max - min of the samples. Samples are taken with ADC.read_uv() when
    microvolts is True, otherwise with ADC.read().

    Args:
        adc: The machine.ADC to sample.
        samples: Number of readings to take.
        delay_ms: Pause between readings.
        microvolts: Read calibrated microvolts instead of the raw count.
    """
    read = adc.read_uv if microvolts else adc.read
    values = []
    for i in range(samples):
        if i:
            time.sleep_ms(delay_ms)
        values.append(read())
    return calc.median(values), calc.spread(values)


def power_sensors():
    """Power the sensors (GPIO4 high) and remember when."""
    global power_on_ms
    power.value(1)
    power_on_ms = time.ticks_ms()


def wait_since_power(settle_ms):
    """Sleep only for what is left of settle_ms since the sensors were powered.

    Connecting Wi-Fi/MQTT already takes seconds, so this is normally a no-op.
    """
    if power_on_ms is None:
        return
    remaining = settle_ms - time.ticks_diff(time.ticks_ms(), power_on_ms)
    if remaining > 0:
        time.sleep_ms(remaining)


def read_dht():
    """Return (temperature, humidity) from the DHT11, or None if it never answers.

    Waits for the sensor to settle after power-up and retries on OSError,
    pausing DHT_RETRY_MS between attempts (the DHT11 allows one read per second).
    """
    if dht_sensor is None:
        return None
    wait_since_power(DHT_SETTLE_MS)
    for attempt in range(DHT_ATTEMPTS):
        try:
            dht_sensor.measure()
            return dht_sensor.temperature(), dht_sensor.humidity()
        except OSError as e:
            print("DHT read failed:", e)
            if attempt < DHT_ATTEMPTS - 1:
                time.sleep_ms(DHT_RETRY_MS)
    return None


def read_soil():
    """Read the soil probe and return (pct, raw, spread, error).

    Capacitive sensors output a higher voltage (higher ADC value) when dry and a
    lower one when wet, so the reading is inverted and linearly mapped between
    two calibration points (see calc.raw_to_percent):

        pct = (SOIL_RAW_DRY - raw) * 100 / (SOIL_RAW_DRY - SOIL_RAW_WET)

    The reading is checked before it is converted (calc.soil_error): a value at
    the ADC rails or a large spread means the probe is unplugged, shorted or
    noisy. In that case pct is None and error says why, so a disconnected probe
    never looks like "0%, needs watering". Only valid readings are clamped to
    0-100.

    Calibration lives in config.py (SOIL_RAW_DRY / SOIL_RAW_WET); read the
    values from the soil/raw topic. Using the full 0-4095 range instead would
    never reach 0% or 100%, because real sensors only span part of the ADC
    range.
    """
    raw, spread = read_adc_median(soil_adc)
    print("soil raw:", raw, "spread:", spread)
    error = calc.soil_error(raw, spread, ADC_MAX_SPREAD)
    if error is not None:
        return None, raw, spread, error
    pct = calc.raw_to_percent(raw, SOIL_RAW_DRY, SOIL_RAW_WET)
    if pct is None:
        return None, raw, spread, "bad-calibration"
    return pct, raw, spread, None


def read_battery_v():
    """Return the battery voltage in volts, rounded to 2 decimals.

    The battery is wired to the ADC pin through a voltage divider that halves
    it (the pin sees about Vbat / 2), because a LiPo can reach 4.2 V, above the
    ADC's ~3.3 V range.

    - With ADC.read_uv() (HAS_READ_UV), which applies the factory calibration:
      V = microvolts / 1e6 * BATTERY_DIVIDER.
    - Otherwise: V = raw / 4095 * 6.6 * 1.1, where 6.6 is 3.3 * 2 (undoing the
      divider) and 1.1 is an empirical correction for the ESP32 ADC, which is
      not exactly linear at ATTN_11DB. Compare against a multimeter and adjust
      it if the reading is off.
    """
    if HAS_READ_UV:
        return round(read_adc_median(bat_adc, microvolts=True)[0] / 1e6 * BATTERY_DIVIDER, 2)
    return round(read_adc_median(bat_adc)[0] / ADC_MAX * 6.6 * 1.1, 2)


def read_lux():
    """Return ambient light in lux, or None if the BH1750 is missing or fails.

    Uses one-time high-resolution mode, after which the sensor powers itself
    down. It returns a 16-bit big-endian count; dividing by 1.2 converts it to
    lux (per the BH1750 datasheet).
    """
    if i2c is None:
        return None
    try:
        i2c.writeto(BH1750_ADDR, b"\x20")  # one-time high-res measurement
        time.sleep_ms(BH1750_MEASURE_MS)
        raw = i2c.readfrom(BH1750_ADDR, 2)
        return round(((raw[0] << 8) | raw[1]) / 1.2, 1)
    except OSError as e:
        print("BH1750 read failed:", e)
        return None


# endregion

# region mqtt
mqtt_client = None


def iso_utc():
    """Return the current UTC time as an ISO 8601 string (YYYY-MM-DDTHH:MM:SSZ).

    Only meaningful once the clock has been synced over NTP (see boot.py).
    """
    now = time.gmtime()
    return "%04d-%02d-%02dT%02d:%02d:%02dZ" % now[:6]


def topic(suffix):
    """Return the full MQTT topic for suffix, under config.MQTT_TOPIC_BASE."""
    return "%s/%s" % (config.MQTT_TOPIC_BASE, suffix)


def on_message(topic_bytes, msg):
    """Handle an incoming MQTT control message.

    The command is the part of the topic after "/control/":

    - interval: new sleep time between cycles in seconds (at least MIN_INTERVAL_S).
    - listen: new control listen window in seconds (at least MIN_LISTEN_S).

    Invalid numbers are ignored. Control messages should be published retained,
    since the device is only listening briefly each cycle.

    Args:
        topic_bytes: Topic the message arrived on, as bytes.
        msg: Message payload, as bytes.
    """
    global publish_interval_s, control_listen_s
    cmd = topic_bytes.decode().split("/control/", 1)[-1]
    msg = msg.decode().strip()
    try:
        if cmd == "interval":
            publish_interval_s = max(MIN_INTERVAL_S, int(msg))
            print("publish interval:", publish_interval_s)
        elif cmd == "listen":
            control_listen_s = max(MIN_LISTEN_S, int(msg))
            print("control listen:", control_listen_s)
    except ValueError:
        print("invalid control payload:", cmd, msg)


def connect_mqtt():
    """Connect to the MQTT broker over TLS and return the client.

    Verifies the broker certificate against CA_FILE (this needs the clock to be
    set, see boot.py), registers an offline last-will on the status topic,
    announces "online" (both retained), and subscribes to the control topics.
    """
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    with open(CA_FILE, "rb") as ca_file:
        ssl_ctx.load_verify_locations(cadata=ca_file.read())
    ssl_ctx.verify_mode = ssl.CERT_REQUIRED  # needs NTP time (see boot.py)
    client = MQTTClient(
        config.MQTT_CLIENT_ID,
        config.MQTT_BROKER,
        port=config.MQTT_PORT,
        user=config.MQTT_USER,
        password=config.MQTT_PASSWORD,
        ssl=ssl_ctx,
        keepalive=60,
    )
    client.set_last_will(topic("status"), b"offline", retain=True)
    client.set_callback(on_message)
    client.connect()
    client.publish(topic("status"), b"online", retain=True)
    client.subscribe(config.MQTT_TOPIC_DEVICES_SUB)
    return client


def send(suffix, payload, retain=False):
    """Publish payload under suffix with QoS 1, waiting for the broker's ack.

    The board powers the radio down right after publishing, so QoS 0 messages
    could still be in flight and get lost.
    """
    mqtt_client.publish(topic(suffix), payload, retain, 1)


def publish_readings():
    """Read all sensors and publish them, skipping any that are unavailable."""
    ts = iso_utc()
    dht_reading = read_dht()
    if dht_reading is not None:
        send("dht/temperature", str(dht_reading[0]))
        send("dht/humidity", str(dht_reading[1]))
        send("dht/data-timestamp", ts)
    wait_since_power(SENSOR_SETTLE_MS)
    pct, raw, spread, soil_err = read_soil()
    send("soil/raw", str(raw))  # also when invalid, to help diagnose and recalibrate
    if soil_err is None:
        send("soil/moisture", str(pct))
    else:
        send("soil/error", soil_err)
    send("soil/data-timestamp", ts)
    salt_raw = read_adc_median(salt_adc)[0]
    print("salt raw:", salt_raw)
    send("salt/raw", str(salt_raw))
    send("battery/voltage", str(read_battery_v()))
    lux = read_lux()
    if lux is not None:
        send("light/lux", str(lux))


# endregion


def listen_for_control():
    """Process incoming messages for control_listen_s seconds.

    Retained control messages are delivered right after subscribing, so this
    window is when they are applied. A "listen" message changes the window
    itself: it takes effect immediately, and the new value is re-read on every
    check, so a shorter window may end this one early.
    """
    start = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), start) < control_listen_s * MS_PER_S:
        mqtt_client.check_msg()
        time.sleep_ms(50)


def radio_off():
    """Disconnect and power down Wi-Fi so deep sleep starts from a clean state."""
    time.sleep_ms(300)  # let the DISCONNECT packet leave before the radio goes down
    wlan = network.WLAN(network.STA_IF)
    wlan.disconnect()
    wlan.active(False)


def run_cycle():
    """Run one awake cycle: connect, apply control messages, publish, disconnect.

    Always leaves the sensor power (GPIO4) off, even on failure, so the
    caller can go straight to sleep.
    """
    global mqtt_client
    try:
        power_sensors()
        print("cycle: connecting MQTT")
        mqtt_client = connect_mqtt()
        print("cycle: listening for control")
        listen_for_control()
        print("cycle: publishing")
        publish_readings()
        send("status", b"sleeping", retain=True)
        mqtt_client.disconnect()
        print("cycle: done")
    except Exception as e:  # any failure must still end in sleep, not an idle awake board
        print("Cycle failed:", e)
    finally:
        power.value(0)


while True:
    run_cycle()
    if DEEP_SLEEP:
        print("deep sleep for", publish_interval_s, "s at", iso_utc())
        radio_off()
        machine.deepsleep(publish_interval_s * MS_PER_S)  # wakes through boot.py
    time.sleep(publish_interval_s)
