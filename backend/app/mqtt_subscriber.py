import json
import paho.mqtt.client as mqtt
from backend.app.config import settings

def on_connect(client, userdata, flags, rc, properties=None):
    print(f"[MQTT] Connected with result code {rc}")
    client.subscribe(settings.MQTT_TOPIC)

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        print(f"[MQTT] Received message on {msg.topic}: {payload}")
    except Exception as e:
        print(f"[MQTT Error] Failed to parse message: {e}")

def start_subscriber():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(settings.MQTT_BROKER, settings.MQTT_PORT, 60)
        client.loop_forever()
    except Exception as e:
        print(f"[MQTT Error] Could not connect to broker: {e}")

if __name__ == "__main__":
    start_subscriber()
