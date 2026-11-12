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
CA_FILE = "emqxsl-ca.der"  # DigiCert Global Root G2, DER
ADC_MAX = 4095  # 12-bit ADC full scale
MS_PER_S = 1000

# Soil sensor calibration (raw 12-bit ADC). Placeholder values: measure your own
# sensor (see read_soil_pct) and replace them.
SOIL_DRY_RAW = 2800  # sensor held in dry air
SOIL_WET_RAW = 1200  # sensor submerged in water up to the marked line
# endregion

# region init
publish_interval_s = config.PUBLISH_INTERVAL_S
pump_max_on_s = config.PUMP_MAX_ON_S
pump_on_since = None

power = machine.Pin(PIN_POWER_CTRL, machine.Pin.OUT, value=1)
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
    i2c.writeto(BH1750_ADDR, b"\x10")  # continuous high-res mode
except (OSError, ValueError) as e:
    print("BH1750 init failed:", e)
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

    The sensor returns a 16-bit big-endian count in continuous high-resolution
    mode; dividing by 1.2 converts it to lux (per the BH1750 datasheet).
    """
    if i2c is None:
        return None
    try:
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
    - interval: new publish interval in seconds.
    - pump-max-on: new pump safety cutoff in seconds.

    Args:
        topic_bytes: Topic the message arrived on, as bytes.
        msg: Message payload, as bytes.
    """
    global publish_interval_s, pump_max_on_s
    cmd = topic_bytes.decode().split("/control/", 1)[-1]
    msg = msg.decode().strip()
    if cmd == "pump":
        pump_set(msg == "on")
    elif cmd == "interval":
        publish_interval_s = int(msg)
        print("publish interval:", publish_interval_s)
    elif cmd == "pump-max-on":
        pump_max_on_s = int(msg)
        print("pump max on:", pump_max_on_s)


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


def recover():
    """Mark the device offline, disconnect, and reset the board.

    Errors while disconnecting are ignored: the connection is usually already
    broken, and the reset follows regardless.
    """
    try:
        mqtt_client.publish(topic("status"), b"offline", retain=True)
        mqtt_client.disconnect()
    except Exception:
        pass
    machine.reset()


try:
    mqtt_client = connect_mqtt()
except OSError as e:
    print("MQTT connect failed:", e)
    recover()

last_publish = time.ticks_add(time.ticks_ms(), -publish_interval_s * MS_PER_S)
while True:
    try:
        mqtt_client.check_msg()
        now = time.ticks_ms()
        if time.ticks_diff(now, last_publish) >= publish_interval_s * MS_PER_S:
            publish_readings()
            last_publish = now
        if pump_on_since is not None and time.ticks_diff(now, pump_on_since) >= pump_max_on_s * MS_PER_S:
            pump_set(False)
            mqtt_client.publish(topic("pump/state"), b"off")
    except OSError as e:
        print("MQTT error:", e)
        pump_set(False)
        recover()
    time.sleep_ms(100)
