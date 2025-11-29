import csv
import queue
import time
import traceback
from collections import deque

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QComboBox, QLabel, QSlider, QFileDialog
)
from PyQt6.QtCore import QTimer, Qt
import pyqtgraph as pg
import serial.tools.list_ports
import numpy as np

from .config import FAKE_FS, ADC_MAX, VREF, BUFFER_SIZE, FRAME_BYTES, VOLTAGE_MULT_CH1, VOLTAGE_MULT_CH2
from .data_source import FakeSource
from .serial_reader import SerialReader
from .utils import moving_average, find_triggers

# Audio output
try:
    import sounddevice as sd
    AUDIO_AVAILABLE = True
except Exception:
    print("No audio")
    AUDIO_AVAILABLE = False
    sd = None


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Mini Scope (USB)")

        # ---- data / state ----
        self.src_fake = FakeSource()
        self.is_frozen = False
        self.fft_mode = False
        
        # Dual-channel buffers
        self.buffer_ch1 = deque([0] * BUFFER_SIZE, maxlen=BUFFER_SIZE)
        self.buffer_ch2 = deque([0] * BUFFER_SIZE, maxlen=BUFFER_SIZE)
        
        # Trigger state - Independent per channel
        self.trigger_mode = "AUTO"     # AUTO / NORMAL
        self.trigger_rising = True
        self.threshold_ch1 = 2048      # ADC counts
        self.threshold_ch1 = 2048      # ADC counts
        self.threshold_ch2 = 2048      # ADC counts
        self.use_trigger_filter = True # Enable moving average for trigger
        self.trigger_avg_window = 5

        
        # Scaling
        self.time_per_div = 0.001          # s/div (1ms) - unified for both channels
        self.voltage_mult_ch1 = 1.0        # voltage multiplier
        self.voltage_mult_ch2 = 1.0        # voltage multiplier
        self.ch1_offset = 0.0              # vertical offset in volts
        self.ch2_offset = 0.0

        
        # Smart Auto-Scaling State
        self.auto_scale_enabled = True     # Toggle for autoscaling
        self.y_min = 0.0
        self.y_max = 3.3
        self.x_min = 0.0
        self.x_max = 100.0
        self.last_expansion_time = time.time()
        self.auto_scale_timeout = 5.0  # seconds to wait before contracting

        # Channel enables
        self.ch1_enabled = True
        self.ch2_enabled = True

        self.last_data_volts = {}      # dict with 'ch1' and 'ch2' keys

        # Cursor state
        self.cursors_enabled = False
        self.cursor_vertical = True  # Vertical (time) vs Horizontal (voltage)

        # UART
        self.use_uart = False
        self.serial_reader = None
        self.serial_queue = queue.Queue(maxsize=8)
        
        # Audio output
        self.audio_enabled = False
        self.audio_volume = 0.5  # 0.0 to 1.0
        self.audio_stream = None

        # ===== UI Styling =====
        self.setup_styles()
        
        # ===== UI Layout =====
        root = QWidget()
        self.setCentralWidget(root)
        hbox = QHBoxLayout(root)

        # ---- Plot ----
        self.plot = pg.PlotWidget(background=None)
        self.plot.showGrid(x=True, y=True)
        self.plot.addLegend()
        self.curve_ch1 = self.plot.plot(pen=pg.mkPen('g', width=2), name='Channel 1')
        self.curve_ch2 = self.plot.plot(pen=pg.mkPen('y', width=2), name='Channel 2')
        
        # Trigger level indicators (arrows on left side)
        self.trigger_arrow_ch1 = pg.TextItem(text="◄", color='#4CAF50', anchor=(1, 0.5))  # Green for CH1
        self.trigger_arrow_ch2 = pg.TextItem(text="◄", color='#FFC107', anchor=(1, 0.5))  # Yellow for CH2
        self.plot.addItem(self.trigger_arrow_ch1)
        self.plot.addItem(self.trigger_arrow_ch2)
        
        # Cursor readout text overlay on plot
        self.cursor_text = pg.TextItem(text="", color='white', anchor=(1, 0))
        self.cursor_text.setPos(1, 1)  # Will be repositioned dynamically
        self.plot.addItem(self.cursor_text)
        
        hbox.addWidget(self.plot, stretch=3)

        # ---- Cursors (hidden initially) ----
        self.cursor1 = pg.InfiniteLine(angle=90, movable=True, pen='y')
        self.cursor2 = pg.InfiniteLine(angle=90, movable=True, pen='c')
        self.plot.addItem(self.cursor1)
        self.plot.addItem(self.cursor2)
        self.cursor1.setVisible(False)
        self.cursor2.setVisible(False)
        self.cursor1.setValue(0.001)
        self.cursor2.setValue(0.002)
        self.cursor1.sigPositionChanged.connect(self.update_cursors)
        self.cursor2.sigPositionChanged.connect(self.update_cursors)

        # ---- Right panel (controls) - Scrollable ----
        from PyQt6.QtWidgets import QGroupBox, QLineEdit, QScrollArea
        from PyQt6.QtGui import QDoubleValidator
        
        # Create scroll area for controls
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        
        # Container widget for controls
        controls_widget = QWidget()
        controls = QVBoxLayout(controls_widget)
        controls.setSpacing(5)
        
        scroll_area.setWidget(controls_widget)
        hbox.addWidget(scroll_area, stretch=1)

        # ===== Data Source Group =====
        uart_group = QGroupBox("Data Source")
        uart_layout = QVBoxLayout()
        uart_layout.setSpacing(3)
        
        self.use_uart_btn = QPushButton("Using Fake Data")
        self.use_uart_btn.setCheckable(True)
        self.use_uart_btn.setStyleSheet(self.button_style_active)
        self.use_uart_btn.clicked.connect(self.toggle_uart)
        uart_layout.addWidget(self.use_uart_btn)
        
        uart_layout.addWidget(QLabel("COM Port"))
        self.com_select = QComboBox()
        self.update_com_ports()
        uart_layout.addWidget(self.com_select)
        
        # uart_layout.addWidget(QLabel("Baud Rate"))
        # self.baud_select = QComboBox()
        # self.baud_select.addItems(["9600", "57600", "115200", "230400", "460800", "921600"])
        # self.baud_select.setCurrentText("115200")
        # uart_layout.addWidget(self.baud_select)
        
        uart_group.setLayout(uart_layout)
        controls.addWidget(uart_group)

        # ===== Display Controls Group =====
        display_group = QGroupBox("Display")
        display_layout = QVBoxLayout()
        display_layout.setSpacing(3)
        
        self.freeze_btn = QPushButton("Freeze")
        self.freeze_btn.setCheckable(True)
        self.freeze_btn.setStyleSheet(self.button_style_active)
        self.freeze_btn.clicked.connect(self.toggle_freeze)
        display_layout.addWidget(self.freeze_btn)
        
        self.fft_btn = QPushButton("FFT")
        self.fft_btn.setCheckable(True)
        self.fft_btn.setStyleSheet(self.button_style_active)
        self.fft_btn.clicked.connect(self.toggle_fft)
        display_layout.addWidget(self.fft_btn)
        
        self.autoscale_btn = QPushButton("Auto-Scale On")
        self.autoscale_btn.setCheckable(True)
        self.autoscale_btn.setChecked(True)
        self.autoscale_btn.setStyleSheet(self.button_style_active)
        self.autoscale_btn.clicked.connect(self.toggle_autoscale)
        display_layout.addWidget(self.autoscale_btn)
        
        self.cursor_enable_btn = QPushButton("Enable Cursors")
        self.cursor_enable_btn.setCheckable(True)
        self.cursor_enable_btn.setStyleSheet(self.button_style_active)
        self.cursor_enable_btn.clicked.connect(self.toggle_cursors)
        display_layout.addWidget(self.cursor_enable_btn)
        
        display_layout.addWidget(QLabel("Cursor Type"))
        self.cursor_orient = QComboBox()
        self.cursor_orient.addItems(["Vertical", "Horizontal"])
        self.cursor_orient.currentIndexChanged.connect(self.update_cursor_orientation)
        display_layout.addWidget(self.cursor_orient)
        
        # Unified Time Scale
        display_layout.addWidget(QLabel("Time Scale (ms/div)"))
        self.time_slider = QSlider(Qt.Orientation.Horizontal)
        self.time_slider.setRange(1, 100)
        self.time_slider.setValue(10)  # default 1ms
        self.time_slider.valueChanged.connect(self.update_time_scale)
        display_layout.addWidget(self.time_slider)
        self.time_label = QLabel("1.0 ms/div")
        display_layout.addWidget(self.time_label)
        
        display_group.setLayout(display_layout)
        controls.addWidget(display_group)
        
        # ===== Audio Output Group =====
        if AUDIO_AVAILABLE:
            audio_group = QGroupBox("Audio Output")
            audio_layout = QVBoxLayout()
            audio_layout.setSpacing(3)
            
            self.audio_btn = QPushButton("Audio Off")
            self.audio_btn.setCheckable(True)
            self.audio_btn.setStyleSheet(self.button_style_active)
            self.audio_btn.clicked.connect(self.toggle_audio)
            audio_layout.addWidget(self.audio_btn)
            
            audio_layout.addWidget(QLabel("Volume"))
            self.volume_slider = QSlider(Qt.Orientation.Horizontal)
            self.volume_slider.setRange(0, 100)
            self.volume_slider.setValue(50)
            self.volume_slider.valueChanged.connect(self.update_volume)
            audio_layout.addWidget(self.volume_slider)
            self.volume_label = QLabel("50%")
            audio_layout.addWidget(self.volume_label)
            
            audio_group.setLayout(audio_layout)
            controls.addWidget(audio_group)

        # ===== Trigger Settings Group =====
        trigger_group = QGroupBox("Trigger")
        trigger_layout = QVBoxLayout()
        trigger_layout.setSpacing(3)
        
        trigger_layout.addWidget(QLabel("Mode"))
        self.trig_select = QComboBox()
        self.trig_select.addItems(["AUTO", "NORMAL"])
        self.trig_select.currentIndexChanged.connect(self.update_trigger_mode)
        trigger_layout.addWidget(self.trig_select)
        
        self.trig_filter_btn = QPushButton("Filter On")
        self.trig_filter_btn.setCheckable(True)
        self.trig_filter_btn.setChecked(self.use_trigger_filter)
        self.trig_filter_btn.setStyleSheet(self.button_style_active)
        self.trig_filter_btn.clicked.connect(self.toggle_trigger_filter)
        trigger_layout.addWidget(self.trig_filter_btn)
        
        # CH1 Trigger Level
        trigger_layout.addWidget(QLabel("CH1 Level (V)"))
        self.th_slider_ch1 = QSlider(Qt.Orientation.Horizontal)
        self.th_slider_ch1.setRange(0, ADC_MAX)
        self.th_slider_ch1.setValue(self.threshold_ch1)
        self.th_slider_ch1.valueChanged.connect(self.update_threshold_ch1)
        trigger_layout.addWidget(self.th_slider_ch1)
        self.th_label_ch1 = QLabel(f"{self.threshold_ch1 * VREF / ADC_MAX:.2f} V")
        self.th_label_ch1.setStyleSheet("color: #4CAF50; font-weight: bold;")  # Green for CH1
        trigger_layout.addWidget(self.th_label_ch1)
        
        # CH2 Trigger Level
        trigger_layout.addWidget(QLabel("CH2 Level (V)"))
        self.th_slider_ch2 = QSlider(Qt.Orientation.Horizontal)
        self.th_slider_ch2.setRange(0, ADC_MAX)
        self.th_slider_ch2.setValue(self.threshold_ch2)
        self.th_slider_ch2.valueChanged.connect(self.update_threshold_ch2)
        trigger_layout.addWidget(self.th_slider_ch2)
        self.th_label_ch2 = QLabel(f"{self.threshold_ch2 * VREF / ADC_MAX:.2f} V")
        self.th_label_ch2.setStyleSheet("color: #FFC107; font-weight: bold;")  # Yellow for CH2
        trigger_layout.addWidget(self.th_label_ch2)
        
        trigger_group.setLayout(trigger_layout)
        controls.addWidget(trigger_group)

        # ===== Channel 1 Settings Group =====
        ch1_group = QGroupBox("Channel 1")
        ch1_group.setStyleSheet("QGroupBox { color: #4CAF50; font-weight: bold; }")
        ch1_layout = QVBoxLayout()
        ch1_layout.setSpacing(3)
        
        self.ch1_enable_btn = QPushButton("CH1 On")
        self.ch1_enable_btn.setCheckable(True)
        self.ch1_enable_btn.setChecked(True)
        self.ch1_enable_btn.setStyleSheet(self.button_style_ch1)
        self.ch1_enable_btn.clicked.connect(self.toggle_ch1)
        ch1_layout.addWidget(self.ch1_enable_btn)
        
        ch1_layout.addWidget(QLabel("Voltage Multiplier"))
        self.volt_mult_input_ch1 = QLineEdit()
        self.volt_mult_input_ch1.setText("1.0")
        self.volt_mult_input_ch1.setValidator(QDoubleValidator(0.001, 1000.0, 3))
        self.volt_mult_input_ch1.editingFinished.connect(self.update_volt_mult_ch1)
        self.volt_mult_input_ch1.setToolTip("Scale factor for displayed voltage")
        ch1_layout.addWidget(self.volt_mult_input_ch1)
        
        ch1_group.setLayout(ch1_layout)
        controls.addWidget(ch1_group)

        # ===== Channel 2 Settings Group =====
        ch2_group = QGroupBox("Channel 2")
        ch2_group.setStyleSheet("QGroupBox { color: #FFC107; font-weight: bold; }")
        ch2_layout = QVBoxLayout()
        ch2_layout.setSpacing(3)
        
        self.ch2_enable_btn = QPushButton("CH2 On")
        self.ch2_enable_btn.setCheckable(True)
        self.ch2_enable_btn.setChecked(True)
        self.ch2_enable_btn.setStyleSheet(self.button_style_ch2)
        self.ch2_enable_btn.clicked.connect(self.toggle_ch2)
        ch2_layout.addWidget(self.ch2_enable_btn)
        
        ch2_layout.addWidget(QLabel("Voltage Multiplier"))
        self.volt_mult_input_ch2 = QLineEdit()
        self.volt_mult_input_ch2.setText("1.0")
        self.volt_mult_input_ch2.setValidator(QDoubleValidator(0.001, 1000.0, 3))
        self.volt_mult_input_ch2.editingFinished.connect(self.update_volt_mult_ch2)
        self.volt_mult_input_ch2.setToolTip("Scale factor for displayed voltage")
        ch2_layout.addWidget(self.volt_mult_input_ch2)
        
        ch2_group.setLayout(ch2_layout)
        controls.addWidget(ch2_group)

        # ===== Measurements Group =====
        meas_group = QGroupBox("Measurements")
        meas_layout = QVBoxLayout()
        meas_layout.setSpacing(5)
        
        # CH1 Measurements
        meas_layout.addWidget(QLabel("<b>Channel 1</b>"))
        self.meas_label_ch1 = QLabel("Freq: --\nVpp: --\nVavg: --")
        self.meas_label_ch1.setStyleSheet("color: #4CAF50;")
        meas_layout.addWidget(self.meas_label_ch1)
        
        # CH2 Measurements
        meas_layout.addWidget(QLabel("<b>Channel 2</b>"))
        self.meas_label_ch2 = QLabel("Freq: --\nVpp: --\nVavg: --")
        self.meas_label_ch2.setStyleSheet("color: #FFC107;")
        meas_layout.addWidget(self.meas_label_ch2)
        
        meas_group.setLayout(meas_layout)
        controls.addWidget(meas_group)

        

        # ===== Export Group =====
        export_group = QGroupBox("Export")
        export_layout = QVBoxLayout()
        export_layout.setSpacing(3)
        
        self.save_btn = QPushButton("Save CSV")
        self.save_btn.clicked.connect(self.save_csv)
        export_layout.addWidget(self.save_btn)
        
        export_group.setLayout(export_layout)
        controls.addWidget(export_group)

        # Stretch to push everything up
        controls.addStretch(1)

        # ---- Timer ----
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_plot)
        self.timer.start(30)  # ~33 FPS

    # --------------------------------------------------------
    # Styling
    # --------------------------------------------------------
    def setup_styles(self):
        """Setup custom styles for better UX"""
        # Blue theme for general buttons
        self.button_style_active = """
            QPushButton:checked {
                background-color: #2196F3;
                color: white;
                font-weight: bold;
            }
        """
        
        # Green theme for Channel 1
        self.button_style_ch1 = """
            QPushButton:checked {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
            }
        """
        
        # Yellow theme for Channel 2
        self.button_style_ch2 = """
            QPushButton:checked {
                background-color: #FFC107;
                color: black;
                font-weight: bold;
            }
        """
    
    def update_button_text(self, button, active_text, inactive_text):
        """Update button text based on checked state"""
        if button.isChecked():
            button.setText(active_text)
        else:
            button.setText(inactive_text)
    
    def update_trigger_arrows(self):
        """Update trigger arrow positions based on current threshold and voltage multiplier"""
        # Position arrows at a fixed x position (left side of typical plot)
        x_pos = 0
        
        # Calculate scaled trigger voltages
        trig_v_ch1 = (self.threshold_ch1 * VREF / ADC_MAX) * VOLTAGE_MULT_CH1 * self.voltage_mult_ch1
        trig_v_ch2 = (self.threshold_ch2 * VREF / ADC_MAX) * VOLTAGE_MULT_CH2 * self.voltage_mult_ch2
        
        # Position arrows at left edge
        self.trigger_arrow_ch1.setPos(x_pos, trig_v_ch1)
        self.trigger_arrow_ch2.setPos(x_pos, trig_v_ch2)

    # --------------------------------------------------------
    # UI handlers
    # --------------------------------------------------------
    def toggle_freeze(self):
        self.is_frozen = self.freeze_btn.isChecked()
        self.update_button_text(self.freeze_btn, "Frozen", "Freeze")

    def toggle_fft(self):
        self.fft_mode = self.fft_btn.isChecked()
        self.update_button_text(self.fft_btn, "FFT On", "FFT")
        # Hide cursors in FFT mode (indices/units differ)
        self.apply_cursor_visibility()

    def toggle_autoscale(self):
        self.auto_scale_enabled = self.autoscale_btn.isChecked()
        self.update_button_text(self.autoscale_btn, "Auto-Scale On", "Auto-Scale Off")
    
    def toggle_audio(self):
        if not AUDIO_AVAILABLE:
            return
        self.audio_enabled = self.audio_btn.isChecked()
        self.update_button_text(self.audio_btn, "Audio On", "Audio Off")
        
        if self.audio_enabled:
            self.start_audio_stream()
        else:
            self.stop_audio_stream()
    
    def update_volume(self):
        self.audio_volume = self.volume_slider.value() / 100.0
        self.volume_label.setText(f"{self.volume_slider.value()}%")

    def update_trigger_mode(self):
        self.trigger_mode = self.trig_select.currentText()

    def toggle_trigger_filter(self):
        self.use_trigger_filter = self.trig_filter_btn.isChecked()
        self.update_button_text(self.trig_filter_btn, "Filter On", "Filter Off")


    def update_threshold_ch1(self):
        self.threshold_ch1 = self.th_slider_ch1.value()
        # Update voltage label (with multiplier applied)
        voltage = (self.threshold_ch1 * VREF / ADC_MAX) * VOLTAGE_MULT_CH1* self.voltage_mult_ch1
        self.th_label_ch1.setText(f"{voltage:.2f} V")
        # Update trigger arrow position
        self.update_trigger_arrows()

    def update_threshold_ch2(self):
        self.threshold_ch2 = self.th_slider_ch2.value()
        # Update voltage label (with multiplier applied)
        voltage = (self.threshold_ch2 * VREF / ADC_MAX) * VOLTAGE_MULT_CH2 * self.voltage_mult_ch2
        self.th_label_ch2.setText(f"{voltage:.2f} V")
        # Update trigger arrow position
        self.update_trigger_arrows()
    
    def update_time_scale(self):
        # Unified time scale
        # Slider 1-100. Factor 0.02 -> 0.02ms to 2ms per div
        ms_per_div = self.time_slider.value() * 0.02
        self.time_per_div = ms_per_div / 1000.0
        self.time_label.setText(f"{ms_per_div:.3f} ms/div")
    
    def update_volt_mult_ch1(self):
        try:
            val = float(self.volt_mult_input_ch1.text())
            if 0.001 <= val <= 1000.0:
                self.voltage_mult_ch1 = val
                # Update trigger label to reflect new multiplier
                self.update_threshold_ch1()
        except ValueError:
            self.volt_mult_input_ch1.setText(f"{self.voltage_mult_ch1}")
    
    def update_volt_mult_ch2(self):
        try:
            val = float(self.volt_mult_input_ch2.text())
            if 0.001 <= val <= 1000.0:
                self.voltage_mult_ch2 = val
                # Update trigger label to reflect new multiplier
                self.update_threshold_ch2()
        except ValueError:
            self.volt_mult_input_ch2.setText(f"{self.voltage_mult_ch2}")
    
    def toggle_ch1(self):
        self.ch1_enabled = self.ch1_enable_btn.isChecked()
        self.curve_ch1.setVisible(self.ch1_enabled)
        self.update_button_text(self.ch1_enable_btn, "CH1 On", "CH1 Off")
    
    def toggle_ch2(self):
        self.ch2_enabled = self.ch2_enable_btn.isChecked()
        self.curve_ch2.setVisible(self.ch2_enabled)
        self.update_button_text(self.ch2_enable_btn, "CH2 On", "CH2 Off")


    def update_com_ports(self):
        ports = serial.tools.list_ports.comports()
        self.com_select.clear()
        # Show device name and description if available
        for p in ports:
            label = f"{p.device} ({p.description})" if p.description else p.device
            # keep device string as the device only
            self.com_select.addItem(p.device)

    def toggle_cursors(self):
        self.cursors_enabled = self.cursor_enable_btn.isChecked()
        self.apply_cursor_visibility()
        self.update_cursors()
        self.update_button_text(self.cursor_enable_btn, "Cursors On", "Enable Cursors")

    def update_cursor_orientation(self):
        self.cursor_vertical = (self.cursor_orient.currentText() == "Vertical")
        # Vertical = time cursors
        if self.cursor_vertical:
            self.cursor1.setAngle(90)
            self.cursor2.setAngle(90)
            # Place roughly inside current x-range
            self.cursor1.setValue(0.0)
            self.cursor2.setValue(0.002)
        else:
            self.cursor1.setAngle(0)
            self.cursor2.setAngle(0)
            # Place to reasonable volt positions
            self.cursor1.setValue(1.0)
            self.cursor2.setValue(2.0)
        self.apply_cursor_visibility()
        self.update_cursors()

    def apply_cursor_visibility(self):
        visible = self.cursors_enabled and (not self.fft_mode)
        self.cursor1.setVisible(visible)
        self.cursor2.setVisible(visible)

    def calculate_measurements(self, data, fs):
        """Calculate basic wave characteristics: Freq, Vpp, Vavg, Vmin, Vmax"""
        if not data or len(data) < 10:
            return None
            
        arr = np.array(data)
        v_min = np.min(arr)
        v_max = np.max(arr)
        v_pp = v_max - v_min
        v_avg = np.mean(arr)
        
        # Frequency detection using FFT
        # Remove DC component for FFT
        arr_ac = arr - v_avg
        n = len(arr_ac)
        
        # Windowing to reduce leakage
        window = np.hanning(n)
        fft_res = np.fft.rfft(arr_ac * window)
        freqs = np.fft.rfftfreq(n, 1/fs)
        mags = np.abs(fft_res)
        
        # Find peak
        # Ignore very low frequencies (near DC)
        idx = np.argmax(mags[1:]) + 1
        peak_freq = freqs[idx]
        peak_mag = mags[idx]
        
        # Simple heuristic: is peak distinct?
        # Compare peak to average magnitude
        avg_mag = np.mean(mags)
        if peak_mag > 3 * avg_mag:
            freq = peak_freq
        else:
            freq = None
            
        return {
            "v_pp": v_pp,
            "v_avg": v_avg,
            "v_min": v_min,
            "v_max": v_max,
            "freq": freq
        }

    # --------------------------------------------------------
    # UART handling
    # --------------------------------------------------------
    def toggle_uart(self):
        self.use_uart = self.use_uart_btn.isChecked()
        if self.use_uart:
            port = self.com_select.currentText()
            baud = 921600
            # clear any old queue items
            with self.serial_queue.mutex:
                self.serial_queue.queue.clear()
            # create and start reader
            self.serial_reader = SerialReader(port=port, baud=baud, frame_bytes=FRAME_BYTES, q=self.serial_queue)
            self.serial_reader.start()
            # small delay to allow thread to try open
            time.sleep(0.05)
            self.update_button_text(self.use_uart_btn, "Using USB", "Using Fake Data")
        else:
            # stop reader
            if self.serial_reader:
                try:
                    self.serial_reader.stop()
                    self.serial_reader.join(timeout=0.5)
                except Exception:
                    pass
                self.serial_reader = None
            self.update_button_text(self.use_uart_btn, "Using UART", "Using Fake Data")
    
    # --------------------------------------------------------
    # Audio Output
    # --------------------------------------------------------
    def start_audio_stream(self):
        if not AUDIO_AVAILABLE or self.audio_stream is not None:
            return
        try:
            # Use standard audio sample rate (CD quality)
            self.audio_sample_rate = 44100
            self.audio_stream = sd.OutputStream(
                samplerate=self.audio_sample_rate,
                channels=1,
                callback=self.audio_callback,
                blocksize=1024
            )
            self.audio_stream.start()
            print(f"Audio started at {self.audio_sample_rate} Hz")
        except Exception as e:
            print(f"Failed to start audio: {e}")
            self.audio_stream = None
    
    def stop_audio_stream(self):
        if self.audio_stream is not None:
            try:
                self.audio_stream.stop()
                self.audio_stream.close()
            except Exception:
                pass
            self.audio_stream = None
    
    def audio_callback(self, outdata, frames, time_info, status):
        """Audio callback to fill output buffer with waveform data"""
        if not self.last_data_volts or 'ch1' not in self.last_data_volts:
            outdata[:] = 0
            return
        
        # Get CH1 data
        volts = self.last_data_volts['ch1']
        if len(volts) < 10:
            outdata[:] = 0
            return
        
        # Resample from FAKE_FS to 44100 Hz using interpolation
        from scipy import interpolate
        
        # Calculate input samples needed
        ratio = self.audio_sample_rate / FAKE_FS  # 44100 / 2880 = ~15.3
        input_needed = int(frames / ratio) + 2
        
        # Get recent data
        if len(volts) >= input_needed:
            audio_data = volts[-input_needed:]
        else:
            # Repeat data if not enough
            audio_data = (volts * ((input_needed // len(volts)) + 1))[:input_needed]
        
        # Interpolate to upsample
        x_old = np.arange(len(audio_data))
        x_new = np.linspace(0, len(audio_data) - 1, frames)
        f = interpolate.interp1d(x_old, audio_data, kind='linear')
        resampled = f(x_new)
        
        # Normalize to -1 to 1 (center around 1.65V)
        normalized = (resampled - 1.65) / 1.65
        normalized = normalized * self.audio_volume
        normalized = np.clip(normalized, -1.0, 1.0)
        
        outdata[:] = normalized.reshape(-1, 1)

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------
    def save_csv(self):
        if not self.last_data_volts or 'ch1' not in self.last_data_volts:
            return
        fname, _ = QFileDialog.getSaveFileName(self, "Save CSV", "", "CSV files (*.csv)")
        if not fname:
            return
        try:
            with open(fname, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["index", "channel1_voltage", "channel2_voltage"])
                ch1 = self.last_data_volts.get('ch1', [])
                ch2 = self.last_data_volts.get('ch2', [])
                max_len = max(len(ch1), len(ch2))
                for i in range(max_len):
                    v1 = ch1[i] if i < len(ch1) else 0.0
                    v2 = ch2[i] if i < len(ch2) else 0.0
                    w.writerow([i, v1, v2])
        except Exception:
            traceback.print_exc()

    # --------------------------------------------------------
    # Cursors
    # --------------------------------------------------------
    def update_cursors(self):
        """Update cursor readout text overlay on plot."""
        if not self.cursors_enabled or not self.last_data_volts or self.fft_mode:
            self.cursor_text.setText("")
            return
        if 'ch1' not in self.last_data_volts:
            self.cursor_text.setText("")
            return

        ch1_data = self.last_data_volts['ch1']
        N = len(ch1_data)
        Fs = FAKE_FS

        if self.cursor_vertical:
            # Vertical cursors → time measurement
            t1 = self.cursor1.value()
            t2 = self.cursor2.value()
            # interpret x as time (seconds)
            i1 = int(max(0, min(N - 1, t1 * Fs)))
            i2 = int(max(0, min(N - 1, t2 * Fs)))
            v1_ch1 = ch1_data[i1]
            v2_ch1 = ch1_data[i2]
            dv_ch1 = v2_ch1 - v1_ch1
            dt = abs(t2 - t1)
            text = (
                f"P1: {t1:.6f}s ({i1}) → CH1:{v1_ch1:.3f} V\n"
                f"P2: {t2:.6f}s ({i2}) → CH1:{v2_ch1:.3f} V\n"
                f"ΔV(CH1) = {dv_ch1:.3f} V\n"
                f"Δt = {dt:.6f} s"
            )
            self.cursor_text.setText(text)
        else:
            # Horizontal cursors → voltage measurement (absolute lines)
            y1 = float(self.cursor1.value())
            y2 = float(self.cursor2.value())
            dv = abs(y2 - y1)
            text = (
                f"Y1 = {y1:.3f} V\n"
                f"Y2 = {y2:.3f} V\n"
                f"ΔV = {dv:.3f} V"
            )
            self.cursor_text.setText(text)
        
        # Position text at a fixed location relative to data (top-right of data range)
        # Use the last sample index and max voltage in data
        if ch1_data:
            x_pos = (N - 1) / Fs  # Right edge of data in time
            y_pos = self.y_max  # Top of current y range
            self.cursor_text.setPos(x_pos, y_pos)

    # --------------------------------------------------------
    # Plot update loop
    # --------------------------------------------------------
    def update_plot(self):
        if self.is_frozen:
            return

        # Get raw counts (dict with 'ch1' and 'ch2') from UART queue or fake generator
        raw_dict = None
        if self.use_uart:
            # consume all frames and use the latest to avoid backlog/jitter
            latest = None
            try:
                while True:
                    latest = self.serial_queue.get_nowait()
            except queue.Empty:
                pass
            if latest is None:
                return  # no new frame yet
            raw_dict = latest
        else:
            raw_dict = self.src_fake.generate()  # dict of ADC counts

        # If raw is missing or invalid, bail
        if not raw_dict or 'ch1' not in raw_dict or 'ch2' not in raw_dict:
            return

        # Update larger buffers with new data
        for val in raw_dict['ch1']:
            self.buffer_ch1.append(val)
        for val in raw_dict['ch2']:
            self.buffer_ch2.append(val)

        # Work with buffer data (lists of ADC counts)
        raw_ch1 = list(self.buffer_ch1)
        raw_ch2 = list(self.buffer_ch2)

        # Time-Qualified Triggering Strategy (Glitch Rejection)
        # We use raw data directly but require the signal to hold for min_width samples.
        
        # Calculate samples to display based on unified time/div
        # Assume 10 divisions horizontal (typical oscilloscope)
        samples_to_show = int(self.time_per_div * FAKE_FS * 10)
        samples_to_show = max(100, min(samples_to_show, len(raw_ch1)))  # clamp

        # Independent triggering for each channel
        
        # Prepare trigger source data
        if self.use_trigger_filter:
            # Use moving average for trigger detection
            trig_src_ch1 = moving_average(raw_ch1, self.trigger_avg_window)
            trig_src_ch2 = moving_average(raw_ch2, self.trigger_avg_window)
        else:
            trig_src_ch1 = raw_ch1
            trig_src_ch2 = raw_ch2

        # Trigger on CH1 using scaled threshold (raw ADC counts)
        # The trigger compares against raw ADC values, not scaled voltages
        trig_thresh_ch1_adc = self.threshold_ch1
        triggers_ch1 = find_triggers(trig_src_ch1, trig_thresh_ch1_adc, self.trigger_rising, max_found=1, min_width=3)
        
        # Adjust indices if filtered
        if self.use_trigger_filter:
            offset = self.trigger_avg_window - 1
            triggers_ch1 = [t + offset for t in triggers_ch1]

        # Build CH1 data window according to trigger mode
        if self.trigger_mode == "AUTO":
            if len(triggers_ch1) >= 1:
                p0_ch1 = triggers_ch1[0]
                if p0_ch1 + samples_to_show <= len(raw_ch1):
                    data_ch1 = raw_ch1[p0_ch1:p0_ch1 + samples_to_show]
                else:
                    data_ch1 = raw_ch1[-samples_to_show:]
            else:
                # No trigger found, show last N samples
                data_ch1 = raw_ch1[-samples_to_show:]
        else:  # NORMAL
            if len(triggers_ch1) >= 1:
                p0_ch1 = triggers_ch1[0]
                if p0_ch1 + samples_to_show <= len(raw_ch1):
                    data_ch1 = raw_ch1[p0_ch1:p0_ch1 + samples_to_show]
                else:
                    # Not enough data, use what we have
                    data_ch1 = raw_ch1[-samples_to_show:]
            else:
                # In NORMAL mode, if no trigger, don't update
                data_ch1 = []

        # Now trigger on CH2 using scaled threshold
        trig_thresh_ch2_adc = self.threshold_ch2
        triggers_ch2 = find_triggers(trig_src_ch2, trig_thresh_ch2_adc, self.trigger_rising, max_found=1, min_width=3)
        
        if self.use_trigger_filter:
            offset = self.trigger_avg_window - 1
            triggers_ch2 = [t + offset for t in triggers_ch2]

        # Build CH2 data window according to trigger mode
        if self.trigger_mode == "AUTO":
            if len(triggers_ch2) >= 1:
                p0_ch2 = triggers_ch2[0]
                if p0_ch2 + samples_to_show <= len(raw_ch2):
                    data_ch2 = raw_ch2[p0_ch2:p0_ch2 + samples_to_show]
                else:
                    data_ch2 = raw_ch2[-samples_to_show:]
            else:
                # No trigger found, show last N samples
                data_ch2 = raw_ch2[-samples_to_show:]
        else:  # NORMAL
            if len(triggers_ch2) >= 1:
                p0_ch2 = triggers_ch2[0]
                if p0_ch2 + samples_to_show <= len(raw_ch2):
                    data_ch2 = raw_ch2[p0_ch2:p0_ch2 + samples_to_show]
                else:
                    # Not enough data, use what we have
                    data_ch2 = raw_ch2[-samples_to_show:]
            else:
                # In NORMAL mode, if no trigger, don't update
                data_ch2 = []

        # If in NORMAL mode and either channel has no trigger, bail
        if self.trigger_mode == "NORMAL" and (not data_ch1 or not data_ch2):
            return

        # Convert to volts
        if self.use_uart:
            scale = VREF / ADC_MAX
            volts_ch1 = [x * scale for x in data_ch1]
            volts_ch2 = [x * scale for x in data_ch2]
        else:
            temp_dict = {'ch1': data_ch1, 'ch2': data_ch2}
            volts_dict = self.src_fake.to_voltage(temp_dict)
            volts_ch1 = volts_dict['ch1']
            volts_ch2 = volts_dict['ch2']

        # Apply offsets and voltage multipliers
        volts_ch1 = [(v + self.ch1_offset) * VOLTAGE_MULT_CH1 * self.voltage_mult_ch1 for v in volts_ch1]
        volts_ch2 = [(v + self.ch2_offset) * VOLTAGE_MULT_CH2 * self.voltage_mult_ch2 for v in volts_ch2]

        self.last_data_volts = {'ch1': volts_ch1, 'ch2': volts_ch2}

        if not self.fft_mode:
            # TIME DOMAIN
            self.plot.setLabel("bottom", "Time (s)")
            self.plot.setLabel("left", "Volts")
            
            if self.auto_scale_enabled:
                # --- Smart Auto-Scaling (Horizontal + Vertical) ---
                # 1. Find min/max of current data
                all_data = volts_ch1 + volts_ch2
                N = max(len(volts_ch1), len(volts_ch2))
                
                if all_data:
                    curr_min = min(all_data)
                    curr_max = max(all_data)
                    
                    # Add some margin for Y axis
                    margin = (curr_max - curr_min) * 0.1 if (curr_max - curr_min) > 0 else 0.1
                    target_y_min = curr_min - margin
                    target_y_max = curr_max + margin
                    
                    # X axis: immediate scaling with offset padding
                    padding_samples = int(N * 0.02)
                    padding_time = padding_samples / FAKE_FS
                    target_x_min = -padding_time
                    target_x_max = (N + padding_samples) / FAKE_FS
                    
                    # Apply X-axis immediately (no delay)
                    self.x_min = target_x_min
                    self.x_max = target_x_max
                    
                    now = time.time()
                    
                    # Y-axis: delayed expansion/contraction
                    expanded = False
                    if target_y_min < self.y_min:
                        self.y_min = target_y_min
                        expanded = True
                    if target_y_max > self.y_max:
                        self.y_max = target_y_max
                        expanded = True
                    
                    if expanded:
                        self.last_expansion_time = now
                    else:
                        # Check for contraction (only if we haven't expanded recently)
                        if (now - self.last_expansion_time) > self.auto_scale_timeout:
                            self.y_min = target_y_min
                            self.y_max = target_y_max
                            self.last_expansion_time = now

                # Apply auto-scaled ranges
                self.plot.disableAutoRange()
                self.plot.setXRange(self.x_min, self.x_max, padding=0)
                self.plot.setYRange(self.y_min, self.y_max, padding=0)
            else:
                # Auto-scale disabled - keep current ranges
                self.plot.disableAutoRange()
            
            # Update both channel curves
            # Create time axis
            t = np.arange(len(volts_ch1)) / FAKE_FS
            
            if self.ch1_enabled:
                self.curve_ch1.setData(t, volts_ch1)
            if self.ch2_enabled:
                self.curve_ch2.setData(t, volts_ch2)
            
            # Update trigger arrows position
            self.update_trigger_arrows()
            
            # Keep cursor visibility up to date and refresh readout
            self.apply_cursor_visibility()
            self.update_cursors()
            
            # Update Measurements
            if self.ch1_enabled and volts_ch1:
                m1 = self.calculate_measurements(volts_ch1, FAKE_FS)
                if m1:
                    f_str = f"{m1['freq']:.1f} Hz" if m1['freq'] else "--"
                    self.meas_label_ch1.setText(
                        f"Freq: {f_str}\n"
                        f"Vpp:  {m1['v_pp']:.2f} V\n"
                        f"Vavg: {m1['v_avg']:.2f} V"
                    )
                else:
                    self.meas_label_ch1.setText("Freq: --\nVpp: --\nVavg: --")
            else:
                self.meas_label_ch1.setText("Channel Off")
                
            if self.ch2_enabled and volts_ch2:
                m2 = self.calculate_measurements(volts_ch2, FAKE_FS)
                if m2:
                    f_str = f"{m2['freq']:.1f} Hz" if m2['freq'] else "--"
                    self.meas_label_ch2.setText(
                        f"Freq: {f_str}\n"
                        f"Vpp:  {m2['v_pp']:.2f} V\n"
                        f"Vavg: {m2['v_avg']:.2f} V"
                    )
                else:
                    self.meas_label_ch2.setText("Freq: --\nVpp: --\nVavg: --")
            else:
                self.meas_label_ch2.setText("Channel Off")

            return

        # FFT MODE (magnitude spectrum)
        Fs = FAKE_FS
        n = len(volts_ch1)
        if n < 16:
            return

        # FFT for both channels
        yf_ch1 = np.fft.rfft(volts_ch1)
        yf_ch2 = np.fft.rfft(volts_ch2)
        xf = np.fft.rfftfreq(n, 1 / Fs)
        mag_ch1 = np.abs(yf_ch1)
        mag_ch2 = np.abs(yf_ch2)

        self.plot.setLabel("bottom", "Frequency (Hz)")
        self.plot.setLabel("left", "Magnitude")
        self.plot.enableAutoRange()
        
        if self.ch1_enabled:
            self.curve_ch1.setData(xf, mag_ch1)
        if self.ch2_enabled:
            self.curve_ch2.setData(xf, mag_ch2)
        
        # Hide cursors in FFT mode
        self.apply_cursor_visibility()

    def closeEvent(self, ev):
        # Stop audio stream
        self.stop_audio_stream()
        
        # ensure serial thread stops
        if self.serial_reader:
            try:
                self.serial_reader.stop()
                self.serial_reader.join(timeout=0.5)
            except Exception:
                pass
        ev.accept()
