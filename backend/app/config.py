import os

class Settings:
    PROJECT_NAME: str = "Smart Traffic & Emergency Routing API"
  # Configure database URL via environment variable or fallback to local default
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/traffic_db")
    MQTT_BROKER: str = os.getenv("MQTT_BROKER", "localhost")
    MQTT_PORT: int = 1883
    MQTT_TOPIC: str = "traffic/intersection_1"

settings = Settings()