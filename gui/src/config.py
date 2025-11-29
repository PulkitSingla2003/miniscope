# Config
FAKE_FS = 50000  # Hz, fake sampling rate for the demo
ADC_MAX = 4095
VREF = 3.3
NUM_CHANNELS = 2

# UART frame: 128 samples per channel, interleaved, 2 bytes each = 512 bytes
SAMPLES_PER_CHANNEL = 2048
SAMPLES_PER_FRAME = SAMPLES_PER_CHANNEL * NUM_CHANNELS  # 256 total samples
FRAME_BYTES = SAMPLES_PER_FRAME * 2  # 512 bytes
BUFFER_SIZE = 8000  # larger buffer for each channel

#Voltage Scaling Factors
VOLTAGE_MULT_CH1 = 9.0
VOLTAGE_MULT_CH2 = 9.25
