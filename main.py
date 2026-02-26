"""
LED Alert Pole - MicroPython for wESP32
Controls LEDs and buzzer based on Zabbix alerts via HTTP API and MQTT.

Pins:
  - Outputs: Red (IO5), Yellow (IO13), Blue (IO14), White (IO18), Green (IO23), Buzzer (IO32)
  - Input: Switch (IO33)
  - Ethernet: MDC (IO16), MDIO (IO17)
"""

import uasyncio as asyncio
import network
import socket
import ujson
import machine
from machine import Pin
import time
import sys
import ubinascii

try:
    from umqtt.simple import MQTTClient
    print('umqtt.simple loaded successfully')
except ImportError:
    print('umqtt.simple not available; please install via: import upip; upip.install("micropython-umqtt.simple")')
    MQTTClient = None

try:
    import mdns
    print('mdns module loaded successfully')
except ImportError:
    print('mdns module not available; mDNS service advertisement disabled')
    mdns = None

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


def load_config():
    """Load configuration from file."""
    global mqtt_config
    try:
        with open('config.json', 'r') as f:
            loaded_config = ujson.load(f)
            mqtt_config.update(loaded_config)
            print('Loaded config:', mqtt_config)
    except Exception as e:
        print(f'Config load error: {e}')
        print('Using default config')


def save_config():
    """Save configuration to file."""
    try:
        with open('config.json', 'w') as f:
            ujson.dump(mqtt_config, f)
        print('Saved config:', mqtt_config)
    except Exception as e:
        print(f'Config save error: {e}')


# HTML help page
HELP_PAGE = (
    "HTTP/1.1 200 OK\r\n"
    "Content-Type: text/html\r\n"
    "\r\n"
    "<!DOCTYPE html>"
    "<html>"
    "<head><title>LED Alert Pole</title></head>"
    "<body>"
    "<h1>LED Alert Pole</h1>"
    "<p>Manages alerts via HTTP and MQTT, controlling LEDs and buzzer based on problem severity.</p>"
    "<p>Press the button once to silence the buzzer, or twice quickly to reset all alerts.</p>"
    "<ul>"
    "  <li><a href=\"/event\">POST /event</a>: Submit alerts (JSON: problemId, problemStatus, problemSeverity).</li>"
    "  <li><a href=\"/status\">GET /status</a>: View system status and active problems.</li>"
    "  <li><a href=\"/config\">GET/POST /config</a>: Configure MQTT settings and hostname.</li>"
    "</ul>"
    "</body>"
    "</html>"
)

# HTML config form template
CONFIG_FORM_TEMPLATE = (
    "<!DOCTYPE html>"
    "<html>"
    "<head><title>Configure LED Alert Pole</title></head>"
    "<body>"
    "<h1>Configure LED Alert Pole</h1>"
    "<form method=\"POST\" action=\"/config\">"
    "  <label>MQTT Server IP: <input type=\"text\" name=\"mqtt_server\" value=\"{mqtt_server}\"></label><br>"
    "  <label>MQTT Topic: <input type=\"text\" name=\"mqtt_topic\" value=\"{mqtt_topic}\"></label><br>"
    "  <label>MQTT Username: <input type=\"text\" name=\"mqtt_username\" value=\"{mqtt_username}\"></label><br>"
    "  <label>MQTT Password: <input type=\"password\" name=\"mqtt_password\"></label><br>"
    "  <label>Hostname: <input type=\"text\" name=\"hostname\" value=\"{hostname}\"></label><br>"
    "  <input type=\"submit\" value=\"Save\">"
    "</form>"
    "</body>"
    "</html>"
)


async def connect_ethernet():
    """Connect to Ethernet and configure hostname."""
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
            default_hostname = mqtt_config.get('hostname') or f'led-pole-{mac_str}'
            mqtt_config['client_id'] = f'led-pole-{mac_str}'
        except Exception as e:
            print(f'MAC address retrieval error: {e}')
            default_hostname = mqtt_config.get('hostname') or 'led-pole-default'
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


def setup_mdns(hostname):
    """Set up mDNS service advertisement for discovery via dns-sd -B."""
    if mdns is None:
        print('mDNS module not available, skipping service advertisement')
        return None
    try:
        mdns_server = mdns.Server(hostname)
        # Advertise HTTP service for discovery
        mdns_server.advertise('_http', '_tcp', port=80, txt={'path': '/', 'device': 'led-pole'})
        print(f'mDNS advertising: {hostname}.local, _http._tcp on port 80')
        return mdns_server
    except AttributeError:
        # Try alternative mdns API (older versions)
        try:
            mdns_server = mdns.MDNS()
            mdns_server.set_hostname(hostname)
            mdns_server.set_service('_http', '_tcp', 80)
            mdns_server.start()
            print(f'mDNS started (legacy API): {hostname}.local')
            return mdns_server
        except Exception as e:
            print(f'mDNS setup error (legacy): {e}')
            return None
    except Exception as e:
        print(f'mDNS setup error: {e}')
        return None


def mqtt_callback(topic, msg):
    """Handle incoming MQTT messages."""
    try:
        topic_str = topic.decode('utf-8', 'ignore')
        msg_str = msg.decode('utf-8', 'ignore')
        print(f'MQTT received: Topic={topic_str}, Message={msg_str}')
        try:
            data = ujson.loads(msg_str)
            print('Parsed MQTT message:', data)
            process_event(data)
        except ValueError as e:
            print(f'JSON parse error: {e}, Message={msg_str}')
    except Exception as e:
        print(f'MQTT message processing error: {e}, Raw Message={msg}')


async def init_mqtt():
    """Initialize MQTT client and subscribe to topic."""
    global mqtt_client
    if MQTTClient is None:
        print('MQTT not available: umqtt.simple not installed')
        mqtt_client = None
        return
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
        print(f'MQTT connected to {mqtt_config["server"]}:1883')
        mqtt_client.subscribe(mqtt_config['topic'].encode('utf-8'))
        print(f'Subscribed to topic: {mqtt_config["topic"]}')
    except Exception as e:
        print(f'MQTT initialization error: {e}')
        mqtt_client = None


async def mqtt_check():
    """Periodically check for MQTT messages."""
    global mqtt_client
    while True:
        try:
            if mqtt_client is None:
                print('MQTT client not initialized; attempting reconnect')
                await init_mqtt()
            else:
                mqtt_client.check_msg()
        except Exception as e:
            print(f'MQTT check error: {e}')
            mqtt_client = None
            await init_mqtt()
        await asyncio.sleep_ms(500)


async def buzzer_cycle():
    """Cycle buzzer: 2s ON, 58s OFF when active."""
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


def url_decode(s):
    """Decode URL-encoded string."""
    result = s.replace('+', ' ')
    i = 0
    decoded = []
    while i < len(result):
        if result[i] == '%' and i + 2 < len(result):
            try:
                hex_val = result[i+1:i+3]
                decoded.append(chr(int(hex_val, 16)))
                i += 3
                continue
            except ValueError:
                pass
        decoded.append(result[i])
        i += 1
    return ''.join(decoded)


def parse_post_request(request):
    """Parse HTTP POST request body (JSON or form-encoded)."""
    try:
        request_str = request.decode('utf-8', 'ignore')

        # Find Content-Length header
        content_length = 0
        for line in request_str.split('\r\n'):
            if line.lower().startswith('content-length:'):
                content_length = int(line.split(':')[1].strip())
                break

        # Extract body after headers
        body_start = request.find(b'\r\n\r\n') + 4
        body = request[body_start:body_start + content_length].decode('utf-8', 'ignore')
        print('Received POST body:', body)

        if 'application/json' in request_str.lower():
            return ujson.loads(body)

        # Parse form data
        form_data = {}
        for pair in body.split('&'):
            if '=' in pair:
                key, value = pair.split('=', 1)
                form_data[url_decode(key)] = url_decode(value)
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


def get_status(lan):
    """Get current system status."""
    # FIX: Use ticks_diff to calculate actual uptime from start_time
    uptime = time.ticks_diff(time.ticks_ms(), start_time) / 1000
    ip_address = lan.ifconfig()[0]
    led_states = {name: bool(pin.value()) for name, pin in LED_PINS.items()}
    status = {
        'uptime': uptime,
        'ip_address': ip_address,
        'active_problems': active_problems,
        'buzzer_silenced': buzzer_silenced,
        'buzzer_active': buzzer_active,
        'led_states': led_states,
        'mqtt_config': {
            'server': mqtt_config['server'],
            'topic': mqtt_config['topic'],
            'hostname': mqtt_config['hostname'],
            'client_id': mqtt_config['client_id']
            # Note: username/password excluded for security
        }
    }
    print('Status requested:', status)
    return status


def update_outputs():
    """Update LEDs and buzzer based on active problems."""
    global buzzer_silenced, buzzer_active
    try:
        # Reset all LEDs (including Green)
        for led in LED_PINS:
            if led != 'Buzzer':
                LED_PINS[led].off()

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


def process_event(data):
    """Process JSON event (shared by /event and MQTT)."""
    global buzzer_silenced
    try:
        problem_id = data.get('problemId')
        status = data.get('problemStatus')
        severity = data.get('problemSeverity')

        # FIX: Check for empty strings as well as None
        if not problem_id or not status or not severity:
            print(f'Invalid JSON data (missing required fields): {data}')
            return False

        if severity not in SEVERITY_TO_LED:
            print(f'Invalid severity value: {severity}')
            return False

        print(f'Processing event: ID={problem_id}, Status={status}, Severity={severity}')
        if status == 'started':
            active_problems[problem_id] = severity
        elif status == 'updated':
            if problem_id in active_problems:
                active_problems[problem_id] = severity
            else:
                print(f'Update ignored: problemId {problem_id} not in active problems')
                # Still return True - this is not an error, just informational
        elif status == 'acknowledged':
            # Acknowledge silences the buzzer but keeps the problem active
            buzzer_silenced = True
            LED_PINS['Buzzer'].off()
            print(f'Problem {problem_id} acknowledged: buzzer silenced')
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


def configure_settings(data, lan):
    """Configure MQTT and hostname settings."""
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
            mqtt_config['username'] = mqtt_username if mqtt_username else None
            mqtt_config['password'] = mqtt_password if mqtt_password else None
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


async def http_server(lan):
    """HTTP server for API endpoints."""
    try:
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('', 80))
        s.listen(5)
        s.setblocking(False)
        print('HTTP server started on port 80')
    except Exception as e:
        print(f'HTTP server setup error: {e}')
        return

    while True:
        conn = None
        try:
            conn, addr = s.accept()
            print('HTTP connection from:', addr)
            conn.settimeout(3.0)

            # FIX: Read headers first, then read body based on Content-Length
            request = b''
            while b'\r\n\r\n' not in request:
                chunk = conn.recv(1024)
                if not chunk:
                    break
                request += chunk

            # Check for Content-Length and read remaining body
            request_str = request.decode('utf-8', 'ignore')
            content_length = 0
            for line in request_str.split('\r\n'):
                if line.lower().startswith('content-length:'):
                    content_length = int(line.split(':')[1].strip())
                    break

            # Calculate how much body we already have
            header_end = request.find(b'\r\n\r\n') + 4
            body_received = len(request) - header_end
            remaining = content_length - body_received

            # Read remaining body if needed
            while remaining > 0:
                chunk = conn.recv(min(1024, remaining))
                if not chunk:
                    break
                request += chunk
                remaining -= len(chunk)

            conn.settimeout(None)
            print(f'HTTP request received, {len(request)} bytes')

            response = 'HTTP/1.1 400 Bad Request\r\n\r\n'
            if request.startswith(b'GET / ') or request.startswith(b'GET /index.html '):
                response = HELP_PAGE
                print('Serving / or /index.html')
            elif request.startswith(b'GET /config '):
                html = CONFIG_FORM_TEMPLATE.format(
                    mqtt_server=mqtt_config['server'] or '',
                    mqtt_topic=mqtt_config['topic'] or '',
                    mqtt_username=mqtt_config['username'] or '',
                    hostname=mqtt_config['hostname'] or mqtt_config['client_id']
                )
                response = 'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n' + html
                print('Serving /config')
            elif request.startswith(b'POST /event '):
                data = parse_post_request(request)
                if data and process_event(data):
                    response = 'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{"status":"ok"}'
                    print('Processed POST /event')
                else:
                    print('POST /event failed')
                    response = 'HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\n\r\n{"status":"error"}'
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
        except OSError:
            await asyncio.sleep_ms(100)
        except Exception as e:
            print(f'HTTP server error: {e}')
            await asyncio.sleep_ms(100)
        finally:
            # FIX: Ensure socket is always closed
            if conn:
                try:
                    conn.close()
                except:
                    pass


async def switch_handler():
    """Handle physical switch for silencing buzzer and resetting alerts."""
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
                    press_count = 1
                    print(f'Switch press detected, count: {press_count}')
                elif current_time - last_press < double_press_window:
                    # Within double press window
                    press_count += 1
                    print(f'Switch press detected, count: {press_count}')
                else:
                    # New press after window expired - process previous and start new
                    buzzer_silenced = True
                    LED_PINS['Buzzer'].off()
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
                    LED_PINS['Buzzer'].off()
                    print('Single press: Buzzer silenced')
                press_count = 0
                last_press = 0

            last_state = current_state
            await asyncio.sleep_ms(50)  # Debounce and polling interval
        except Exception as e:
            print(f'Switch handler error: {e}')
            await asyncio.sleep_ms(50)


async def main():
    """Main entry point."""
    try:
        load_config()
        lan = await connect_ethernet()
        # Set up mDNS for service discovery (dns-sd -B _http._tcp)
        hostname = mqtt_config.get('hostname') or mqtt_config.get('client_id') or 'led-pole'
        setup_mdns(hostname)
        await init_mqtt()
        asyncio.create_task(http_server(lan))
        asyncio.create_task(switch_handler())
        asyncio.create_task(mqtt_check())
        asyncio.create_task(buzzer_cycle())
        print('All tasks started, entering main loop')
        while True:
            await asyncio.sleep(1)
    except Exception as e:
        print(f'Main loop error: {e}')
        print('Restarting in 5 seconds...')
        await asyncio.sleep(5)
        machine.reset()


# Run the program
if __name__ == '__main__':
    try:
        print('MicroPython version:', sys.version)
        print('Starting LED Alert Pole...')
        asyncio.run(main())
    except KeyboardInterrupt:
        print('Program stopped by user')
    except Exception as e:
        print(f'Program error: {e}')
        print('Restarting in 5 seconds...')
        time.sleep(5)
        machine.reset()
