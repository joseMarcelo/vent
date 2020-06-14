# Sensor Manager
import time
from datetime import datetime
from multiprocessing import Process, Queue, Array, Value
import logging

import board
import busio

import rpi2c
import valve

from sensor_lps import PressureSensorLPS

i2c_in = rpi2c.rpi_i2c(1)
i2c_ex = rpi2c.rpi_i2c(3)

VCO = 2.40256
MAXPA = 4000


def check_spontaneous(pressure, breathing, assist):
    if pressure < -(assist) and breathing.value == 0:
        logging.warn("spontaneous breath initiated")
        breathing.value = 1

def sensor_prime(pressure_in_1, pressure_in_2, pressure_ex_1, pressure_ex_2):
    for i in range(0,100):
        time.sleep(0.005)
        pressure_in_1.read()
        pressure_in_2.read()
        pressure_ex_1.read()
        pressure_ex_2.read()


def sensor_loop(times, flow, volume, tidal,
                pmin, pmax, expire, breathing,
                in_pressure_1, in_pressure_2, in_flow,
                ex_pressure_1, ex_pressure_2, ex_flow,
                idx, count, assist):

    # inspiration
    pressure_in_1 = PressureSensorLPS(i2c_in, address=0x5d)
    pressure_in_2 = PressureSensorLPS(i2c_in, address=0x5c)

    # expiration
    pressure_ex_1 = PressureSensorLPS(i2c_ex, address=0x5d)
    pressure_ex_2 = PressureSensorLPS(i2c_ex, address=0x5c)

    # calibration routine
    sensor_prime(pressure_in_1, pressure_in_2, pressure_ex_1, pressure_ex_2)
    pressure_in_1.zero_pressure()
    pressure_in_2.zero_pressure()
    pressure_ex_1.zero_pressure()
    pressure_ex_2.zero_pressure()
    sensor_prime(pressure_in_1, pressure_in_2, pressure_ex_1, pressure_ex_2)

    # open outfile
    fname = datetime.now().strftime("%Y-%m-%d-%H-%M-%S.out")
    f = open(fname, "wb", 0)

    # state management
    state_breathing = 0        # valve open when breathing = 1
    state_volume_sum = 0       # accumulator to track volume (inhalation - exhalation)
    state_sample_sum = 0       # accumulator to track samples
    state_last_samples = 75    # calculated number of samples in last breath 
    state_tidal_sum = 0        # accumulator to track tidal volume (exhalation volume)
    state_last_tidal = 0       # calculated tidal volume of last breath
    state_pmin_min = MAXPA     # minimum pressure per breath cycle
    state_last_pmin = 0        # calculated min pressure in last breath cycle
    state_pmax_max = 0         # maximum pressure per breath cycle
    state_last_pmax = 0        # calculate max pressure in last breath cycle
    state_start_expire = 0     # start of last exspire
    state_last_expire = 0      # calculated expiry time per breath cycle
    
    while True:
        pressure_in_1.read()
        pressure_in_2.read()
        pressure_ex_1.read()
        pressure_ex_2.read()

        idx.value += 1
        if idx.value >= count.value:
            idx.value = 0

        # update timestamp
        ts = time.time()
        times[idx.value] = ts
        
        # inspiration
        p1 = pressure_in_1.data.pressure
        p2 = pressure_in_2.data.pressure
        in_pressure_1[idx.value] = p1
        in_pressure_2[idx.value] = p2
        in_flow[idx.value] = VCO * (abs(p2-p1)**0.5)
        
        # expiration
        p1 = pressure_ex_1.data.pressure
        p2 = pressure_ex_2.data.pressure
        ex_pressure_1[idx.value] = p1
        ex_pressure_2[idx.value] = p2
        ex_flow[idx.value] = VCO * (abs(p2-p1)**0.5)

        if (breathing.value == 1):
            # transition from expire to inspire reset volume state calculations
            if state_breathing == 0:
                state_breathing = 1
                state_volume_sum = 0
                state_last_tidal = state_tidal_sum
                state_tidal_sum = 0
                state_last_samples = state_sample_sum
                state_sample_sum = 0
                state_last_pmin = state_pmin_min
                state_pmin_min = MAXPA
                state_last_pmax = state_pmax_max
                state_pmax_max = 0
                if state_start_expire > 0:
                    state_last_expire = ts - state_start_expire
                    state_start_expire = 0

            # update inspiration metrics
            state_sample_sum += 1
            state_volume_sum += in_flow[idx.value]
            state_pmax_max = max(state_pmax_max, in_pressure_2[idx.value])
            flow[idx.value] = in_flow[idx.value]
        else:
            # transition from inspire to expire capture time
            if state_breathing == 1:
                state_breathing = 0
                state_start_expire = ts
            # update expiration metrics
            state_sample_sum += 1
            state_tidal_sum += ex_flow[idx.value]
            state_volume_sum -= ex_flow[idx.value]
            state_pmin_min = min(state_pmin_min, ex_pressure_2[idx.value])
            flow[idx.value] = -ex_flow[idx.value]

        volume[idx.value] = state_volume_sum / state_last_samples * 60 # volume changes throughout breathing cycle
        tidal[idx.value] = state_last_tidal / state_last_samples * 60  # tidal volume counted at end of breathing cycle
        pmin[idx.value] = state_last_pmin                              # minimum pressure at end of last breathing cycle
        pmax[idx.value] = state_last_pmax                              # maximum pressure at end of last breathing cycle
        expire[idx.value] = state_last_expire                          # expiration time of last breath

        if assist > 0:
            check_spontaneous(in_pressure_2[idx.value], breathing, assist)

        f.write(bytearray(b"%f %f %f %f %f %f %f %f %f %f\n" % (
            times[idx.value],
            flow[idx.value],
            volume[idx.value],
            tidal[idx.value],
            in_pressure_1[idx.value],
            in_pressure_2[idx.value],
            in_flow[idx.value],
            ex_pressure_1[idx.value],
            ex_pressure_2[idx.value],
            ex_flow[idx.value])))
             
        time.sleep(0.005) 
    
if __name__ == '__main__':
    idx = Value('i', 0)
    count = Value('i', 1000)
    times = Array('d', range(count.value))

    breathing = Value('i', 0)
    flow = Array('d', range(count.value))
    volume = Array('d', range(count.value))
    tidal = Array('d', range(count.value))
    pmin = Array('d', range(count.value))
    
    in_pressure_1 = Array('d', range(count.value))
    in_pressure_2 = Array('d', range(count.value))
    in_flow = Array('d', range(count.value))
    ex_pressure_1 = Array('d', range(count.value))
    ex_pressure_2 = Array('d', range(count.value))
    ex_flow = Array('d', range(count.value))
    
    p = Process(target=sensor_loop, args=(
        times, flow, volume, tidal, pmin, breathing,
        in_pressure_1, in_pressure_2, in_flow,
        ex_pressure_1, ex_pressure_2, ex_flow,
        idx, count))

    p.start()
    input()
    p.terminate()


