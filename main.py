import ssl
import time

import machine
import dht
from umqtt.simple import MQTTClient

import config

# region constants
# LilyGO HiGrow pins
PIN_DHT = 16
PIN_SOIL = 32
PIN_SALT = 34
PIN_BAT = 33
PIN_POWER_CTRL = 4  # powers the sensors
PIN_PUMP = 19  # motor drive (only when no LoRa shield)
PIN_I2C_SDA = 25
PIN_I2C_SCL = 26
BH1750_ADDR = 0x23
BH1750_MEASURE_MS = 180  # max high-res measurement time
CA_FILE = "emqxsl-ca.der"  # DigiCert Global Root G2, DER
ADC_MAX = 4095  # 12-bit ADC full scale
MS_PER_S = 1000
CONTROL_LISTEN_S = 3  # default awake window to receive retained control messages
MIN_LISTEN_S = 1  # lower bound for the "listen" command; 0 would make the board deaf to control
MIN_INTERVAL_S = 10  # lower bound for the "interval" command (protects the battery)
DEEP_SLEEP = getattr(config, "DEEP_SLEEP", True)  # set False in config.py while developing

# Soil sensor calibration (raw 12-bit ADC). Placeholder values: measure your own
# sensor (see read_soil_pct) and replace them.
SOIL_DRY_RAW = 2800  # sensor held in dry air
SOIL_WET_RAW = 1200  # sensor submerged in water up to the marked line
# endregion

# region init
publish_interval_s = config.PUBLISH_INTERVAL_S
pump_max_on_s = config.PUMP_MAX_ON_S
control_listen_s = CONTROL_LISTEN_S
pump_on_since = None

power = machine.Pin(PIN_POWER_CTRL, machine.Pin.OUT, value=0)  # on only during a cycle
pump = machine.Pin(PIN_PUMP, machine.Pin.OUT, value=0)
soil_adc = machine.ADC(machine.Pin(PIN_SOIL))
soil_adc.atten(machine.ADC.ATTN_11DB)
bat_adc = machine.ADC(machine.Pin(PIN_BAT))
bat_adc.atten(machine.ADC.ATTN_11DB)
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


def read_soil_pct():
    """Return soil moisture as an integer percentage, 0 (dry) to 100 (wet).

    Capacitive sensors output a higher voltage (higher ADC value) when dry and a
    lower one when wet, so the reading is inverted and linearly mapped between
    two calibration points:

        pct = (SOIL_DRY_RAW - raw) * 100 / (SOIL_DRY_RAW - SOIL_WET_RAW)

    The result is clamped to 0-100, since readings can fall outside the
    calibrated range.

    Calibration: print soil_adc.read() with the sensor in dry air and set
    SOIL_DRY_RAW; then submerge it in water (up to the marked line) and set
    SOIL_WET_RAW. Using the full 0-4095 range instead would never reach 0% or
    100%, because real sensors only span part of the ADC range.
    """
    raw = soil_adc.read()
    pct = (SOIL_DRY_RAW - raw) * 100 / (SOIL_DRY_RAW - SOIL_WET_RAW)
    return max(0, min(100, round(pct)))


def read_battery_v():
    """Return the battery voltage in volts, rounded to 2 decimals.

    The battery is wired to the ADC pin through a voltage divider that halves
    it (the pin sees about Vbat / 2), because a LiPo can reach 4.2 V, above the
    ADC's ~3.3 V range. The conversion is:

        V = raw / 4095 * 6.6 * 1.1

    - raw / 4095 * 3.3 would be the voltage at the pin, so * 6.6 (3.3 * 2)
      undoes the divider and gives the battery voltage.
    - 1.1 is an empirical correction for the ESP32 ADC, which is not exactly
      linear at ATTN_11DB and does not match the nominal 3.3 V full scale.
      Compare against a multimeter and adjust it if the reading is off.
    """
    return round(bat_adc.read() / ADC_MAX * 6.6 * 1.1, 2)


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
    except OSError:
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


def pump_set(on):
    """Switch the pump on or off and track when it was turned on.

    pump_on_since feeds the max-on-time safety cutoff in the main loop.
    """
    global pump_on_since
    pump.value(1 if on else 0)
    pump_on_since = time.ticks_ms() if on else None
    print("pump", "on" if on else "off")


def on_message(topic_bytes, msg):
    """Handle an incoming MQTT control message.

    The command is the part of the topic after "/control/":

    - pump: "on" turns the pump on, anything else turns it off.
    - interval: new sleep time between cycles in seconds (at least MIN_INTERVAL_S).
    - pump-max-on: new pump safety cutoff in seconds.
    - listen: new control listen window in seconds (at least MIN_LISTEN_S).

    Invalid numbers are ignored. Control messages should be published retained,
    since the device is only listening briefly each cycle.

    Args:
        topic_bytes: Topic the message arrived on, as bytes.
        msg: Message payload, as bytes.
    """
    global publish_interval_s, pump_max_on_s, control_listen_s
    cmd = topic_bytes.decode().split("/control/", 1)[-1]
    msg = msg.decode().strip()
    try:
        if cmd == "pump":
            pump_set(msg == "on")
        elif cmd == "interval":
            publish_interval_s = max(MIN_INTERVAL_S, int(msg))
            print("publish interval:", publish_interval_s)
        elif cmd == "pump-max-on":
            pump_max_on_s = int(msg)
            print("pump max on:", pump_max_on_s)
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


def publish_readings():
    """Read all sensors and publish them, skipping any that are unavailable."""
    ts = iso_utc()
    if dht_sensor is not None:
        try:
            dht_sensor.measure()
            mqtt_client.publish(topic("dht/temperature"), str(dht_sensor.temperature()))
            mqtt_client.publish(topic("dht/humidity"), str(dht_sensor.humidity()))
            mqtt_client.publish(topic("dht/data-timestamp"), ts)
        except OSError as e:
            print("DHT read failed:", e)
    mqtt_client.publish(topic("soil/moisture"), str(read_soil_pct()))
    mqtt_client.publish(topic("soil/data-timestamp"), ts)
    mqtt_client.publish(topic("battery/voltage"), str(read_battery_v()))
    lux = read_lux()
    if lux is not None:
        mqtt_client.publish(topic("light/lux"), str(lux))


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


def wait_for_pump():
    """Stay awake while the pump runs, enforcing the pump_max_on_s cutoff."""
    while pump_on_since is not None:
        mqtt_client.check_msg()
        if time.ticks_diff(time.ticks_ms(), pump_on_since) >= pump_max_on_s * MS_PER_S:
            pump_set(False)
            mqtt_client.publish(topic("pump/state"), b"off")
        time.sleep_ms(100)


def run_cycle():
    """Run one awake cycle: connect, apply control messages, publish, disconnect.

    Always leaves the pump and the sensor power off, even on failure, so the
    caller can go straight to sleep.
    """
    global mqtt_client
    power.value(1)
    try:
        print("cycle: connecting MQTT")
        mqtt_client = connect_mqtt()
        print("cycle: listening for control")
        listen_for_control()
        print("cycle: publishing")
        publish_readings()
        wait_for_pump()
        mqtt_client.publish(topic("status"), b"sleeping", retain=True)
        mqtt_client.disconnect()
        print("cycle: done")
    except Exception as e:  # any failure must still end in sleep, not an idle awake board
        print("Cycle failed:", e)
    finally:
        pump_set(False)
        power.value(0)


while True:
    run_cycle()
    if DEEP_SLEEP:
        print("deep sleep for", publish_interval_s, "s")
        machine.deepsleep(publish_interval_s * MS_PER_S)  # wakes through boot.py
    time.sleep(publish_interval_s)
