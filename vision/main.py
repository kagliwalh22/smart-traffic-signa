
import os
import time
import cv2
import threading
import numpy as np
import base64
from flask import Flask, jsonify, request
from flask_cors import CORS
from ultralytics import YOLO

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 1. Load Local Models
# Standard COCO YOLO for traffic density counting
coco_model = YOLO(os.path.join(BASE_DIR, 'yolov8n.pt'))
COCO_VEHICLE_CLASSES = [2, 3, 5, 7]  # Car (2), Motorcycle (3), Bus (5), Truck (7)

# Custom trained model for Ambulance/Emergency detection
ambulance_model = YOLO(os.path.join(BASE_DIR, 'best.pt'))

# Confidence Thresholds
EMERGENCY_CONF_THRESH = 0.60  # Robust detection of emergency vehicles (avoids false truck alerts)
COCO_CONF_THRESH = 0.25

# Detection filtering parameters
MIN_AMBULANCE_AREA = 10000  # Minimum area for ambulance detection (filters small distant vehicles)
MAX_AMBULANCE_AREA = 80000  # Maximum area for ambulance detection
AMBULANCE_ASPECT_RATIO_MIN = 0.6  # Support frontal, diagonal, and side camera angles
AMBULANCE_ASPECT_RATIO_MAX = 3.5

# Video Sources
LANE_VIDEOS = {
    "Lane 1": os.path.join(BASE_DIR, "lane1.mp4"),
    "Lane 2": os.path.join(BASE_DIR, "lane2.mp4"),
    "Lane 3": os.path.join(BASE_DIR, "lane3.mp4"),
    "Lane 4": os.path.join(BASE_DIR, "lane4.mp4"),
}

LANES_ORDER = ["Lane 1", "Lane 2", "Lane 3", "Lane 4"]

system_state = {
    "active_green_lane": "Lane 1",
    "emergency_detected": False,
    "emergency_lane": None,
    "current_timer": 15,
    "is_manual": False,
    "lane_data": {
        "Lane 1": {"emergency_count": 0, "normal_count": 0},
        "Lane 2": {"emergency_count": 0, "normal_count": 0},
        "Lane 3": {"emergency_count": 0, "normal_count": 0},
        "Lane 4": {"emergency_count": 0, "normal_count": 0},
    }
}

# Store latest processed frames for visualization
latest_frames = {
    "Lane 1": None,
    "Lane 2": None, 
    "Lane 3": None,
    "Lane 4": None
}

# Track emergency detection history for temporal consistency
emergency_detection_history = {
    "Lane 1": 0,
    "Lane 2": 0,
    "Lane 3": 0,
    "Lane 4": 0
}
EMERGENCY_DETECTION_THRESHOLD = 2  # Require 2 consecutive detections

emergency_queue = []
manual_override_lane = None

def calculate_iou(box1, box2):
    """Calculate Intersection over Union (IoU) to avoid double counting ambulances as normal cars."""
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])

    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
    area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
    union = area1 + area2 - intersection
    return intersection / union if union > 0 else 0

def is_valid_ambulance(box, conf):
    """Filter false positive ambulance detections based on size and shape."""
    x1, y1, x2, y2 = box
    width = x2 - x1
    height = y2 - y1
    area = width * height
    aspect_ratio = width / height if height > 0 else 0
    
    # Check confidence threshold
    if conf < EMERGENCY_CONF_THRESH:
        return False
    
    # Check area constraints
    if area < MIN_AMBULANCE_AREA or area > MAX_AMBULANCE_AREA:
        return False
    
    # Check aspect ratio (allows oncoming, diagonal, and side-view angles)
    if aspect_ratio < AMBULANCE_ASPECT_RATIO_MIN or aspect_ratio > AMBULANCE_ASPECT_RATIO_MAX:
        return False
    
    return True

def calculate_green_time(vehicle_count):
    if vehicle_count < 10:
        return 15
    elif 10 <= vehicle_count <= 20:
        return 30
    else:
        return 45

def get_next_sequential_lane(current_lane):
    idx = LANES_ORDER.index(current_lane)
    return LANES_ORDER[(idx + 1) % len(LANES_ORDER)]

def traffic_controller():
    global emergency_queue, manual_override_lane

    while True:
        if system_state["is_manual"] and manual_override_lane:
            system_state["active_green_lane"] = manual_override_lane
            system_state["current_timer"] = 30
            time.sleep(1)
            continue

        active_lane = system_state["active_green_lane"]
        vehicle_count = system_state["lane_data"][active_lane]["normal_count"]
        allocated_time = calculate_green_time(vehicle_count)

        for remaining in range(allocated_time, 0, -1):
            if system_state["is_manual"] or (len(emergency_queue) > 0 and emergency_queue[0] != active_lane):
                break
            system_state["current_timer"] = remaining
            time.sleep(1)

        if system_state["is_manual"]:
            continue

        if len(emergency_queue) > 0:
            next_lane = emergency_queue.pop(0)
            system_state["emergency_detected"] = True
            system_state["emergency_lane"] = next_lane
            system_state["active_green_lane"] = next_lane
            print(f"🚨 Switching to emergency lane: {next_lane}")
        else:
            # Always reset emergency flags when queue is empty
            system_state["emergency_detected"] = False
            system_state["emergency_lane"] = None
            next_lane = get_next_sequential_lane(active_lane)
            system_state["active_green_lane"] = next_lane
            print(f"🚗 Switching to next lane: {next_lane} (timer: {allocated_time}s based on {vehicle_count} vehicles)")

def process_video_feeds():
    global emergency_queue, emergency_detection_history

    caps = {}
    for lane, path in LANE_VIDEOS.items():
        if os.path.exists(path):
            caps[lane] = cv2.VideoCapture(path)
            print(f"✅ Loaded video for {lane}: {path}")
        else:
            print(f"⚠️ Video file not found for {lane}: {path}")

    if not caps:
        print("❌ Error: No video files found in vision directory.")
        return

    print(f"🚀 Starting video processing for {len(caps)} lanes...")
    
    # Check if any video files failed to load
    expected_lanes = len(LANE_VIDEOS)
    loaded_lanes = len(caps)
    if loaded_lanes < expected_lanes:
        print(f"⚠️ Warning: Only {loaded_lanes}/{expected_lanes} video files loaded")
    
    frame_count = 0
    debug_counter = 0

    while True:
        for lane, cap in caps.items():
            ret, frame = cap.read()
            if not ret:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = cap.read()
                if not ret:
                    print(f"⚠️ Failed to read frame for {lane}")
                    continue

            # 1. Run Local Ambulance Detection Model (best.pt)
            amb_results = ambulance_model(frame, conf=0.45, verbose=False)[0]  # Lower conf threshold, filter later
            emergency_boxes = []

            if amb_results.boxes is not None:
                for box in amb_results.boxes:
                    coords = [int(c) for c in box.xyxy[0].tolist()]
                    conf = float(box.conf[0])
                    
                    # Apply advanced filtering
                    if is_valid_ambulance(coords, conf):
                        emergency_boxes.append((coords, conf))

            emergency_count = len(emergency_boxes)

            # 2. Run Local COCO Model for General Vehicles (yolov8n.pt)
            coco_results = coco_model(frame, conf=COCO_CONF_THRESH, classes=COCO_VEHICLE_CLASSES, verbose=False)[0]
            normal_count = 0
            coco_boxes = []

            if coco_results.boxes is not None:
                for coco_box in coco_results.boxes:
                    coords = [int(c) for c in coco_box.xyxy[0].tolist()]
                    cls_id = int(coco_box.cls[0])
                    conf = float(coco_box.conf[0])

                    # Ignore vehicle box if it overlaps with a detected ambulance
                    is_overlap = any(calculate_iou(coords, em[0]) > 0.30 for em in emergency_boxes)  # Increased IoU threshold

                    if not is_overlap:
                        coco_boxes.append((coords, cls_id, conf))
                        normal_count += 1

            # Update Telemetry State with temporal consistency
            if emergency_count > 0:
                emergency_detection_history[lane] += 1
                actual_emergency = emergency_detection_history[lane] >= EMERGENCY_DETECTION_THRESHOLD
            else:
                emergency_detection_history[lane] = max(0, emergency_detection_history[lane] - 1)
                actual_emergency = emergency_detection_history[lane] >= EMERGENCY_DETECTION_THRESHOLD

            system_state["lane_data"][lane] = {
                "emergency_count": 1 if actual_emergency else 0,
                "normal_count": normal_count
            }

            # Debug output every 50 frames per lane
            debug_counter += 1
            if debug_counter % 50 == 0:
                print(f"📊 {lane}: {normal_count} vehicles, {emergency_count} raw detections, {actual_emergency} confirmed")

            if actual_emergency:
                if lane not in emergency_queue and system_state["active_green_lane"] != lane:
                    emergency_queue.append(lane)
                    print(f"🚨 Emergency vehicle confirmed in {lane}, added to queue")
                elif system_state["active_green_lane"] == lane:
                    system_state["emergency_detected"] = True
                    system_state["emergency_lane"] = lane
            else:
                # Remove from queue if emergency vehicle is no longer detected
                if lane in emergency_queue:
                    emergency_queue.remove(lane)
                    print(f"✅ Emergency vehicle cleared from {lane}, removed from queue")

            # 3. Visualization Rendering
            display_frame = frame.copy()

            # Normal Traffic (White Bounding Boxes)
            for coords, cls_id, conf in coco_boxes:
                x1, y1, x2, y2 = coords
                label_text = f"{coco_model.names.get(cls_id, 'vehicle')} {conf:.2f}"
                cv2.rectangle(display_frame, (x1, y1), (x2, y2), (220, 220, 220), 1)
                cv2.putText(display_frame, label_text, (x1, max(15, y1 - 4)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (220, 220, 220), 1)

            # Ambulance Emergency Vehicles (Red Bounding Boxes)
            for coords, conf in emergency_boxes:
                x1, y1, x2, y2 = coords
                cv2.rectangle(display_frame, (x1, y1), (x2, y2), (0, 0, 255), 3)
                cv2.putText(display_frame, f"AMBULANCE 🚨 {conf:.2f}", (x1, max(20, y1 - 10)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

            # Store latest frame for API access
            latest_frames[lane] = display_frame

            # Optional display - only if DISPLAY environment variable is set
            if os.environ.get('DISPLAY'):
                resized_frame = cv2.resize(display_frame, (480, 270))
                cv2.imshow(f"Live Traffic Feed - {lane}", resized_frame)

        # Small delay to prevent CPU overload
        time.sleep(0.01)
        
        if os.environ.get('DISPLAY') and cv2.waitKey(1) & 0xFF == ord('q'):
            break

    for cap in caps.values():
        cap.release()
    cv2.destroyAllWindows()

# --- API Endpoints ---

@app.route('/api/traffic', methods=['GET'])
def get_traffic_data():
    return jsonify(system_state)

@app.route('/api/frames', methods=['GET'])
def get_detection_frames():
    """Return the latest detection frames for all lanes as base64 encoded images"""
    frames_data = {}
    
    for lane, frame in latest_frames.items():
        if frame is not None:
            # Resize frame for faster transmission
            resized = cv2.resize(frame, (640, 360))
            # Encode to JPEG
            _, buffer = cv2.imencode('.jpg', resized, [cv2.IMWRITE_JPEG_QUALITY, 80])
            # Convert to base64
            frame_base64 = base64.b64encode(buffer).decode('utf-8')
            frames_data[lane] = frame_base64
        else:
            frames_data[lane] = None
    
    return jsonify(frames_data)

@app.route('/api/manual', methods=['POST', 'OPTIONS'])
def manual_control():
    global manual_override_lane

    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200

    data = request.get_json(silent=True) or {}
    raw_lane = str(data.get("lane") or data.get("lane_id") or data.get("lane_number") or "").strip()
    action = data.get("action", "enable")

    if raw_lane.isdigit():
        lane = f"Lane {raw_lane}"
    elif raw_lane.lower().startswith("lane"):
        lane = f"Lane {raw_lane.split()[-1]}"
    else:
        lane = raw_lane

    if action == "disable":
        system_state["is_manual"] = False
        manual_override_lane = None
        return jsonify({"status": "success", "message": "Manual override disabled."}), 200

    if lane in LANES_ORDER:
        system_state["is_manual"] = True
        manual_override_lane = lane
        system_state["active_green_lane"] = lane
        return jsonify({"status": "success", "message": f"Manual override set to {lane}"}), 200
    else:
        return jsonify({"status": "error", "message": f"Invalid lane: {lane}"}), 400

if __name__ == '__main__':
    print("🚀 Starting Smart Traffic System...")
    
    # Test model loading
    try:
        print("🔍 Testing COCO model...")
        test_result = coco_model.predict(np.zeros((640, 640, 3), dtype=np.uint8), verbose=False)
        print("✅ COCO model loaded successfully")
    except Exception as e:
        print(f"❌ Error loading COCO model: {e}")
        
    try:
        print("🔍 Testing Ambulance model...")
        test_result = ambulance_model.predict(np.zeros((640, 640, 3), dtype=np.uint8), verbose=False)
        print("✅ Ambulance model loaded successfully")
    except Exception as e:
        print(f"❌ Error loading Ambulance model: {e}")
    
    video_thread = threading.Thread(target=process_video_feeds, daemon=True)
    video_thread.start()
    print("✅ Video processing thread started")

    controller_thread = threading.Thread(target=traffic_controller, daemon=True)
    controller_thread.start()
    print("✅ Traffic controller thread started")

    app.run(host='0.0.0.0', port=5000)