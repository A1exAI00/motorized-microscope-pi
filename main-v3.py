#!/usr/bin/python3

# This is the same as mjpeg_server.py, but uses the h/w MJPEG encoder.
# Enhanced with stepper motor focus control

import io
import logging
import socketserver
import json
import os
import time
import math
import threading
from http import server
from threading import Condition
from pathlib import Path

import pigpio
from picamera2 import Picamera2
from picamera2.encoders import MJPEGEncoder
from picamera2.outputs import FileOutput

# GPIO Pins
DIR_PIN = 17
STEP_PIN = 27
SLEEP_PIN = 22

# Motor parameters
MICROSTEPPING = 32
STEPS_PER_REV = 200 * MICROSTEPPING  # 200 steps per revolution * microstepping

GUI_WIDTH, GUI_HEIGHT = 1000, 1000
IMAGE_WIDTH, IMAGE_HEIGHT = 1500, 1500

# HTML template with CSS and JavaScript for the GUI
PAGE = """\
<!DOCTYPE html>
<html>
<head>
<title>picamera2 MJPEG streaming with Focus Control</title>
<style>
    body, html {
        margin: 0;
        padding: 0;
        height: 100%;
        overflow: hidden;
        font-family: Arial, sans-serif;
    }
    .container {
        display: flex;
        height: 100vh;
        width: 100vw;
    }
    .camera-section {
        flex: 0 0 auto;
        background: #000;
        display: flex;
        justify-content: center;
        align-items: center;
        overflow: hidden;
    }
    .camera-section img {
        max-width: 100%;
        max-height: 100vh;
        object-fit: contain;
    }
    .controls-section {
        flex: 0 0 auto;
        background: #f0f0f0;
        padding: 20px;
        overflow-y: auto;
        box-sizing: border-box;
        border-left: 3px solid #ccc;
    }
    .divider {
        width: 5px;
        background: #999;
        cursor: col-resize;
        user-select: none;
    }
    .divider:hover {
        background: #666;
    }
    .control-group {
        background: white;
        border-radius: 8px;
        padding: 15px;
        margin-bottom: 20px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    .control-group h3 {
        margin-top: 0;
        margin-bottom: 15px;
        color: #333;
    }
    .input-row {
        display: flex;
        align-items: center;
        margin-bottom: 10px;
        flex-wrap: wrap;
        gap: 10px;
    }
    .input-row label {
        min-width: 120px;
        font-weight: bold;
    }
    .input-row input {
        padding: 8px;
        border: 1px solid #ccc;
        border-radius: 4px;
        flex: 1;
    }
    .input-row input[readonly] {
        background: #f5f5f5;
    }
    .button-row {
        display: flex;
        gap: 10px;
        margin: 15px 0;
        flex-wrap: wrap;
    }
    button {
        padding: 10px 20px;
        border: none;
        border-radius: 4px;
        cursor: pointer;
        font-size: 14px;
        font-weight: bold;
        transition: background-color 0.2s;
    }
    button.primary {
        background: #007bff;
        color: white;
    }
    button.primary:hover {
        background: #0056b3;
    }
    button.secondary {
        background: #6c757d;
        color: white;
    }
    button.secondary:hover {
        background: #545b62;
    }
    button.success {
        background: #28a745;
        color: white;
    }
    button.success:hover {
        background: #218838;
    }
    button.warning {
        background: #ffc107;
        color: black;
    }
    button.warning:hover {
        background: #e0a800;
    }
    button.danger {
        background: #dc3545;
        color: white;
        width: 100%;
        padding: 15px;
        font-size: 16px;
        margin-bottom: 20px;
    }
    button.danger:hover {
        background: #c82333;
    }
    .status {
        margin-top: 10px;
        padding: 10px;
        border-radius: 4px;
        font-weight: bold;
    }
    .status.moving {
        background: #fff3cd;
        color: #856404;
    }
    .status.idle {
        background: #d4edda;
        color: #155724;
    }
    .progress {
        width: 100%;
        height: 20px;
        background: #e9ecef;
        border-radius: 4px;
        margin-top: 10px;
        overflow: hidden;
    }
    .progress-bar {
        height: 100%;
        background: #28a745;
        transition: width 0.3s;
        color: white;
        text-align: center;
        line-height: 20px;
        font-size: 12px;
    }
</style>
</head>
<body>
<div class="container">
    <div class="camera-section" id="cameraSection">
        <img src="stream.mjpg" id="cameraFeed" />
    </div>
    <div class="divider" id="divider" onmousedown="initDrag(event)"></div>
    <div class="controls-section" id="controlsSection">
        <button class="danger" onclick="emergencyStop()">EMERGENCY STOP</button>
        
        <div class="control-group">
            <h3>Jog Panel</h3>
            <div class="input-row">
                <label>Speed (steps/s):</label>
                <input type="number" id="speed" value="1000" min="1">
            </div>
            <div class="input-row">
                <label>Acceleration (steps/s²):</label>
                <input type="number" id="acceleration" value="1000" min="1">
            </div>
            <div class="input-row">
                <label>Movement amount (steps):</label>
                <input type="number" id="movementAmount" value="100">
            </div>
            <div class="button-row">
                <button class="primary" onclick="moveMotor(-1)">-</button>
                <button class="primary" onclick="moveMotor(1)">+</button>
            </div>
            <div class="input-row">
                <label>Current position:</label>
                <input type="text" id="currentPosition" value="0" readonly>
            </div>
            <button class="secondary" onclick="setZero()">Set Zero</button>
        </div>

        <div class="control-group">
            <h3>Image Burst Panel</h3>
            <div class="input-row">
                <label>Start position:</label>
                <input type="number" id="startPos" value="0">
            </div>
            <div class="input-row">
                <label>End position:</label>
                <input type="number" id="endPos" value="1000">
            </div>
            <div class="input-row">
                <label>Number of images:</label>
                <input type="number" id="numImages" value="10" min="2">
            </div>
            <button class="success" onclick="takeBurst()">Take Burst</button>
            <div class="status idle" id="motorStatus">Idle</div>
            <div class="progress" id="progressContainer" style="display: none;">
                <div class="progress-bar" id="progressBar" style="width: 0%">0%</div>
            </div>
        </div>
    </div>
</div>

<script>
    let currentPosition = 0;
    let isMoving = false;
    let dragActive = false;

    // Divider dragging functionality
    function initDrag(e) {
        dragActive = true;
        e.preventDefault();
    }

    document.addEventListener('mousemove', function(e) {
        if (!dragActive) return;
        
        const container = document.querySelector('.container');
        const containerRect = container.getBoundingClientRect();
        const cameraSection = document.getElementById('cameraSection');
        const controlsSection = document.getElementById('controlsSection');
        
        let newWidth = e.clientX - containerRect.left;
        
        // Minimum widths
        if (newWidth < 300) newWidth = 300;
        if (containerRect.width - newWidth < 400) newWidth = containerRect.width - 400;
        
        cameraSection.style.width = newWidth + 'px';
        controlsSection.style.width = (containerRect.width - newWidth - 5) + 'px';
    });

    document.addEventListener('mouseup', function() {
        dragActive = false;
    });

    // Motor control functions
    function updateMotorStatus() {
        fetch('/motor_status')
            .then(response => response.json())
            .then(data => {
                currentPosition = data.position;
                document.getElementById('currentPosition').value = data.position;
                
                const statusEl = document.getElementById('motorStatus');
                const progressContainer = document.getElementById('progressContainer');
                
                if (data.moving) {
                    statusEl.className = 'status moving';
                    statusEl.innerText = 'Moving...';
                    if (data.burst_progress > 0) {
                        progressContainer.style.display = 'block';
                        const progressBar = document.getElementById('progressBar');
                        progressBar.style.width = data.burst_progress + '%';
                        progressBar.innerText = data.burst_progress + '%';
                    }
                } else {
                    statusEl.className = 'status idle';
                    statusEl.innerText = 'Idle';
                    progressContainer.style.display = 'none';
                }
                
                isMoving = data.moving;
            });
    }

    function moveMotor(direction) {
        if (isMoving) {
            alert('Motor is currently moving. Please wait or press emergency stop.');
            return;
        }
        
        const amount = parseInt(document.getElementById('movementAmount').value) * direction;
        const speed = parseInt(document.getElementById('speed').value);
        const acceleration = parseInt(document.getElementById('acceleration').value);
        
        fetch('/move_motor', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                steps: amount,
                speed: speed,
                acceleration: acceleration
            })
        });
    }

    function setZero() {
        if (isMoving) {
            alert('Cannot set zero while motor is moving.');
            return;
        }
        fetch('/set_zero', {method: 'POST'});
    }

    function emergencyStop() {
        fetch('/emergency_stop', {method: 'POST'});
    }

    function takeBurst() {
        if (isMoving) {
            alert('Motor is currently moving. Please wait or press emergency stop.');
            return;
        }
        
        const startPos = parseInt(document.getElementById('startPos').value);
        const endPos = parseInt(document.getElementById('endPos').value);
        const numImages = parseInt(document.getElementById('numImages').value);
        const speed = parseInt(document.getElementById('speed').value);
        const acceleration = parseInt(document.getElementById('acceleration').value);
        
        fetch('/take_burst', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                start_pos: startPos,
                end_pos: endPos,
                num_images: numImages,
                speed: speed,
                acceleration: acceleration
            })
        });
    }

    // Update motor status every 200ms
    setInterval(updateMotorStatus, 200);
</script>
</body>
</html>
"""


class MotorController:
    def __init__(self):
        self.pi = pigpio.pi()
        if not self.pi.connected:
            raise RuntimeError("Could not connect to pigpio daemon")
        
        # Setup pins
        self.pi.set_mode(DIR_PIN, pigpio.OUTPUT)
        self.pi.set_mode(STEP_PIN, pigpio.OUTPUT)
        self.pi.set_mode(SLEEP_PIN, pigpio.OUTPUT)
        
        # Initial state
        self.pi.write(SLEEP_PIN, 0)  # Sleep mode on
        self.pi.write(DIR_PIN, 0)
        self.pi.write(STEP_PIN, 0)
        
        self.position = 0
        self.target_position = 0
        self.moving = False
        self.emergency_stop_flag = False
        self.burst_progress = 0
        self.move_thread = None
        self.lock = threading.Lock()
        
    def wake_up(self):
        """Wake up the motor driver"""
        self.pi.write(SLEEP_PIN, 1)
        time.sleep(0.001)  # Small delay to wake up
        
    def sleep(self):
        """Put motor driver to sleep"""
        self.pi.write(SLEEP_PIN, 0)
        
    def set_direction(self, direction):
        """Set motor direction: 1 for positive, 0 for negative"""
        self.pi.write(DIR_PIN, 1 if direction > 0 else 0)
        
    def step(self):
        """Generate one step pulse"""
        self.pi.write(STEP_PIN, 1)
        time.sleep(0.000001)  # 1 microsecond pulse
        self.pi.write(STEP_PIN, 0)
        
    def move_steps(self, steps, speed_hz, acceleration, callback=None):
        """Move motor by specified number of steps with acceleration"""
        with self.lock:
            if self.emergency_stop_flag:
                self.emergency_stop_flag = False
                return
            self.moving = True
            self.wake_up()
        
        direction = 1 if steps > 0 else -1
        abs_steps = abs(steps)
        self.set_direction(direction)
        
        # Simple acceleration/deceleration profile
        steps_to_accel = min(abs_steps // 2, int(speed_hz * speed_hz / (2 * acceleration)))
        if steps_to_accel < 1:
            steps_to_accel = 0
            
        for i in range(abs_steps):
            with self.lock:
                if self.emergency_stop_flag:
                    self.emergency_stop_flag = False
                    break
                    
            # Calculate current speed based on acceleration profile
            if i < steps_to_accel:
                current_speed = acceleration * i / speed_hz
            elif i > abs_steps - steps_to_accel:
                current_speed = acceleration * (abs_steps - i) / speed_hz
            else:
                current_speed = speed_hz
                
            # Ensure minimum speed
            current_speed = max(1, current_speed)
            
            self.step()
            
            with self.lock:
                self.position += direction
                
            # Delay for the next step
            time.sleep(1.0 / current_speed)
            
            if callback and i % 100 == 0:
                callback(i / abs_steps * 100)
        
        with self.lock:
            self.moving = False
            self.sleep()
            
    def move_to_position(self, target, speed_hz, acceleration, callback=None):
        """Move to absolute position"""
        with self.lock:
            steps_to_move = target - self.position
            
        if steps_to_move != 0:
            self.move_steps(steps_to_move, speed_hz, acceleration, callback)
            
    def emergency_stop(self):
        """Emergency stop - stop all movement"""
        with self.lock:
            self.emergency_stop_flag = True
            self.moving = False
            self.sleep()
            
    def set_zero(self):
        """Set current position to zero"""
        with self.lock:
            self.position = 0
            self.target_position = 0
            
    def get_status(self):
        """Get motor status"""
        with self.lock:
            return {
                'position': self.position,
                'moving': self.moving,
                'burst_progress': self.burst_progress
            }
            
    def cleanup(self):
        """Cleanup GPIO"""
        self.emergency_stop()
        self.pi.write(SLEEP_PIN, 0)
        self.pi.stop()


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
        if self.path == '/':
            self.send_response(301)
            self.send_header('Location', '/index.html')
            self.end_headers()
        elif self.path == '/index.html':
            content = PAGE.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', len(content))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == '/stream.mjpg':
            self.send_response(200)
            self.send_header('Age', 0)
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.end_headers()
            try:
                while True:
                    with output.condition:
                        output.condition.wait()
                        frame = output.frame
                    self.wfile.write(b'--FRAME\r\n')
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', len(frame))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b'\r\n')
            except Exception as e:
                logging.warning(
                    'Removed streaming client %s: %s',
                    self.client_address, str(e))
        elif self.path == '/motor_status':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(motor.get_status()).encode())
        else:
            self.send_error(404)
            self.end_headers()
            
    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        
        if self.path == '/move_motor':
            data = json.loads(post_data)
            steps = data['steps']
            speed = data['speed']
            acceleration = data['acceleration']
            
            def move_done():
                pass
                
            thread = threading.Thread(target=motor.move_steps, 
                                     args=(steps, speed, acceleration, move_done))
            thread.daemon = True
            thread.start()
            
            self.send_response(200)
            self.end_headers()
            
        elif self.path == '/set_zero':
            motor.set_zero()
            self.send_response(200)
            self.end_headers()
            
        elif self.path == '/emergency_stop':
            motor.emergency_stop()
            self.send_response(200)
            self.end_headers()
            
        elif self.path == '/take_burst':
            data = json.loads(post_data)
            start_pos = data['start_pos']
            end_pos = data['end_pos']
            num_images = data['num_images']
            speed = data['speed']
            acceleration = data['acceleration']
            
            def burst_thread():
                # Create burst directory
                burst_num = 1
                while os.path.exists(f"burst_{burst_num:03d}"):
                    burst_num += 1
                
                burst_dir = f"burst_{burst_num:03d}"
                os.makedirs(burst_dir)
                
                # Move to start position
                motor.move_to_position(start_pos, speed, acceleration)
                
                # Calculate positions for each image
                positions = []
                for i in range(num_images):
                    pos = start_pos + (end_pos - start_pos) * i / (num_images - 1)
                    positions.append(int(round(pos)))
                
                # Take images while moving
                for i, pos in enumerate(positions):
                    with motor.lock:
                        if motor.emergency_stop_flag:
                            motor.emergency_stop_flag = False
                            break
                        motor.burst_progress = int((i + 1) / num_images * 100)
                    
                    # Move to position
                    motor.move_to_position(pos, speed, acceleration)
                    
                    # Capture image
                    timestamp = time.strftime("%Y%m%d_%H%M%S")
                    filename = f"{burst_dir}/burst_{pos:04d}.jpeg"
                    picam2.capture_file(filename)
                    
                    time.sleep(0.1)  # Small delay between captures
                
                with motor.lock:
                    motor.burst_progress = 0
            
            thread = threading.Thread(target=burst_thread)
            thread.daemon = True
            thread.start()
            
            self.send_response(200)
            self.end_headers()
        else:
            self.send_error(404)
            self.end_headers()


class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


# Initialize motor controller
motor = MotorController()

# Initialize camera
picam2 = Picamera2()
picam2.configure(picam2.create_video_configuration(main={"size": (IMAGE_WIDTH, IMAGE_HEIGHT)}))
output = StreamingOutput()
picam2.start_recording(MJPEGEncoder(), FileOutput(output))

try:
    address = ('', 8000)
    server = StreamingServer(address, StreamingHandler)
    print(f"Server started at http://localhost:8000")
    print(f"Motor controller initialized with microstepping 1/{MICROSTEPPING}")
    server.serve_forever()
finally:
    motor.cleanup()
    picam2.stop_recording()