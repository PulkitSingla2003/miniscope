import math
import random
from collections import deque

from .config import FAKE_FS, ADC_MAX, VREF, BUFFER_SIZE


class FakeSource:
    def __init__(self, buf_size=BUFFER_SIZE, vref=VREF, fs=FAKE_FS):
        self.dt = 1 / fs
        self.fs = fs
        self.t = 0.0
        self.buf_ch1 = deque([0] * buf_size, maxlen=buf_size)
        self.buf_ch2 = deque([0] * buf_size, maxlen=buf_size)
        self.noise = 15
        self.vref = vref
        self.adc_max = ADC_MAX
        self.freq1 = 800   # Hz sine for channel 1
        self.freq2 = 1200  # Hz sine for channel 2

    def generate(self):
        """Generate new samples and update circular buffers for both channels."""
        for _ in range(60):
            # Channel 1: 80 Hz sine
            v1 = 2048 + 700 * math.sin(2 * math.pi * self.freq1 * self.t)
            v1 += random.randint(-self.noise, self.noise)
            # Add random spikes (simulating noise glitches)
            if random.random() < 0.01:  # 1% chance
                v1 += random.choice([-1000, 1000])
            v1 = max(0, min(self.adc_max, int(v1)))
            self.buf_ch1.append(v1)
            
            # Channel 2: 120 Hz sine with different amplitude
            v2 = 2048 + 500 * math.sin(2 * math.pi * self.freq2 * self.t)
            v2 += random.randint(-self.noise, self.noise)
            # Add random spikes
            if random.random() < 0.01:
                v2 += random.choice([-1000, 1000])
            v2 = max(0, min(self.adc_max, int(v2)))
            self.buf_ch2.append(v2)
            
            self.t += self.dt
        return {'ch1': list(self.buf_ch1), 'ch2': list(self.buf_ch2)}

    def to_voltage(self, data_dict):
        """ADC counts → volts for both channels."""
        scale = self.vref / self.adc_max
        return {
            'ch1': [x * scale for x in data_dict['ch1']],
            'ch2': [x * scale for x in data_dict['ch2']]
        }
