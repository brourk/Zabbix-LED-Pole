# LED Alert Pole

A MicroPython-based alert notification system for wESP32 that displays Zabbix problem severities using colored LEDs and an audible buzzer. Receives alerts via HTTP API or MQTT.

## Features

- Visual alerts with color-coded LEDs based on problem severity
- Audible buzzer alarm for High/Disaster severity (2s ON, 58s OFF cycle)
- Physical button to silence buzzer or reset all alerts
- HTTP API for receiving events and viewing status
- MQTT subscription for real-time Zabbix events
- Web-based configuration interface
- mDNS service discovery

## Hardware Requirements

- **wESP32** (ESP32 with Ethernet)
- **LEDs**: Red, Yellow, Blue, White, Green (active high)
- **Buzzer**: Active buzzer or relay-controlled horn
- **Switch**: Normally open momentary push button

## Pin Configuration

| Function | GPIO Pin |
|----------|----------|
| Red LED | IO5 |
| Yellow LED | IO13 |
| Blue LED | IO14 |
| White LED | IO18 |
| Green LED | IO23 |
| Buzzer | IO32 |
| Switch | IO33 (pulled up) |
| Ethernet MDC | IO16 |
| Ethernet MDIO | IO17 |

## Severity to LED Mapping

| Zabbix Severity | LED Color |
|-----------------|-----------|
| Disaster | Red |
| High | Red |
| Average | Yellow |
| Warning | Yellow |
| Information | Blue |
| Not classified | White |
| No problems | Green |

## Installation

### 1. Install MicroPython Dependencies

Connect to your wESP32 via serial and install required packages:

```python
import upip
upip.install('micropython-umqtt.simple')
```

The `mdns` module is optional and enables service discovery via `dns-sd -B _http._tcp`.

### 2. Deploy Files to wESP32

Upload the following files to the root of your wESP32 filesystem:

- `main.py` - Main application code
- `config.json` - Configuration file (created automatically if missing)

You can use tools like:
- **Thonny IDE** - Built-in file manager
- **ampy** - `ampy --port /dev/ttyUSB0 put main.py`
- **rshell** - `rshell cp main.py /pyboard/`
- **mpremote** - `mpremote connect /dev/ttyUSB0 cp main.py :`

### 3. Configure Settings

Edit `config.json` or use the web interface at `http://<device-ip>/config`:

```json
{
    "server": "192.168.1.100",
    "topic": "zabbixEvents",
    "username": null,
    "password": null,
    "hostname": null
}
```

| Setting | Description |
|---------|-------------|
| server | MQTT broker IP address |
| topic | MQTT topic to subscribe to |
| username | MQTT username (optional) |
| password | MQTT password (optional) |
| hostname | Device hostname for mDNS (auto-generated if null) |

## API Documentation

### GET /

Returns an HTML help page with API documentation.

### GET /status

Returns current system status as JSON.

**Response:**
```json
{
    "uptime": 3600.5,
    "ip_address": "192.168.1.50",
    "active_problems": {"12345": "High"},
    "buzzer_silenced": false,
    "buzzer_active": true,
    "led_states": {
        "Red": true,
        "Yellow": false,
        "Blue": false,
        "White": false,
        "Green": false,
        "Buzzer": true
    },
    "mqtt_config": {
        "server": "192.168.1.100",
        "topic": "zabbixEvents",
        "hostname": "led-pole-abc123",
        "client_id": "led-pole-abc123"
    }
}
```

### POST /event

Submit a Zabbix event notification.

**Request (JSON):**
```json
{
    "problemId": "12345",
    "problemStatus": "started",
    "problemSeverity": "High"
}
```

| Field | Values |
|-------|--------|
| problemId | Unique problem identifier |
| problemStatus | `started`, `updated`, `acknowledged`, `resolved` |
| problemSeverity | `Not classified`, `Information`, `Warning`, `Average`, `High`, `Disaster` |

**Response:**
```json
{"status": "ok"}
```

### GET /config

Returns HTML configuration form.

### POST /config

Update MQTT and hostname settings via form submission.

## MQTT Integration

Subscribe to a topic and send JSON messages in the same format as the `/event` endpoint:

```json
{
    "problemId": "12345",
    "problemStatus": "started",
    "problemSeverity": "Disaster"
}
```

## Zabbix Integration

### Media Type Configuration

Create a custom media type in Zabbix to send alerts:

1. Go to **Administration > Media types > Create media type**
2. Configure as Webhook or Script
3. Use the following JSON payload:

```json
{
    "problemId": "{EVENT.ID}",
    "problemStatus": "{EVENT.UPDATE.STATUS}",
    "problemSeverity": "{EVENT.SEVERITY}"
}
```

### Action Configuration

Create an action that sends alerts when problems occur:

1. Go to **Configuration > Actions > Create action**
2. Set conditions (e.g., trigger severity >= Warning)
3. Configure operations to send via your LED Pole media type

### Example curl Commands

**Start a problem:**
```bash
curl -X POST http://led-pole.local/event \
  -H "Content-Type: application/json" \
  -d '{"problemId":"1","problemStatus":"started","problemSeverity":"High"}'
```

**Resolve a problem:**
```bash
curl -X POST http://led-pole.local/event \
  -H "Content-Type: application/json" \
  -d '{"problemId":"1","problemStatus":"resolved","problemSeverity":"High"}'
```

**Check status:**
```bash
curl http://led-pole.local/status
```

## Physical Button Operations

- **Single press**: Silence the buzzer (LEDs remain active)
- **Double press**: Reset all active problems and turn on Green LED

## Troubleshooting

### Device not responding
- Check Ethernet connection and cable
- Verify device has obtained an IP address via DHCP
- Check serial console output for errors

### MQTT not connecting
- Verify MQTT broker IP and port (default: 1883)
- Check username/password if authentication is enabled
- Ensure topic name matches your Zabbix configuration

### mDNS not working
- mDNS module may not be installed on your firmware
- Use IP address directly as fallback
- Check with `dns-sd -B _http._tcp` on macOS/Linux

## License

MIT License
