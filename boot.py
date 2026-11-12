import network
import ntptime
import time
import config


def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return wlan
    wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
    start = time.ticks_ms()
    while not wlan.isconnected():
        if time.ticks_diff(time.ticks_ms(), start) > config.WIFI_TIMEOUT_S * 1000:
            print("Wi-Fi timeout")
            return wlan
        time.sleep_ms(200)
    print("Wi-Fi:", wlan.ifconfig())
    return wlan


if connect_wifi().isconnected():
    try:
        ntptime.settime()
    except OSError as e:
        print("NTP failed:", e)
