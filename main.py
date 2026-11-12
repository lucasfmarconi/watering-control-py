import json
import machine
import dht
import ssl
import time
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
except Exception as e:
    print("DHT init failed:", e)
    dht_sensor = None

try:
    i2c = machine.I2C(0, sda=machine.Pin(PIN_I2C_SDA), scl=machine.Pin(PIN_I2C_SCL))
    i2c.writeto(BH1750_ADDR, b"\x10")  # continuous high-res mode
except Exception as e:
    print("BH1750 init failed:", e)
    i2c = None


def read_soil_pct():
    return round((4095 - soil_adc.read()) * 100 / 4095)


def read_battery_v():
    return round(bat_adc.read() / 4095 * 6.6 * 1.1, 2)


def read_lux():
    if i2c is None:
        return None
    try:
        raw = i2c.readfrom(BH1750_ADDR, 2)
        return round(((raw[0] << 8) | raw[1]) / 1.2, 1)
    except OSError:
        return None
# endregion

# region mqtt
client = None


def iso_utc():
    t = time.gmtime()
    return "%04d-%02d-%02dT%02d:%02d:%02dZ" % t[:6]


def topic(suffix):
    return "%s/%s" % (config.MQTT_TOPIC_BASE, suffix)


def pump_set(on):
    global pump_on_since
    pump.value(1 if on else 0)
    pump_on_since = time.ticks_ms() if on else None
    print("pump", "on" if on else "off")


def on_message(t, msg):
    t = t.decode()
    msg = msg.decode().strip()
    cmd = t.split("/control/", 1)[-1]
    if cmd == "pump":
        pump_set(msg == "on")
    elif cmd == "interval":
        global publish_interval_s
        publish_interval_s = int(msg)
        print("publish interval:", publish_interval_s)
    elif cmd == "pump-max-on":
        global pump_max_on_s
        pump_max_on_s = int(msg)
        print("pump max on:", pump_max_on_s)


def connect_mqtt():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.verify_mode = ssl.CERT_NONE  # broker cert NOT verified
    c = MQTTClient(
        config.MQTT_CLIENT_ID, config.MQTT_BROKER, port=config.MQTT_PORT,
        user=config.MQTT_USER, password=config.MQTT_PASSWORD,
        ssl=ctx, keepalive=60,
    )
    c.set_last_will(topic("status"), b"offline", retain=True)
    c.set_callback(on_message)
    c.connect()
    c.publish(topic("status"), b"online", retain=True)
    c.subscribe(config.MQTT_TOPIC_DEVICES_SUB)
    return c


def publish_readings():
    ts = iso_utc()
    if dht_sensor is not None:
        try:
            dht_sensor.measure()
            client.publish(topic("dht/temperature"), str(dht_sensor.temperature()))
            client.publish(topic("dht/humidity"), str(dht_sensor.humidity()))
            client.publish(topic("dht/data-timestamp"), ts)
        except OSError as e:
            print("DHT read failed:", e)
    client.publish(topic("soil/moisture"), str(read_soil_pct()))
    client.publish(topic("soil/data-timestamp"), ts)
    client.publish(topic("battery/voltage"), str(read_battery_v()))
    lux = read_lux()
    if lux is not None:
        client.publish(topic("light/lux"), str(lux))
# endregion


def recover():
    try:
        client.publish(topic("status"), b"offline", retain=True)
        client.disconnect()
    except Exception:
        pass
    machine.reset()


try:
    client = connect_mqtt()
except OSError as e:
    print("MQTT connect failed:", e)
    recover()

last_publish = time.ticks_add(time.ticks_ms(), -publish_interval_s * 1000)
while True:
    try:
        client.check_msg()
        now = time.ticks_ms()
        if time.ticks_diff(now, last_publish) >= publish_interval_s * 1000:
            publish_readings()
            last_publish = now
        if pump_on_since is not None and time.ticks_diff(now, pump_on_since) >= pump_max_on_s * 1000:
            pump_set(False)
            client.publish(topic("pump/state"), b"off")
    except OSError as e:
        print("MQTT error:", e)
        pump_set(False)
        recover()
    time.sleep_ms(100)
