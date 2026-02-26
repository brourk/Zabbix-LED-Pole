Below is a MicroPython program for the wESP32 that implements the specified functionality. It uses a capacitive touch input to silence or reset alerts, controls six output pins for LEDs and a buzzer, and provides an API to process JSON input for managing alerts. The program avoids the restricted pins and uses asynchronous programming with uasyncio for efficient handling of the API and touch input.

### Program Overview

*     Touch Input: Pin IO33 (capacitive touch) detects single taps to silence the buzzer and double taps to reset all alerts.
*     Outputs: Controls LEDs (Red: IO5, Yellow: IO13, Blue: IO14, White: IO18, Green: IO23) and Buzzer (IO32).
*     API: Accessible at http://alert-pole.local/event, processes JSON input to manage alerts based on problemId, problemStatus, and problemSeverity.
*     Logic: Maintains a list of active problems, updates LEDs and buzzer based on severity, and handles resolved/updated statuses.
*     Dependencies: Uses MicroPython's uasyncio, machine, network, socket, and json modules, with mDNS for hostname resolution.

Code

```python
import uasyncio as asyncio
import network
import socket
import json
import machine
from machine import Pin, TouchPad
import mdns
import time

# Pin configuration
LED_PINS = {
    'Red': Pin(5, Pin.OUT),
    'Yellow': Pin(13, Pin.OUT),
    'Blue': Pin(14, Pin.OUT),
    'White': Pin(18, Pin.OUT),
    'Green': Pin(23, Pin.OUT),
    'Buzzer': Pin(32, Pin.OUT)
}

# Initialize all outputs to OFF
for pin in LED_PINS.values():
    pin.off()

# Capacitive touch on IO33
TOUCH_PIN = TouchPad(Pin(33))

# Severity to LED mapping
SEVERITY_TO_LED = {
    'Not classified': 'White',
    'Information': 'Blue',
    'Warning': 'Yellow',
    'Average': 'Yellow',
    'High': 'Red',
    'Disaster': 'Red'
}

# Active problems list: {problemId: severity}
active_problems = {}

# Buzzer state
buzzer_silenced = False

# Start time for uptime calculation
start_time = time.ticks_ms()

# Connect to Ethernet
def connect_ethernet():
    lan = network.LAN(
        mdc=machine.Pin(16),
        mdio=machine.Pin(17),
        power=None,
        phy_type=network.PHY_RTL8201,
        phy_addr=0
    )
    lan.active(1)
    while not lan.isconnected():
        time.sleep(1)
    print('Ethernet connected:', lan.ifconfig())
    return lan

# Set up mDNS
def setup_mdns():
    mdns_server = mdns.MDNS()
    mdns_server.set_hostname('alert-pole')
    mdns_server.set_service('_http', '_tcp', 80)
    mdns_server.start()
    print('mDNS started: alert-pole.local')

# Parse HTTP POST request
def parse_post_request(request):
    try:
        # Extract JSON body
        body_start = request.find(b'\r\n\r\n') + 4
        body = request[body_start:].decode()
        return json.loads(body)
    except:
        return None

# Get status information
def get_status(lan):
    uptime = time.ticks_ms() / 1000  # Uptime in seconds
    ip_address = lan.ifconfig()[0]
    led_states = {name: bool(pin.value()) for name, pin in LED_PINS.items()}
    return {
        'uptime': uptime,
        'ip_address': ip_address,
        'active_problems': active_problems,
        'buzzer_silenced': buzzer_silenced,
        'led_states': led_states
    }

# Update LEDs and buzzer based on active problems
def update_outputs():
    global buzzer_silenced
    # Reset all LEDs
    for led in ['Red', 'Yellow', 'Blue', 'White']:
        LED_PINS[led].off()
    LED_PINS['Green'].off()
    LED_PINS['Buzzer'].off()

    if not active_problems:
        # No active problems: Green LED ON
        LED_PINS['Green'].on()
        buzzer_silenced = False
        return

    # Determine highest severity
    severities = list(active_problems.values())
    highest_severity = 'Not classified'
    for severity in ['Disaster', 'High', 'Average', 'Warning', 'Information', 'Not classified']:
        if severity in severities:
            highest_severity = severity
            break

    # Turn on corresponding LED
    led_color = SEVERITY_TO_LED[highest_severity]
    LED_PINS[led_color].on()

    # Activate buzzer for High/Disaster if not silenced
    if highest_severity in ['High', 'Disaster'] and not buzzer_silenced:
        LED_PINS['Buzzer'].on()

# Process JSON event
def process_event(data):
    global buzzer_silenced
    problem_id = data.get('problemId')
    status = data.get('problemStatus')
    severity = data.get('problemSeverity')

    if not all([problem_id, status, severity]) or severity not in SEVERITY_TO_LED:
        return False

    if status == 'started':
        active_problems[problem_id] = severity
    elif status == 'updated':
        if problem_id in active_problems:
            active_problems[problem_id] = severity
    elif status == 'resolved':
        active_problems.pop(problem_id, None)
        buzzer_silenced = False  # Reset silence on resolve

    update_outputs()
    return True

# HTTP server
async def http_server(lan):
    s = socket.socket()
    s.bind(('', 80))
    s.listen(5)
    s.setblocking(False)

    while True:
        try:
            conn, addr = s.accept()
            conn.settimeout(3.0)
            request = conn.recv(1024)
            conn.settimeout(None)

            response = 'HTTP/1.1 400 Bad Request\r\n\r\n'
            if b'POST /event' in request:
                data = parse_post_request(request)
                if data and process_event(data):
                    response = 'HTTP/1.1 200 OK\r\n\r\n'
            elif b'GET /status' in request:
                status = get_status(lan)
                response = 'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n' + json.dumps(status)
            else:
                response = 'HTTP/1.1 404 Not Found\r\n\r\n'

            conn.send(response.encode())
            conn.close()
        except OSError:
            await asyncio.sleep(0.1)

# Touch handler
async def touch_handler():
    global buzzer_silenced
    last_touch = 0
    touch_count = 0
    touch_threshold = 300  # Adjust based on testing
    double_tap_window = 0.5  # 500ms window for double tap

    while True:
        touch_value = TOUCH_PIN.read()
        current_time = time.ticks_ms() / 1000

        if touch_value < touch_threshold:
            if last_touch == 0:  # New touch detected
                last_touch = current_time
                touch_count += 1
            elif current_time - last_touch > double_tap_window:
                # Single tap detected after window
                if touch_count == 1:
                    buzzer_silenced = True
                    LED_PINS['Buzzer'].off()
                touch_count = 1
                last_touch = current_time
        else:
            if last_touch and current_time - last_touch > double_tap_window:
                if touch_count >= 2:
                    # Double tap: reset all alerts
                    active_problems.clear()
                    buzzer_silenced = False
                    update_outputs()
                elif touch_count == 1:
                    # Single tap: silence buzzer
                    buzzer_silenced = True
                    LED_PINS['Buzzer'].off()
                touch_count = 0
                last_touch = 0

        await asyncio.sleep(0.05)

# Main function
async def main():
    lan = connect_ethernet()
    setup_mdns()
    asyncio.create_task(http_server(lan))
    asyncio.create_task(touch_handler())
    while True:
        await asyncio.sleep(1)

# Run the program
try:
    asyncio.run(main())
except KeyboardInterrupt:
    print('Program stopped')

```

### Explanation

*     Pin Configuration:
	*         Outputs: Red LED (IO5), Yellow LED (IO13), Blue LED (IO14), White LED (IO18), Green LED (IO23), Buzzer (IO32).
	*         Input: Capacitive touch on IO33.
	*         All restricted pins (IO0, IO16, IO17, IO19, IO21, IO22, IO25, IO26, IO27) are avoided.
*     Wi-Fi and mDNS:
	*         Connects to Wi-Fi (replace 'your_ssid' and 'your_password' with your credentials).
	*         Sets up mDNS to make the device accessible at alert-pole.local.
*     API Server:
	*         Listens for POST requests at /event.
		*         Parses JSON input and processes problemId, problemStatus, and problemSeverity.
*         Updates the active_problems dictionary and controls LEDs/buzzer accordingly.
*     Touch Handling:
	*         Uses TouchPad on IO33 to detect touches.
	*         Single tap: Silences the buzzer (buzzer_silenced = True).
	*         Double tap: Clears active_problems and resets outputs (Green LED ON, others OFF).
	*         Implements a 500ms window for double-tap detection.
*     Alert Logic:
	*         Started: Adds problem to active_problems and updates outputs.
	*         Updated: Updates severity for the problem ID.
	*         Resolved: Removes problem ID and resets buzzer silence.
	*         After each event, checks the highest severity to set the appropriate LED and buzzer state.
	*         If no problems remain, turns on Green LED and turns off others.
*     Output Management:
	*         Maps severities to LEDs as specified.
	*         Buzzer activates for High/Disaster unless silenced.
	*         Ensures only the LED for the highest severity is ON.

### Notes

*     Wi-Fi Credentials: Update the SSID and password in connect_wifi().
*     Touch Calibration: The touch_threshold (300) may need adjustment based on your setup. Test the touch sensor with TOUCH_PIN.read() to find the appropriate value.
*     Dependencies: Ensure the wESP32 has MicroPython with uasyncio, mdns, and other required modules. You may need to install mdns via upip.
*     Testing: Use a tool like curl to test the API:

```bash
curl -X POST -H "Content-Type: application/json" -d '{"problemId":"60260","problemStatus":"started","problemSeverity":"High"}' http://alert-pole.local/event
```
*		Test Status :

```bash
curl http://alert-pole.local/status
```
 
*     Error Handling: The program includes basic error handling for malformed JSON or invalid requests.
*     Asyncio: Uses uasyncio for non-blocking operation, allowing simultaneous API and touch handling.

This program should meet all specified requirements while adhering to the wESP32's capabilities and pin restrictions. Let me know if you need further clarification or modifications!




Below is the updated MicroPython program for the wESP32, modified to use the Ethernet port (via the PHY interface) instead of Wi-Fi. The program retains all the original functionality, including the capacitive touch input on IO33, six output pins (IO5, IO13, IO14, IO18, IO23, IO32), and the JSON-based API at http://alert-pole.local/event. The Wi-Fi code is replaced with Ethernet initialization using the provided LAN configuration, despite the use of restricted pins IO16 and IO17 for MDC and MDIO, as they are necessary for the Ethernet PHY.

### Key Changes

*     Replaced Wi-Fi connection with Ethernet setup using network.LAN:

```python
 
import network
import machine
lan = network.LAN(mdc=machine.Pin(16), mdio=machine.Pin(17),power=None, phy_type=network.PHY_RTL8201, phy_addr=0)
lan.active(1)
```
 
*     Note: IO16 and IO17 are used for MDC and MDIO, as specified in your Ethernet configuration, even though they were listed as restricted. If this is problematic, please clarify alternative pins or confirm if these are acceptable for Ethernet.
*     Kept mDNS setup for alert-pole.local resolution over Ethernet.
*     All other functionality (touch input, API, LED/buzzer control) remains unchanged.


### Explanation of Changes

*     Ethernet Setup:
	*         The connect_ethernet function initializes the LAN interface using the provided configuration:
		*             mdc: Pin 16
		*             mdio: Pin 17
		*             phy_type: RTL8201 (as specified for wESP32)
		*             phy_addr: 0
		*             power: None (no power control pin)
	*         Activates the LAN and waits for a connection, printing the IP configuration.
*     Pin Usage:
	*         Conflict Note: Your original request restricted IO16 and IO17, but the Ethernet configuration you provided uses these pins for MDC and MDIO. This is standard for the wESP32's Ethernet PHY (RTL8201). If you need to avoid IO16 and IO17, please specify alternative pins or confirm that these pins are acceptable for Ethernet use.
	*         All other pins remain as specified: outputs on IO5, IO13, IO14, IO18, IO23, IO32; touch input on IO33; and no use of IO0, IO19, IO21, IO22, IO25, IO26, IO27.
*     mDNS:
	*         Retained mDNS setup to ensure the device is accessible at http://alert-pole.local/event over Ethernet.
*     Unchanged Functionality:
	*         Touch input (IO33) for single-tap (silence buzzer) and double-tap (reset alerts).
	*         Outputs for Red, Yellow, Blue, White, Green LEDs, and Buzzer.
	*         API handling JSON input with problemId, problemStatus, and problemSeverity.
	*         Logic for managing active problems, updating LEDs based on highest severity, and controlling the buzzer.

### Notes

*     Ethernet Configuration: Ensure the wESP32 is connected to an Ethernet network. The program assumes a DHCP server assigns an IP address. If you need a static IP, modify connect_ethernet with lan.config('ip', 'your_static_ip').
*     Pin Conflict: If IO16 and IO17 must be avoided, you may need a different Ethernet PHY configuration or external hardware. The wESP32's RTL8201 typically requires these pins for MDC/MDIO. Please clarify if this is an issue.
*     Touch Calibration: As before, adjust touch_threshold (default 300) based on testing with TOUCH_PIN.read().
*     Testing the API: Use a tool like curl to send POST requests:

```bash
curl -X POST -H "Content-Type: application/json" -d '{"problemId":"60260","problemStatus":"started","problemSeverity":"High"}' http://alert-pole.local/event
```

*     Dependencies: Ensure MicroPython includes uasyncio, mdns, and Ethernet support for the RTL8201 PHY. You may need to install mdns via upip.

### Testing

*     Connect the wESP32 to an Ethernet network.
*     Deploy the program to the wESP32.
*     Verify Ethernet connectivity with print(lan.ifconfig()).
*     Access the API at http://alert-pole.local/event and test JSON inputs.
*     Test touch input on IO33 for single and double taps.

If you have further requirements (e.g., avoiding IO16/IO17, static IP, or additional features), please let me know!



Modified to add some basic stats :

```json
{
  "uptime": 123.45,
  "ip_address": "192.168.1.100",
  "active_problems": {"60260": "High", "60261": "Warning"},
  "buzzer_silenced": true,
  "led_states": {
    "Red": false,
    "Yellow": true,
    "Blue": false,
    "White": false,
    "Green": false,
    "Buzzer": false
  }
}
```




New Version with MQTT

```python
import uasyncio as asyncio
import network
import socket
import ujson
import machine
from machine import Pin
import time
import sys
import ubinascii
import os
try:
    from umqtt.simple import MQTTClient
    print('umqtt.simple loaded successfully')
except ImportError:
    print('umqtt.simple not available; please install via: import upip; upip.install("micropython-umqtt.simple")')
    raise ImportError('MQTT requires umqtt.simple')

# Pin configuration
LED_PINS = {
    'Red': Pin(5, Pin.OUT),
    'Yellow': Pin(13, Pin.OUT),
    'Blue': Pin(14, Pin.OUT),
    'White': Pin(18, Pin.OUT),
    'Green': Pin(23, Pin.OUT),
    'Buzzer': Pin(32, Pin.OUT)
}

# Initialize all outputs to OFF
for name, pin in LED_PINS.items():
    try:
        pin.off()
        print(f'Initialized {name} pin to OFF')
    except Exception as e:
        print(f'Error initializing {name} pin:', e)

# Normally open switch on IO33 (pulled up, active low)
try:
    SWITCH_PIN = Pin(33, Pin.IN, Pin.PULL_UP)
    print('Switch initialized on IO33')
except Exception as e:
    print('Switch initialization error:', e)

# Severity to LED mapping
SEVERITY_TO_LED = {
    'Not classified': 'White',
    'Information': 'Blue',
    'Warning': 'Yellow',
    'Average': 'Yellow',
    'High': 'Red',
    'Disaster': 'Red'
}

# Active problems list: {problemId: severity}
active_problems = {}

# Buzzer state
buzzer_silenced = False
buzzer_active = False  # Tracks if buzzer should be cycling

# Start time for uptime calculation
start_time = time.ticks_ms()

# MQTT configuration (defaults)
mqtt_config = {
    'server': '208.65.23.45',
    'topic': 'zabbixEvents',
    'username': None,
    'password': None,
    'client_id': 'led-pole-default',  # Updated after Ethernet setup
    'hostname': None
}
mqtt_client = None

# Load configuration from file
def load_config():
    global mqtt_config
    try:
        with open('config.json', 'r') as f:
            loaded_config = ujson.load(f)
            mqtt_config.update(loaded_config)
            print('Loaded config:', mqtt_config)
    except Exception as e:
        print(f'Config load error: {e}')
        print('Using default config')

# Save configuration to file
def save_config():
    try:
        with open('config.json', 'w') as f:
            ujson.dump(mqtt_config, f)
        print('Saved config:', mqtt_config)
    except Exception as e:
        print(f'Config save error: {e}')

# HTML help page
HELP_PAGE = """\
HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n
<!DOCTYPE html>
<html>
<head><title>LED Alert Pole</title></head>
<body>
<h1>LED Alert Pole</h1>
<p>Manages alerts via HTTP and MQTT, controlling LEDs and buzzer based on problem severity.</p>
<p>Press the button once to silence the buzzer, or twice quickly to reset all alerts.</p>
<ul>
  <li><a href="/event">POST /event</a>: Submit alerts (JSON: problemId, problemStatus, problemSeverity).</li>
  <li><a href="/status">GET /status</a>: View system status and active problems.</li>
  <li><a href="/config">GET/POST /config</a>: Configure MQTT settings and hostname.</li>
</ul>
</body>
</html>
"""

# HTML config form
CONFIG_FORM = """\
HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n
<!DOCTYPE html>
<html>
<head><title>Configure LED Alert Pole</title></head>
<body>
<h1>Configure LED Alert Pole</h1>
<form method="POST" action="/config">
  <label>MQTT Server IP: <input type="text" name="mqtt_server" value="{mqtt_server}"></label><br>
  <label>MQTT Topic: <input type="text" name="mqtt_topic" value="{mqtt_topic}"></label><br>
  <label>MQTT Username: <input type="text" name="mqtt_username" value="{mqtt_username}"></label><br>
  <label>MQTT Password: <input type="password" name="mqtt_password"></label><br>
  <label>Hostname: <input type="text" name="hostname" value="{hostname}"></label><br>
  <input type="submit" value="Save">
</form>
</body>
</html>
"""

# Connect to Ethernet
async def connect_ethernet():
    global mqtt_config
    try:
        lan = network.LAN(
            mdc=machine.Pin(16),
            mdio=machine.Pin(17),
            power=None,
            phy_type=network.PHY_RTL8201,
            phy_addr=0
        )
        # Get MAC address
        try:
            mac = lan.config('mac')
            mac_str = ubinascii.hexlify(mac[-4:]).decode()
            default_hostname = mqtt_config.get('hostname', f'led-pole-{mac_str}')
            mqtt_config['client_id'] = f'led-pole-{mac_str}'
        except Exception as e:
            print(f'MAC address retrieval error: {e}')
            default_hostname = mqtt_config.get('hostname', 'led-pole-default')
            mqtt_config['client_id'] = 'led-pole-default'
        lan.config(hostname=default_hostname)
        print(f'Set hostname: {default_hostname}')
        lan.active(1)
        print('Ethernet LAN activated')
        timeout = time.ticks_ms() + 10000
        while not lan.isconnected() and time.ticks_diff(time.ticks_ms(), timeout) < 0:
            print('Waiting for Ethernet connection...')
            await asyncio.sleep_ms(100)
        if not lan.isconnected():
            raise OSError('Ethernet connection failed')
        ip_config = lan.ifconfig()
        print('Ethernet connected:', ip_config)
        return lan
    except Exception as e:
        print(f'Ethernet setup error: {e}')
        raise

# MQTT callback
def mqtt_callback(topic, msg):
    try:
        topic_str = topic.decode('utf-8', 'ignore')
        msg_str = msg.decode('utf-8', 'ignore')
        print(f'MQTT received: Topic={topic_str}, Message={msg_str}, Raw={msg}')
        try:
            data = ujson.loads(msg_str)
            print('Parsed MQTT message:', data)
            process_event(data)
        except ValueError as e:
            print(f'JSON parse error: {e}, Message={msg_str}')
    except Exception as e:
        print(f'MQTT message processing error: {e}, Raw Message={msg}')

# Initialize MQTT client
async def init_mqtt():
    global mqtt_client
    try:
        if not mqtt_config['server'] or not mqtt_config['topic']:
            print('MQTT not configured: server or topic missing')
            mqtt_client = None
            return
        print(f'Attempting MQTT connection: server={mqtt_config["server"]}, port=1883, topic={mqtt_config["topic"]}, client_id={mqtt_config["client_id"]}, user={mqtt_config["username"]}')
        mqtt_client = MQTTClient(
            client_id=mqtt_config['client_id'],
            server=mqtt_config['server'],
            user=mqtt_config['username'],
            password=mqtt_config['password'],
            port=1883
        )
        mqtt_client.set_callback(mqtt_callback)
        mqtt_client.connect()
        print(f'MQTT connected to {mqtt_config["server"]}:{1883}')
        mqtt_client.subscribe(mqtt_config['topic'].encode('utf-8'))
        print(f'Subscribed to topic: {mqtt_config["topic"]}')
    except Exception as e:
        print(f'MQTT initialization error: {e}')
        mqtt_client = None

# Check MQTT messages
async def mqtt_check():
    global mqtt_client
    while True:
        try:
            if mqtt_client is None:
                print('MQTT client not initialized; attempting reconnect')
                await init_mqtt()
            else:
                mqtt_client.check_msg()
                print('MQTT check performed')
        except Exception as e:
            print(f'MQTT check error: {e}')
            mqtt_client = None
            await init_mqtt()
        await asyncio.sleep_ms(500)

# Buzzer cycling (2s ON, 58s OFF)
async def buzzer_cycle():
    global buzzer_active, buzzer_silenced
    while True:
        if buzzer_active and not buzzer_silenced:
            LED_PINS['Buzzer'].on()
            print('Buzzer ON (2s)')
            await asyncio.sleep(2)
            if buzzer_active and not buzzer_silenced:
                LED_PINS['Buzzer'].off()
                print('Buzzer OFF (58s)')
                await asyncio.sleep(58)
        else:
            LED_PINS['Buzzer'].off()
            await asyncio.sleep_ms(100)

# Parse HTTP POST request
def parse_post_request(request):
    try:
        body_start = request.find(b'\r\n\r\n') + 4
        body = request[body_start:].decode('utf-8', 'ignore')
        print('Received POST body:', body)
        if 'Content-Type: application/json' in request.decode('utf-8', 'ignore'):
            return ujson.loads(body)
        form_data = {}
        for pair in body.split('&'):
            if '=' in pair:
                key, value = pair.split('=', 1)
                form_data[key] = value.replace('+', ' ').replace('%20', ' ')
        print('Parsed form data:', form_data)
        return {
            'mqtt_server': form_data.get('mqtt_server', ''),
            'mqtt_topic': form_data.get('mqtt_topic', ''),
            'mqtt_username': form_data.get('mqtt_username', ''),
            'mqtt_password': form_data.get('mqtt_password', ''),
            'hostname': form_data.get('hostname', '')
        }
    except Exception as e:
        print(f'POST parsing error: {e}')
        return None

# Get status information
def get_status(lan):
    uptime = time.ticks_ms() / 1000
    ip_address = lan.ifconfig()[0]
    led_states = {name: bool(pin.value()) for name, pin in LED_PINS.items()}
    status = {
        'uptime': uptime,
        'ip_address': ip_address,
        'active_problems': active_problems,
        'buzzer_silenced': buzzer_silenced,
        'buzzer_active': buzzer_active,
        'led_states': led_states,
        'mqtt_config': mqtt_config
    }
    print('Status requested:', status)
    return status

# Update LEDs and buzzer
def update_outputs():
    global buzzer_silenced, buzzer_active
    try:
        for led in ['Red', 'Yellow', 'Blue', 'White']:
            LED_PINS[led].off()
        LED_PINS['Green'].off()

        if not active_problems:
            LED_PINS['Green'].on()
            buzzer_silenced = False
            buzzer_active = False
            print('No active problems; Green LED ON')
            return

        severities = list(active_problems.values())
        highest_severity = 'Not classified'
        for severity in ['Disaster', 'High', 'Average', 'Warning', 'Information', 'Not classified']:
            if severity in severities:
                highest_severity = severity
                break

        led_color = SEVERITY_TO_LED[highest_severity]
        LED_PINS[led_color].on()
        print(f'Highest severity: {highest_severity}, LED: {led_color} ON')

        buzzer_active = highest_severity in ['High', 'Disaster'] and not buzzer_silenced
    except Exception as e:
        print(f'Output update error: {e}')

# Process JSON event (shared by /event and MQTT)
def process_event(data):
    global buzzer_silenced
    try:
        problem_id = data.get('problemId')
        status = data.get('problemStatus')
        severity = data.get('problemSeverity')

        if not all([problem_id, status, severity]) or severity not in SEVERITY_TO_LED:
            print(f'Invalid JSON data: {data}')
            return False

        print(f'Processing event: ID={problem_id}, Status={status}, Severity={severity}')
        if status == 'started':
            active_problems[problem_id] = severity
        elif status == 'updated':
            if problem_id in active_problems:
                active_problems[problem_id] = severity
            else:
                print(f'Update failed: problemId {problem_id} not found')
                return False
        elif status == 'resolved':
            active_problems.pop(problem_id, None)
            buzzer_silenced = False
        else:
            print(f'Invalid problemStatus: {status}')
            return False

        update_outputs()
        return True
    except Exception as e:
        print(f'Event processing error: {e}')
        return False

# Configure MQTT and hostname
def configure_settings(data, lan):
    try:
        global mqtt_config, mqtt_client
        mqtt_server = data.get('mqtt_server')
        mqtt_topic = data.get('mqtt_topic')
        mqtt_username = data.get('mqtt_username')
        mqtt_password = data.get('mqtt_password')
        hostname = data.get('hostname')

        if mqtt_server and mqtt_topic:
            mqtt_config['server'] = mqtt_server
            mqtt_config['topic'] = mqtt_topic
            mqtt_config['username'] = mqtt_username
            mqtt_config['password'] = mqtt_password
            print(f'MQTT configured: server={mqtt_server}, topic={mqtt_topic}')
            if mqtt_client:
                try:
                    mqtt_client.disconnect()
                except:
                    pass
            mqtt_client = None
            save_config()
            asyncio.create_task(init_mqtt())
        else:
            print(f'Invalid MQTT config: {data}')
            return False

        if hostname:
            try:
                mqtt_config['hostname'] = hostname
                lan.config(hostname=hostname)
                print(f'Hostname set to: {hostname}')
                save_config()
            except Exception as e:
                print(f'Hostname set error: {e}')
                return False

        return True
    except Exception as e:
        print(f'Configuration error: {e}')
        return False

# HTTP server
async def http_server(lan):
    try:
        s = socket.socket()
        s.bind(('', 80))
        s.listen(5)
        s.setblocking(False)
        print('HTTP server started on port 80')
    except Exception as e:
        print(f'HTTP server setup error: {e}')
        return

    while True:
        try:
            conn, addr = s.accept()
            print('HTTP connection from:', addr)
            conn.settimeout(3.0)
            request = b''
            while True:
                chunk = conn.recv(1024)
                if not chunk:
                    break
                request += chunk
                if b'\r\n\r\n' in request:
                    break
            conn.settimeout(None)
            request_str = request.decode('utf-8', 'ignore')
            print(f'HTTP request: {request_str}')

            response = 'HTTP/1.1 400 Bad Request\r\n\r\n'
            if request.startswith(b'GET / ') or request.startswith(b'GET /index.html '):
                response = HELP_PAGE
                print('Serving / or /index.html')
            elif request.startswith(b'GET /config '):
                response = CONFIG_FORM.format(
                    mqtt_server=mqtt_config['server'] or '',
                    mqtt_topic=mqtt_config['topic'] or '',
                    mqtt_username=mqtt_config['username'] or '',
                    mqtt_password=mqtt_config['password'] or '',
                    hostname=mqtt_config['hostname'] or mqtt_config['client_id']
                )
                print('Serving /config')
            elif request.startswith(b'POST /event '):
                data = parse_post_request(request)
                if data and process_event(data):
                    response = 'HTTP/1.1 200 OK\r\n\r\n'
                    print('Processed POST /event')
                else:
                    print('POST /event failed')
                    response = 'HTTP/1.1 400 Bad Request\r\n\r\n'
            elif request.startswith(b'GET /status '):
                status = get_status(lan)
                response = 'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n' + ujson.dumps(status)
                print('Serving /status')
            elif request.startswith(b'POST /config '):
                data = parse_post_request(request)
                if data and configure_settings(data, lan):
                    response = 'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\nConfiguration saved. <a href="/config">Back</a>'
                    print('Processed POST /config')
                else:
                    print('POST /config failed')
                    response = 'HTTP/1.1 400 Bad Request\r\n\r\n'
            else:
                response = 'HTTP/1.1 404 Not Found\r\n\r\n'
                print('Serving 404 Not Found')

            conn.send(response.encode('utf-8'))
            conn.close()
        except OSError:
            await asyncio.sleep_ms(100)
        except Exception as e:
            print(f'HTTP server error: {e}')
            await asyncio.sleep_ms(100)

# Switch handler
async def switch_handler():
    global buzzer_silenced
    last_press = 0
    press_count = 0
    double_press_window = 0.5  # 500ms window for double press
    last_state = 1  # Assume switch is open (high) initially

    while True:
        try:
            current_state = SWITCH_PIN.value()  # 0 when pressed (low), 1 when open (high)
            current_time = time.ticks_ms() / 1000

            # Detect falling edge (switch pressed)
            if current_state == 0 and last_state == 1:
                if last_press == 0:  # First press
                    last_press = current_time
                    press_count += 1
                    print(f'Switch press detected, count: {press_count}')
                elif current_time - last_press < double_press_window:
                    # Within double press window
                    press_count += 1
                    print(f'Switch press detected, count: {press_count}')
                else:
                    # New press after window
                    if press_count == 1:
                        buzzer_silenced = True
                        print('Single press: Buzzer silenced')
                    press_count = 1
                    last_press = current_time
            # Detect if window has passed without new press
            elif last_press and current_time - last_press > double_press_window:
                if press_count >= 2:
                    active_problems.clear()
                    buzzer_silenced = False
                    update_outputs()
                    print('Double press: Alerts reset')
                elif press_count == 1:
                    buzzer_silenced = True
                    print('Single press: Buzzer silenced')
                press_count = 0
                last_press = 0

            last_state = current_state
            await asyncio.sleep_ms(50)  # Debounce and polling interval
        except Exception as e:
            print(f'Switch handler error: {e}')
            await asyncio.sleep_ms(50)

# Main function
async def main():
    try:
        load_config()
        lan = await connect_ethernet()
        await init_mqtt()
        asyncio.create_task(http_server(lan))
        asyncio.create_task(switch_handler())
        asyncio.create_task(mqtt_check())
        asyncio.create_task(buzzer_cycle())
        while True:
            await asyncio.sleep(1)
    except Exception as e:
        print(f'Main loop error: {e}')
        print('Restarting in 5 seconds...')
        await asyncio.sleep(5)
        machine.reset()

# Run the program
try:
    print('MicroPython version:', sys.version)
    print('Starting main loop...')
    asyncio.run(main())
except KeyboardInterrupt:
    print('Program stopped by user')
except Exception as e:
    print(f'Program error: {e}')
    print('Restarting in 5 seconds...')
    time.sleep(5)
    machine.reset()
           
```


Install umqtt.simple (if not already done):

```
import upip
upip.install('micropython-umqtt.simple')
```

Configure MQTT/hostname:

```
curl -X POST -H "Content-Type: application/json" -d '{"mqtt_server":"192.168.1.100","mqtt_topic":"alerts","mqtt_username":"user","mqtt_password":"pass","hostname":"custom-led-pole"}' http://<IP>/config
```

Event:

```
curl -X POST -H "Content-Type: application/json" -d '{"problemId":"60260","problemStatus":"started","problemSeverity":"High"}' http://<IP>/event
```

Test MQTT

```
mosquitto_pub -h 192.168.1.100 -t alerts -m '{"problemId":"60260","problemStatus":"started","problemSeverity":"High"}'
```
