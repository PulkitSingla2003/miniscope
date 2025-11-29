import threading
import queue
import time
import traceback

import serial

from .config import FRAME_BYTES


class SerialReader(threading.Thread):
    """
    Continuously read from serial, yield complete frames (list of 12-bit samples).
    - Non-blocking to GUI: pushes frames to a Queue.
    - Automatically re-syncs if a partial read happens.
    """
    def __init__(self, port: str, baud: int, frame_bytes=FRAME_BYTES, q: queue.Queue = None):
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.frame_bytes = frame_bytes
        self.q = q or queue.Queue(maxsize=8)
        self._stop = threading.Event()
        self.ser = None
        self._reconnect_delay = 1.0  # seconds
        self._buf = bytearray()

    def stop(self):
        self._stop.set()

    def close_ser(self):
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass
        self.ser = None

    def open_ser(self):
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=0.2)
            # flush input to try to start clean
            try:
                self.ser.reset_input_buffer()
            except Exception:
                pass
            return True
        except Exception as e:
            self.ser = None
            return False

    def run(self):
        while not self._stop.is_set():
            try:
                if not self.ser:
                    opened = self.open_ser()
                    if not opened:
                        time.sleep(self._reconnect_delay)
                        continue

                # Read a chunk
                chunk = self.ser.read(256)  # read up to 256 bytes at a time
                if not chunk:
                    # no data this iteration — loop back
                    continue
                self._buf.extend(chunk)

                # While we have at least one full frame, extract them
                while len(self._buf) >= self.frame_bytes:
                    frame_bytes = bytes(self._buf[:self.frame_bytes])
                    # Remove consumed bytes
                    del self._buf[:self.frame_bytes]

                    # Convert to samples (little-endian 16-bit, lower 12 bits valid)
                    # Data is interleaved: {C1S1, C2S1, C1S2, C2S2, ...}
                    samples_ch1 = []
                    samples_ch2 = []
                    for i in range(0, len(frame_bytes), 4):  # step by 4 bytes (2 samples)
                        # Channel 1 sample
                        lo1 = frame_bytes[i]
                        hi1 = frame_bytes[i + 1]
                        raw1 = (hi1 << 8) | lo1
                        adc12_ch1 = raw1 & 0x0FFF
                        samples_ch1.append(adc12_ch1)
                        
                        # Channel 2 sample
                        lo2 = frame_bytes[i + 2]
                        hi2 = frame_bytes[i + 3]
                        raw2 = (hi2 << 8) | lo2
                        adc12_ch2 = raw2 & 0x0FFF
                        samples_ch2.append(adc12_ch2)
                    
                    frame_data = {'ch1': samples_ch1, 'ch2': samples_ch2}

                    # push latest frame: if queue full, drop oldest to not block GUI
                    try:
                        self.q.put_nowait(frame_data)
                    except queue.Full:
                        try:
                            _ = self.q.get_nowait()  # drop one
                        except Exception:
                            pass
                        try:
                            self.q.put_nowait(frame_data)
                        except Exception:
                            pass

            except Exception:
                # On any serial error, close and retry
                try:
                    traceback.print_exc()
                except Exception:
                    pass
                self.close_ser()
                time.sleep(self._reconnect_delay)

        # cleanup
        self.close_ser()
