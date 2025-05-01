import time
import os
import board
import busio
import wifi
import socketpool
import ssl

import adafruit_amg88xx
import adafruit_vl53l1x

from adafruit_minimqtt.adafruit_minimqtt import MQTT
from adafruit_io.adafruit_io import IO_MQTT

# === UART Setup for Roomba ===
uart = busio.UART(board.GP0, board.GP1, baudrate=115200)

def send_command(opcode, data=[]):
    try:
        uart.write(bytes([opcode] + data))
    except Exception as e:
        print("⚠️ UART write failed:", e)

# === Initialize Roomba ===
def initialize_roomba():
    print("🔋 Waking Roomba and entering Full mode...")
    send_command(128)  # Start
    time.sleep(0.1)
    send_command(131)  # Safe mode
    #send_command(132)  # Full mode, uncomment this and comment out the above to make your roomba more versatile. Beware, this can lead to massive battery problems
    time.sleep(0.1)
    # Test command: play a short beep to verify connection
    send_command(140, [3, 1, 64, 16])  # Define song 3
    send_command(141, [3])             # Play song 3
    print("✅ Roomba should now be in Full mode.")

# === I2C Setup for Sensors ===
i2c = busio.I2C(board.GP5, board.GP4)

# Thermal Camera Setup
amg = adafruit_amg88xx.AMG88XX(i2c)

# Distance Sensor Setup
vl53 = adafruit_vl53l1x.VL53L1X(i2c)
vl53.distance_mode = 2
vl53.timing_budget = 200
vl53.start_ranging()

# === Connect to Wi-Fi ===
print("Connecting to Wi-Fi...")
wifi.radio.connect(
    os.getenv("CIRCUITPY_WIFI_SSID"),
    os.getenv("CIRCUITPY_WIFI_PASSWORD")
)
print("✅ Connected to Wi-Fi")
print("📡 IP Address:", wifi.radio.ipv4_address)

# Play chime for Wi-Fi connection
send_command(140, [0, 2, 72, 32, 76, 32])
send_command(141, [0])

# Initialize Roomba
initialize_roomba()

# === MQTT Setup ===
aio_username = os.getenv("ADAFRUIT_AIO_USERNAME")
aio_key = os.getenv("ADAFRUIT_AIO_KEY")

pool = socketpool.SocketPool(wifi.radio)
mqtt_client = MQTT(
    broker="io.adafruit.com",
    username=aio_username,
    password=aio_key,
    socket_pool=pool,
    ssl_context=ssl.create_default_context(),
)
io = IO_MQTT(mqtt_client)

# === Movement State Flags ===
searching_left = False
searching_right = False
seeking_forward = False

search_start_time = None
search_direction = None

# === Roomba Movement Functions ===
def drive_left():
    send_command(137, [0x00, 0x64, 0x00, 0x01])

def drive_right():
    send_command(137, [0x00, 0x64, 0xFF, 0xFF])

def drive_forward():
    send_command(137, [0x00, 0xC8, 0x80, 0x00])

def drive_backward():
    send_command(137, [0xFF, 0x38, 0x80, 0x00])

def stop():
    send_command(137, [0x00, 0x00, 0x80, 0x00])

# === Sensor Checking Functions ===
def detect_human(threshold=24.0):
    thermal_data = amg.pixels
    for row in thermal_data:
        for temp in row:
            if temp >= threshold:
                return True
    return False

def obstacle_detected(threshold_mm=40):
    if vl53.data_ready:
        dist = vl53.distance
        if dist and dist <= threshold_mm:
            vl53.clear_interrupt()
            return True
    return False

# === Feed Handler ===
def handle_roomba_steering(client, feed_id, payload):
    global searching_left, searching_right, seeking_forward
    global search_start_time, search_direction

    print("Command received:", payload)

    if payload == "forward":
        searching_left = False
        searching_right = False
        seeking_forward = False
        stop()
        drive_forward()

    elif payload == "backward":
        searching_left = False
        searching_right = False
        seeking_forward = False
        stop()
        drive_backward()

    elif payload == "left":
        searching_left = False
        searching_right = False
        seeking_forward = False
        stop()
        drive_left()

    elif payload == "right":
        searching_left = False
        searching_right = False
        seeking_forward = False
        stop()
        drive_right()

    elif payload == "stop":
        searching_left = False
        searching_right = False
        seeking_forward = False
        stop()

    elif payload == "search_left":
        searching_left = True
        searching_right = False
        seeking_forward = False
        search_start_time = time.monotonic()
        search_direction = "left"

    elif payload == "search_right":
        searching_left = False
        searching_right = True
        seeking_forward = False
        search_start_time = time.monotonic()
        search_direction = "right"

    elif payload == "seek_forward":
        searching_left = False
        searching_right = False
        seeking_forward = True

# === MQTT Connection Handlers ===
def connected(client):
    print("🔌 Connected to Adafruit IO")
    io.subscribe("roomba-steering")

def disconnected(client):
    print("🔌 Disconnected from Adafruit IO")

# === Subscribe to Feed Handlers ===
io.on_connect = connected
io.on_disconnect = disconnected
io.add_feed_callback("roomba-steering", handle_roomba_steering)

# === Connect to Adafruit IO ===
print("Connecting to Adafruit IO...")
io.connect()
print("✅ Listening for Roomba commands...")

# Immediately stop Roomba after boot
stop()
print("🛑 Roomba motors stopped after boot")

# === Main Loop ===
while True:
    try:
        io.loop()

        if searching_left or searching_right:
            if searching_left:
                drive_left()
            if searching_right:
                drive_right()

            if detect_human():
                print("👤 Human detected! Stopping search.")
                stop()
                searching_left = False
                searching_right = False
                send_command(140, [1, 2, 76, 32, 72, 32])
                send_command(141, [1])

            time.sleep(0.2)

            if search_start_time and (time.monotonic() - search_start_time > 10):
                print("⏰ Search timeout. Stopping.")
                stop()
                searching_left = False
                searching_right = False

        if seeking_forward:
            drive_forward()
            if obstacle_detected():
                print("🚧 Obstacle detected! Stopping seek.")
                stop()
                seeking_forward = False
                print("🏆 Beer delivery successful! Playing victory chime.")
                send_command(140, [2, 3, 72, 32, 76, 32, 79, 32])
                send_command(141, [2])

        time.sleep(0.1)

    except Exception as e:
        print("⚠️ Error:", e)
        stop()
        time.sleep(5)
        io.reconnect()
