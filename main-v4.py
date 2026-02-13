#!/usr/bin/python3

# Enhanced MJPEG streaming with stepper motor focus control

import io
import logging
import socketserver
import os
import time
from http import server
from threading import Condition, Thread, Lock
import json

import pigpio
from picamera2 import Picamera2
from picamera2.encoders import MJPEGEncoder
from picamera2.outputs import FileOutput

import shinestacker

# GPIO pins
Z_DIR_PIN = 17
Z_Z_STEP_PIN = 27
SLEEP_PIN = 22

GUI_WIDTH, GUI_HEIGHT = 1000, 1000
WEB_GUI_IMAGE_WIDTH, WEB_GUI_IMAGE_HEIGHT = 1500, 1500
SAVED_IMAGE_WIDTH, SAVED_IMAGE_HEIGHT = 1500, 1500

PAGE = """\
<html>
<head>
<title>picamera2 MJPEG streaming with Focus Control</title>
<style>
    body {{
        margin: 0;
        padding: 20px;
        font-family: Arial, sans-serif;
    }}
    .container {{
        display: flex;
        height: calc(100vh - 40px);
        border: 1px solid #ccc;
    }}
    .resizer {{
        width: 5px;
        background: #ccc;
        cursor: col-resize;
        user-select: none;
    }}
    .resizer:hover {{
        background: #999;
    }}
    .left-panel {{
        flex: 0 0 50%;
        overflow: hidden;
        display: flex;
        align-items: center;
        justify-content: center;
        background: #f0f0f0;
    }}
    .left-panel img {{
        max-width: 100%;
        max-height: 100%;
        object-fit: contain;
    }}
    .right-panel {{
        flex: 1;
        padding: 20px;
        overflow-y: auto;
        display: flex;
        flex-direction: column;
        gap: 20px;
    }}
    .subsection {{
        border: 1px solid #ddd;
        padding: 15px;
        border-radius: 5px;
    }}
    .subsection h3 {{
        margin-top: 0;
        margin-bottom: 15px;
        color: #333;
    }}
    .row {{
        display: flex;
        gap: 10px;
        margin-bottom: 10px;
        flex-wrap: wrap;
        align-items: center;
    }}
    input[type="number"] {{
        width: 120px;
        padding: 8px;
        border: 1px solid #ddd;
        border-radius: 4px;
    }}
    input[readonly] {{
        background-color: #f5f5f5;
    }}
    button {{
        padding: 8px 15px;
        border: none;
        border-radius: 4px;
        cursor: pointer;
        font-weight: bold;
    }}
    button.emergency {{
        background-color: #ff4444;
        color: white;
        width: 100%;
        padding: 15px;
        font-size: 18px;
        margin-bottom: 20px;
    }}
    button.emergency:hover {{
        background-color: #ff6666;
    }}
    button.primary {{
        background-color: #4CAF50;
        color: white;
    }}
    button.primary:hover {{
        background-color: #45a049;
    }}
    button.secondary {{
        background-color: #008CBA;
        color: white;
    }}
    button.secondary:hover {{
        background-color: #0077a3;
    }}
    .field-label {{
        font-size: 12px;
        color: #666;
        margin-bottom: 2px;
    }}
    .field-container {{
        display: flex;
        flex-direction: column;
    }}
    #currentPosition {{
        font-weight: bold;
        color: #4CAF50;
    }}
</style>
</head>
<body>
<div class="container">
    <div class="left-panel" id="leftPanel">
        <img src="stream.mjpg" id="streamImage" />
    </div>
    <div class="resizer" id="resizer"></div>
    <div class="right-panel" id="rightPanel">
        <button class="emergency" id="emergencyStop">EMERGENCY STOP</button>
        
        <div class="subsection">
            <h3>Jog Panel</h3>
            <div class="row">
                <div class="field-container">
                    <span class="field-label">Speed (steps/sec)</span>
                    <input type="number" id="speed" value="1000" min="1">
                </div>
                <div class="field-container">
                    <span class="field-label">Acceleration (steps/sec²)</span>
                    <input type="number" id="acceleration" value="1000" min="1">
                </div>
                <div class="field-container">
                    <span class="field-label">Movement amount (steps)</span>
                    <input type="number" id="moveAmount" value="100" step="1">
                </div>
            </div>
            <div class="row">
                <button class="primary" id="movePlus">+</button>
                <button class="primary" id="moveMinus">-</button>
                <div class="field-container">
                    <span class="field-label">Current position (steps)</span>
                    <input type="number" id="currentPosition" value="0" readonly>
                </div>
                <button class="secondary" id="setZero">Set Zero</button>
            </div>
        </div>

        <div class="subsection">
            <h3>Image Burst Panel</h3>
            <div class="row">
                <div class="field-container">
                    <span class="field-label">Start position</span>
                    <input type="number" id="startPos" value="0">
                </div>
                <div class="field-container">
                    <span class="field-label">End position</span>
                    <input type="number" id="endPos" value="1000">
                </div>
                <div class="field-container">
                    <span class="field-label">Number of images</span>
                    <input type="number" id="numImages" value="10" min="2">
                </div>
            </div>
            <div class="row">
                <button class="primary" id="takeBurst">Take Burst</button>
            </div>
        </div>
    </div>
</div>

<script>
    // Splitter functionality
    const resizer = document.getElementById('resizer');
    const leftPanel = document.getElementById('leftPanel');
    const rightPanel = document.getElementById('rightPanel');
    const container = document.querySelector('.container');
    
    let isResizing = false;
    
    resizer.addEventListener('mousedown', (e) => {{
        isResizing = true;
        document.addEventListener('mousemove', handleMouseMove);
        document.addEventListener('mouseup', () => {{
            isResizing = false;
            document.removeEventListener('mousemove', handleMouseMove);
        }});
    }});
    
    function handleMouseMove(e) {{
        if (!isResizing) return;
        
        const containerRect = container.getBoundingClientRect();
        const leftWidth = ((e.clientX - containerRect.left) / containerRect.width) * 100;
        
        if (leftWidth > 20 && leftWidth < 80) {{
            leftPanel.style.flex = `0 0 ${{leftWidth}}%`;
        }}
    }}
    
    // Motor control functions
    function updatePosition() {{
        fetch('/api/position')
            .then(response => response.json())
            .then(data => {{
                document.getElementById('currentPosition').value = data.position;
            }});
    }}
    
    function sendCommand(endpoint, data) {{
        fetch(endpoint, {{
            method: 'POST',
            headers: {{
                'Content-Type': 'application/json',
            }},
            body: JSON.stringify(data)
        }}).then(() => updatePosition());
    }}
    
    // Event listeners for motor controls
    document.getElementById('movePlus').addEventListener('click', () => {{
        sendCommand('/api/move', {{
            direction: 'positive',
            steps: parseInt(document.getElementById('moveAmount').value),
            speed: parseInt(document.getElementById('speed').value),
            acceleration: parseInt(document.getElementById('acceleration').value)
        }});
    }});
    
    document.getElementById('moveMinus').addEventListener('click', () => {{
        sendCommand('/api/move', {{
            direction: 'negative',
            steps: parseInt(document.getElementById('moveAmount').value),
            speed: parseInt(document.getElementById('speed').value),
            acceleration: parseInt(document.getElementById('acceleration').value)
        }});
    }});
    
    document.getElementById('setZero').addEventListener('click', () => {{
        sendCommand('/api/setzero', {{}});
    }});
    
    document.getElementById('emergencyStop').addEventListener('click', () => {{
        sendCommand('/api/emergency', {{}});
    }});
    
    document.getElementById('takeBurst').addEventListener('click', () => {{
        const data = {{
            startPos: parseInt(document.getElementById('startPos').value),
            endPos: parseInt(document.getElementById('endPos').value),
            numImages: parseInt(document.getElementById('numImages').value),
            speed: parseInt(document.getElementById('speed').value),
            acceleration: parseInt(document.getElementById('acceleration').value)
        }};
        sendCommand('/api/burst', data);
    }});
    
    // Update position periodically
    setInterval(updatePosition, 1000);
    updatePosition();
</script>
</body>
</html>
"""


class StepperMotor:
    def __init__(self, pi, dir_pin, step_pin, sleep_pin):
        self.pi = pi
        self.dir_pin = dir_pin
        self.step_pin = step_pin
        self.sleep_pin = sleep_pin
        self.current_position = 0
        self.target_position = 0
        self.is_moving = False
        self.emergency_stop = False
        self.lock = Lock()

        # Setup GPIO
        pi.set_mode(dir_pin, pigpio.OUTPUT)
        pi.set_mode(step_pin, pigpio.OUTPUT)
        pi.set_mode(sleep_pin, pigpio.OUTPUT)

        # Wake up the motor
        pi.write(sleep_pin, 1)

        # Start monitoring thread
        self.running = True
        self.monitor_thread = Thread(target=self._monitor)
        self.monitor_thread.start()

    def set_zero(self):
        with self.lock:
            self.current_position = 0
            self.target_position = 0

    def emergency_stop_now(self):
        with self.lock:
            self.emergency_stop = True
            self.is_moving = False

    def move(self, steps, direction, speed=1000, acceleration=1000):
        """Move relative steps in direction (positive or negative)"""
        if direction == "positive":
            self.move_to(self.target_position + steps, speed, acceleration)
        else:
            self.move_to(self.target_position - steps, speed, acceleration)

    def move_to(self, target, speed=1000, acceleration=1000):
        """Move to absolute target position"""
        with self.lock:
            if self.emergency_stop:
                return
            self.target_position = int(target)
            if not self.is_moving:
                self.is_moving = True
                Thread(target=self._move_thread, args=(speed, acceleration)).start()

    def _move_thread(self, speed, acceleration):
        """Thread to handle the actual movement with acceleration"""
        steps_to_move = abs(self.target_position - self.current_position)
        if steps_to_move == 0:
            self.is_moving = False
            return

        # Set direction
        direction = 1 if self.target_position > self.current_position else 0
        self.pi.write(self.dir_pin, direction)

        # Calculate timing for acceleration/deceleration
        # Simplified acceleration profile - linear acceleration to max speed then deceleration
        accel_steps = min(steps_to_move // 2, int(speed * speed / (2 * acceleration)))

        for step in range(steps_to_move):
            with self.lock:
                if self.emergency_stop:
                    self.is_moving = False
                    return

            # Calculate current speed based on acceleration/deceleration
            if step < accel_steps:
                current_speed = acceleration * step / speed  # Accelerating
            elif step > steps_to_move - accel_steps:
                current_speed = (
                    acceleration * (steps_to_move - step) / speed
                )  # Decelerating
            else:
                current_speed = 1.0  # Full speed

            # Send step pulse
            self.pi.write(self.step_pin, 1)
            time.sleep(0.000001)  # Small pulse width
            self.pi.write(self.step_pin, 0)

            # Wait for next step based on current speed
            try:
                time.sleep(1 / (speed * current_speed))
            except:
                time.sleep(1 / (speed))

        with self.lock:
            self.current_position = self.target_position
            self.is_moving = False

    def _monitor(self):
        """Monitor thread to handle movement state"""
        while self.running:
            time.sleep(0.1)

    def cleanup(self):
        self.running = False
        self.emergency_stop = True
        if self.monitor_thread.is_alive():
            self.monitor_thread.join()
        self.pi.write(self.sleep_pin, 0)  # Put motor to sleep


class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()


class StreamingHandler(server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(301)
            self.send_header("Location", "/index.html")
            self.end_headers()
        elif self.path == "/index.html":
            content = PAGE.format(GUI_WIDTH=GUI_WIDTH, GUI_HEIGHT=GUI_HEIGHT).encode(
                "utf-8"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", len(content))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == "/stream.mjpg":
            self.send_response(200)
            self.send_header("Age", 0)
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header(
                "Content-Type", "multipart/x-mixed-replace; boundary=FRAME"
            )
            self.end_headers()
            try:
                while True:
                    with output.condition:
                        output.condition.wait()
                        frame = output.frame
                    self.wfile.write(b"--FRAME\r\n")
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", len(frame))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
            except Exception as e:
                logging.warning(
                    "Removed streaming client %s: %s", self.client_address, str(e)
                )
        elif self.path == "/api/position":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(f'{{"position": {z_stepper.current_position}}}'.encode())
        else:
            self.send_error(404)
            self.end_headers()

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b"{}"

        data = json.loads(post_data) if post_data else {}

        if self.path == "/api/move":
            direction = data.get("direction", "positive")
            steps = data.get("steps", 100)
            speed = data.get("speed", 1000)
            acceleration = data.get("acceleration", 1000)

            z_stepper.move(steps, direction, speed, acceleration)

            self.send_response(200)
            self.end_headers()

        elif self.path == "/api/setzero":
            z_stepper.set_zero()

            self.send_response(200)
            self.end_headers()

        elif self.path == "/api/emergency":
            z_stepper.emergency_stop_now()

            self.send_response(200)
            self.end_headers()

        elif self.path == "/api/burst":
            start_pos = data.get("startPos", 0)
            end_pos = data.get("endPos", 1000)
            num_images = data.get("numImages", 10)
            speed = data.get("speed", 1000)
            acceleration = data.get("acceleration", 1000)

            # Start burst in a separate thread
            Thread(
                target=take_burst,
                args=(start_pos, end_pos, num_images, speed, acceleration),
            ).start()

            self.send_response(200)
            self.end_headers()

        else:
            self.send_error(404)
            self.end_headers()


class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def take_burst(start_pos, end_pos, num_images, speed, acceleration):
    """Take a burst of images while moving from start to end position"""
    global burst_counter

    # Find next available burst directory
    burst_num = 1
    while os.path.exists(f"burst_{burst_num:03d}"):
        burst_num += 1

    burst_dir = f"burst_{burst_num:03d}"
    os.makedirs(burst_dir, exist_ok=True)

    # Move to start position
    z_stepper.move_to(start_pos, speed, acceleration)
    while z_stepper.is_moving:
        time.sleep(0.1)
        if z_stepper.emergency_stop:
            return

    # Calculate positions for each image
    positions = []
    if num_images > 1:
        step_size = (end_pos - start_pos) / (num_images - 1)
        for i in range(num_images):
            positions.append(int(start_pos + i * step_size))

    # Take images while moving
    for pos in positions:
        if z_stepper.emergency_stop:
            return

        # Move to position
        z_stepper.move_to(pos, speed, acceleration)

        # Wait for movement to complete
        while z_stepper.is_moving:
            time.sleep(0.05)
            if z_stepper.emergency_stop:
                return

        # Capture image
        time.sleep(0.1)  # Small delay for motor to settle
        request = picam2.capture_request()
        request.save("main", f"{burst_dir}/burst_{pos:04d}.jpeg")
        request.release()

    logging.info(f"Burst {burst_num} completed with {num_images} images")

    logging.info(f"Starting focus stacking")
    try:
        job = shinestacker.StackJob(burst_dir, input_path=burst_dir)
        job.add_action(
            shinestacker.CombinedActions(
                "align", actions=[shinestacker.AlignFrames(), shinestacker.BalanceFrames()]
            )
        )
        # job.add_action(
        #     shinestacker.FocusStackBunch(
        #         "batches", shinestacker.PyramidStack(), frames=12, overlap=2
        #     )
        # )
        job.add_action(
            shinestacker.FocusStack("stack", shinestacker.PyramidStack(), prefix="pyram_")
        )
        # job.add_action(
        #     shinestacker.FocusStack("stack", shinestacker.DepthMapStack(), prefix="dmap_")
        # )
        job.run()
    except Exception as e: 
        logging.info(f"Failed to  focus stacking: {e}")


# Initialize pigpio
pi = pigpio.pi()
if not pi.connected:
    raise RuntimeError("Could not connect to pigpio daemon")

# Initialize stepper motor
z_stepper = StepperMotor(pi, Z_DIR_PIN, Z_Z_STEP_PIN, SLEEP_PIN)

# Initialize camera
picam2 = Picamera2()
picam2.configure(
    picam2.create_video_configuration(
        main={"size": (WEB_GUI_IMAGE_WIDTH, WEB_GUI_IMAGE_HEIGHT)}
    )
)
output = StreamingOutput()
picam2.start_recording(MJPEGEncoder(), FileOutput(output))

burst_counter = 0

try:
    address = ("", 8000)
    server = StreamingServer(address, StreamingHandler)
    logging.info("Server started at http://0.0.0.0:8000")
    server.serve_forever()
finally:
    picam2.stop_recording()
    z_stepper.cleanup()
    pi.stop()
