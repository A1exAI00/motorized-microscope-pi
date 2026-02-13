#!/usr/bin/python3

import io
import logging
import socketserver
import threading
import time
import os
import re
from http import server
from threading import Condition
from dataclasses import dataclass
from enum import Enum
import RPi.GPIO as GPIO

from picamera2 import Picamera2
from picamera2.encoders import MJPEGEncoder
from picamera2.outputs import FileOutput

# GPIO Pins for stepper motor
DIR_PIN = 17
STEP_PIN = 27
SLEEP_PIN = 22

# Motor settings
MICROSTEPS = 32
STEPS_PER_REV = 200 * MICROSTEPS  # 200 steps per revolution * microsteps

# GUI settings
GUI_WIDTH, GUI_HEIGHT = 1000, 1000
IMAGE_WIDTH, IMAGE_HEIGHT = 1500, 1500

# Motor state
class MotorState(Enum):
    IDLE = "idle"
    MOVING = "moving"
    EMERGENCY_STOP = "emergency_stop"
    BURST = "burst"

@dataclass
class MotorStatus:
    position: int = 0
    target_position: int = 0
    state: MotorState = MotorState.IDLE
    speed: int = 1000
    movement_amount: int = 100

# Global motor status
motor_status = MotorStatus()
motor_lock = threading.Lock()
burst_counter = 1
burst_stop_requested = False

# HTML Template with split view and controls
PAGE = """\
<!DOCTYPE html>
<html>
<head>
    <title>Picamera2 MJPEG Streaming with Focus Control</title>
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
        }
        .camera-section {
            flex: 0 0 auto;
            height: 100vh;
            overflow: hidden;
            background: #000;
            display: flex;
            align-items: center;
            justify-content: center;
            border-right: 3px solid #ccc;
        }
        .camera-section img {
            max-width: 100%;
            max-height: 100vh;
            object-fit: contain;
        }
        .controls-section {
            flex: 1;
            height: 100vh;
            overflow-y: auto;
            background: #f5f5f5;
            padding: 20px;
            box-sizing: border-box;
        }
        .divider {
            width: 5px;
            background: #ccc;
            cursor: col-resize;
            position: relative;
            z-index: 10;
        }
        .divider:hover {
            background: #999;
        }
        .control-panel {
            background: white;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .control-panel h2 {
            margin-top: 0;
            color: #333;
            border-bottom: 2px solid #eee;
            padding-bottom: 10px;
        }
        .control-row {
            display: flex;
            gap: 10px;
            margin-bottom: 15px;
            align-items: center;
            flex-wrap: wrap;
        }
        .control-row label {
            min-width: 120px;
            color: #666;
        }
        .control-row input {
            padding: 8px;
            border: 1px solid #ddd;
            border-radius: 4px;
            flex: 1;
            min-width: 150px;
        }
        .control-row input[readonly] {
            background: #f9f9f9;
            color: #333;
        }
        .control-row button {
            padding: 8px 16px;
            background: #4CAF50;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
            min-width: 80px;
        }
        .control-row button:hover {
            background: #45a049;
        }
        .control-row button.danger {
            background: #f44336;
        }
        .control-row button.danger:hover {
            background: #da190b;
        }
        .control-row button.warning {
            background: #ff9800;
        }
        .control-row button.warning:hover {
            background: #e68a00;
        }
        .status-indicator {
            padding: 5px 10px;
            border-radius: 4px;
            font-weight: bold;
            display: inline-block;
        }
        .status-idle { background: #4CAF50; color: white; }
        .status-moving { background: #ff9800; color: white; }
        .status-emergency_stop { background: #f44336; color: white; }
        .status-burst { background: #2196F3; color: white; }
        .button-group {
            display: flex;
            gap: 5px;
        }
    </style>
</head>
<body>
    <div class="container" id="container">
        <div class="camera-section" id="cameraSection">
            <img src="stream.mjpg" id="camera-feed">
        </div>
        <div class="divider" id="divider" onmousedown="startResize(event)"></div>
        <div class="controls-section" id="controlsSection">
            <div class="control-panel">
                <h2>Emergency Stop</h2>
                <div class="control-row">
                    <button class="danger" onclick="emergencyStop()" style="width: 100%; padding: 15px;">EMERGENCY STOP</button>
                </div>
                <div class="control-row">
                    <label>Motor Status:</label>
                    <span id="motor-state" class="status-indicator status-idle">IDLE</span>
                </div>
            </div>
            
            <div class="control-panel">
                <h2>Jog Panel</h2>
                <div class="control-row">
                    <label>Speed (steps/sec):</label>
                    <input type="number" id="speed" value="1000" min="1" max="10000">
                </div>
                <div class="control-row">
                    <label>Movement amount (steps):</label>
                    <input type="number" id="movement-amount" value="100" min="1">
                </div>
                <div class="control-row">
                    <label>Current position:</label>
                    <input type="text" id="current-position" readonly value="0">
                </div>
                <div class="control-row">
                    <label>Target position:</label>
                    <input type="text" id="target-position" readonly value="0">
                </div>
                <div class="control-row">
                    <div class="button-group">
                        <button onclick="moveMotor('-')">-</button>
                        <button onclick="moveMotor('+')">+</button>
                        <button onclick="setZero()">Set Zero</button>
                    </div>
                </div>
            </div>
            
            <div class="control-panel">
                <h2>Image Burst Panel</h2>
                <div class="control-row">
                    <label>Start position:</label>
                    <input type="number" id="burst-start" value="0">
                </div>
                <div class="control-row">
                    <label>End position:</label>
                    <input type="number" id="burst-end" value="1000">
                </div>
                <div class="control-row">
                    <label>Number of images:</label>
                    <input type="number" id="burst-count" value="10" min="2">
                </div>
                <div class="control-row">
                    <label>Speed (steps/sec):</label>
                    <input type="number" id="burst-speed" value="1000" min="1">
                </div>
                <div class="control-row">
                    <button class="warning" onclick="takeBurst()">Take Burst</button>
                </div>
                <div class="control-row">
                    <label>Burst progress:</label>
                    <progress id="burst-progress" value="0" max="100" style="width: 100%;"></progress>
                </div>
            </div>
        </div>
    </div>

    <script>
        let isResizing = false;
        let startX, startWidth;
        
        function startResize(e) {
            isResizing = true;
            startX = e.clientX;
            startWidth = document.getElementById('cameraSection').offsetWidth;
            document.addEventListener('mousemove', resize);
            document.addEventListener('mouseup', stopResize);
            e.preventDefault();
        }
        
        function resize(e) {
            if (!isResizing) return;
            const width = startWidth + (e.clientX - startX);
            if (width > 200 && width < window.innerWidth - 300) {
                document.getElementById('cameraSection').style.width = width + 'px';
            }
        }
        
        function stopResize() {
            isResizing = false;
            document.removeEventListener('mousemove', resize);
            document.removeEventListener('mouseup', stopResize);
        }
        
        function emergencyStop() {
            fetch('/api/emergency_stop', {method: 'POST'})
                .then(response => response.json())
                .then(data => {
                    updateStatus(data);
                });
        }
        
        function moveMotor(direction) {
            const speed = document.getElementById('speed').value;
            const amount = document.getElementById('movement-amount').value;
            fetch(`/api/move?direction=${direction}&speed=${speed}&amount=${amount}`, {method: 'POST'})
                .then(response => response.json())
                .then(data => {
                    updateStatus(data);
                });
        }
        
        function setZero() {
            fetch('/api/set_zero', {method: 'POST'})
                .then(response => response.json())
                .then(data => {
                    updateStatus(data);
                });
        }
        
        function takeBurst() {
            const start = document.getElementById('burst-start').value;
            const end = document.getElementById('burst-end').value;
            const count = document.getElementById('burst-count').value;
            const speed = document.getElementById('burst-speed').value;
            
            document.getElementById('burst-progress').value = 0;
            
            fetch(`/api/burst?start=${start}&end=${end}&count=${count}&speed=${speed}`, {method: 'POST'})
                .then(response => response.json())
                .then(data => {
                    updateStatus(data);
                });
        }
        
        function updateStatus(data) {
            document.getElementById('current-position').value = data.position;
            document.getElementById('target-position').value = data.target_position;
            
            const stateSpan = document.getElementById('motor-state');
            stateSpan.className = `status-indicator status-${data.state}`;
            stateSpan.textContent = data.state.toUpperCase().replace('_', ' ');
            
            if (data.burst_progress !== undefined) {
                document.getElementById('burst-progress').value = data.burst_progress;
            }
        }
        
        function pollStatus() {
            fetch('/api/status')
                .then(response => response.json())
                .then(data => {
                    updateStatus(data);
                });
        }
        
        // Poll for status updates every 500ms
        setInterval(pollStatus, 500);
        
        // Initial camera section width (50% of viewport)
        window.onload = function() {
            const initialWidth = window.innerWidth * 0.5;
            document.getElementById('cameraSection').style.width = initialWidth + 'px';
        };
    </script>
</body>
</html>
"""

class StepperMotor:
    def __init__(self, dir_pin, step_pin, sleep_pin):
        self.dir_pin = dir_pin
        self.step_pin = step_pin
        self.sleep_pin = sleep_pin
        self.current_position = 0
        self.target_position = 0
        self.is_moving = False
        self.emergency_stop = False
        
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(dir_pin, GPIO.OUT)
        GPIO.setup(step_pin, GPIO.OUT)
        GPIO.setup(sleep_pin, GPIO.OUT)
        
        # Start with motor asleep
        GPIO.output(sleep_pin, GPIO.LOW)
        GPIO.output(dir_pin, GPIO.LOW)
        GPIO.output(step_pin, GPIO.LOW)
    
    def wake(self):
        GPIO.output(self.sleep_pin, GPIO.HIGH)
        time.sleep(0.001)  # Small delay for wake-up
    
    def sleep(self):
        GPIO.output(self.sleep_pin, GPIO.LOW)
    
    def move_to(self, target, speed=1000):
        """Move to absolute position"""
        with motor_lock:
            if motor_status.state == MotorState.EMERGENCY_STOP:
                return False
            
            motor_status.state = MotorState.MOVING
            motor_status.target_position = target
            
        steps_to_move = target - self.current_position
        return self._move_steps(steps_to_move, speed)
    
    def move_relative(self, steps, speed=1000):
        """Move relative to current position"""
        with motor_lock:
            if motor_status.state == MotorState.EMERGENCY_STOP:
                return False
            
            motor_status.state = MotorState.MOVING
            motor_status.target_position = self.current_position + steps
            
        return self._move_steps(steps, speed)
    
    def _move_steps(self, steps, speed):
        """Internal method to move steps"""
        if steps == 0:
            with motor_lock:
                motor_status.state = MotorState.IDLE
            return True
        
        direction = GPIO.HIGH if steps > 0 else GPIO.LOW
        GPIO.output(self.dir_pin, direction)
        
        # Calculate delay based on speed (steps per second)
        step_delay = 1.0 / abs(speed)
        
        self.wake()
        self.is_moving = True
        
        steps_remaining = abs(steps)
        
        try:
            while steps_remaining > 0 and self.is_moving:
                with motor_lock:
                    if motor_status.state == MotorState.EMERGENCY_STOP:
                        self.is_moving = False
                        break
                
                GPIO.output(self.step_pin, GPIO.HIGH)
                time.sleep(step_delay / 2)
                GPIO.output(self.step_pin, GPIO.LOW)
                time.sleep(step_delay / 2)
                
                # Update position
                if direction == GPIO.HIGH:
                    self.current_position += 1
                else:
                    self.current_position -= 1
                
                steps_remaining -= 1
                
                # Update motor status
                with motor_lock:
                    motor_status.position = self.current_position
        
        finally:
            self.is_moving = False
            self.sleep()
            
            with motor_lock:
                if motor_status.state != MotorState.EMERGENCY_STOP:
                    motor_status.state = MotorState.IDLE
                    motor_status.position = self.current_position
                    motor_status.target_position = self.current_position
            
            return steps_remaining == 0
    
    def emergency_stop(self):
        """Emergency stop - stop all movement"""
        with motor_lock:
            motor_status.state = MotorState.EMERGENCY_STOP
        self.is_moving = False
        self.sleep()
    
    def set_zero(self):
        """Set current position as zero"""
        with motor_lock:
            self.current_position = 0
            motor_status.position = 0
            motor_status.target_position = 0
    
    def cleanup(self):
        """Cleanup GPIO"""
        GPIO.cleanup()

class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()
        self.burst_mode = False
        self.burst_dir = None
        self.burst_count = 0
        self.burst_total = 0

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()
            
            # Save image if in burst mode
            if self.burst_mode and self.burst_dir:
                self.save_burst_image(buf)
    
    def save_burst_image(self, frame_data):
        """Save frame during burst capture"""
        with motor_lock:
            current_pos = motor_status.position
            
        # Save image with position in filename
        filename = os.path.join(self.burst_dir, f"burst_{current_pos:04d}.jpeg")
        
        # Only save if we haven't reached the target count
        if self.burst_count < self.burst_total:
            with open(filename, 'wb') as f:
                f.write(frame_data)
            
            self.burst_count += 1
            
            # Update progress
            with motor_lock:
                progress = (self.burst_count / self.burst_total) * 100
                motor_status.burst_progress = progress

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
                logging.warning('Removed streaming client %s: %s', self.client_address, str(e))
        elif self.path.startswith('/api/'):
            self.handle_api()
        else:
            self.send_error(404)
            self.end_headers()
    
    def do_POST(self):
        if self.path.startswith('/api/'):
            self.handle_api()
    
    def handle_api(self):
        """Handle API requests"""
        try:
            if self.path == '/api/status':
                self.send_json_response(self.get_status())
            
            elif self.path == '/api/emergency_stop':
                motor.emergency_stop()
                self.send_json_response(self.get_status())
            
            elif self.path == '/api/set_zero':
                motor.set_zero()
                self.send_json_response(self.get_status())
            
            elif self.path.startswith('/api/move'):
                from urllib.parse import urlparse, parse_qs
                query = parse_qs(urlparse(self.path).query)
                
                direction = query.get('direction', ['+'])[0]
                speed = int(query.get('speed', [1000])[0])
                amount = int(query.get('amount', [100])[0])
                
                if direction == '+':
                    motor.move_relative(amount, speed)
                else:
                    motor.move_relative(-amount, speed)
                
                self.send_json_response(self.get_status())
            
            elif self.path.startswith('/api/burst'):
                from urllib.parse import urlparse, parse_qs
                query = parse_qs(urlparse(self.path).query)
                
                start = int(query.get('start', [0])[0])
                end = int(query.get('end', [1000])[0])
                count = int(query.get('count', [10])[0])
                speed = int(query.get('speed', [1000])[0])
                
                # Start burst in a separate thread
                thread = threading.Thread(target=self.perform_burst, args=(start, end, count, speed))
                thread.daemon = True
                thread.start()
                
                self.send_json_response(self.get_status())
            
            else:
                self.send_error(404)
        
        except Exception as e:
            self.send_error(500, str(e))
    
    def perform_burst(self, start, end, count, speed):
        """Perform burst capture while moving"""
        global burst_counter, burst_stop_requested
        
        # Create burst directory
        burst_dir = None
        while True:
            dir_name = f"burst_{burst_counter:03d}"
            if not os.path.exists(dir_name):
                burst_dir = dir_name
                os.makedirs(burst_dir)
                break
            burst_counter += 1
        
        # Calculate step increment
        step_range = end - start
        step_increment = step_range / (count - 1)
        
        with motor_lock:
            motor_status.state = MotorState.BURST
            output.burst_mode = True
            output.burst_dir = burst_dir
            output.burst_count = 0
            output.burst_total = count
        
        try:
            # Move to start position
            motor.move_to(start, speed)
            
            # Capture images while moving to end
            for i in range(count):
                with motor_lock:
                    if motor_status.state == MotorState.EMERGENCY_STOP:
                        break
                
                target_pos = int(start + (i * step_increment))
                
                # Move to next position (except for first image which is already at start)
                if i > 0:
                    motor.move_to(target_pos, speed)
                
                # Give time for camera to stabilize
                time.sleep(0.1)
        
        finally:
            with motor_lock:
                output.burst_mode = False
                output.burst_dir = None
                if motor_status.state != MotorState.EMERGENCY_STOP:
                    motor_status.state = MotorState.IDLE
                motor_status.burst_progress = 0
    
    def get_status(self):
        """Get current motor status"""
        with motor_lock:
            return {
                'position': motor_status.position,
                'target_position': motor_status.target_position,
                'state': motor_status.state.value,
                'speed': motor_status.speed,
                'burst_progress': getattr(motor_status, 'burst_progress', 0)
            }
    
    def send_json_response(self, data):
        """Send JSON response"""
        import json
        content = json.dumps(data).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(content))
        self.end_headers()
        self.wfile.write(content)

class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

# Initialize motor
motor = StepperMotor(DIR_PIN, STEP_PIN, SLEEP_PIN)

# Initialize camera
picam2 = Picamera2()
picam2.configure(picam2.create_video_configuration(main={"size": (IMAGE_WIDTH, IMAGE_HEIGHT)}))
output = StreamingOutput()
picam2.start_recording(MJPEGEncoder(), FileOutput(output))

try:
    address = ('', 8000)
    server = StreamingServer(address, StreamingHandler)
    print(f"Server started at http://0.0.0.0:8000")
    print(f"Motor initialized - DIR: GPIO{DIR_PIN}, STEP: GPIO{STEP_PIN}, SLEEP: GPIO{SLEEP_PIN}")
    server.serve_forever()
finally:
    picam2.stop_recording()
    motor.cleanup()