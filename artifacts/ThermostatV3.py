#
# Thermostat - This is the Python code used to demonstrate
# the functionality of the thermostat that we have prototyped throughout
# the course. 
#
# This code works with the test circuit that was built for module 7.
#
# Functionality:
#
# The thermostat has three states: off, heat, cool
#
# The lights will represent the state that the thermostat is in.
#
# If the thermostat is set to off, the lights will both be off.
#
# If the thermostat is set to heat, the Red LED will be fading in 
# and out if the current temperature is blow the set temperature;
# otherwise, the Red LED will be on solid.
#
# If the thermostat is set to cool, the Blue LED will be fading in 
# and out if the current temperature is above the set temperature;
# otherwise, the Blue LED will be on solid.
#
# One button will cycle through the three states of the thermostat.
#
# One button will raise the setpoint by a degree.
#
# One button will lower the setpoint by a degree.
#
# The LCD display will display the date and time on one line and
# alternate the second line between the current temperature and 
# the state of the thermostat along with its set temperature.
#
# The Thermostat will send a status update to the TemperatureServer
# over the serial port every 30 seconds in a comma delimited string
# including the state of the thermostat, the current temperature
# in degrees Fahrenheit, and the setpoint of the thermostat.
#
# ------------------------------------------------------------------
# Change History
# ------------------------------------------------------------------
# Version   |   Description
# ------------------------------------------------------------------
#    1          Initial Development
#    2          Added LCD setpoint display and fixed state label
#    3          Added I2C lock to prevent sensor collisions and silenced counter print
#    4          Safe sensor/LCD/serial I/O; optional smoothing + hysteresis
#    5          Daily schedule with binary-search lookup; manual offset over schedule
# ------------------------------------------------------------------

##
## Import necessary to provide timing in the main loop
##
from time import sleep
from datetime import datetime

##
## Imports required to allow us to build a fully functional state machine
##
from statemachine import StateMachine, State

##
## Imports necessary to provide connectivity to the
## thermostat sensor and the I2C bus
##
import board
import adafruit_ahtx0

##
## These are the packages that we need to pull in so that we can work
## with the GPIO interface on the Raspberry Pi board and work with
## the 16x2 LCD display
##
# import board - already imported for I2C connectivity
import digitalio
import adafruit_character_lcd.character_lcd as characterlcd

## This imports the Python serial package to handle communications over the
## Raspberry Pi's serial port.
import serial

##
## Imports required to handle our Button, and our PWMLED devices
##
from gpiozero import Button, PWMLED

##
## This package is necessary so that we can delegate the blinking
## lights to their own thread so that more work can be done at the
## same time
##
from threading import Thread, Lock

##
## This is needed to get coherent matching of temperatures.
##
from math import floor

##
## Additional helpers for optional smoothing and schedule search
##
from collections import deque
import bisect

##
## DEBUG flag - boolean value to indicate whether or not to print
## status messages on the console of the program
##
DEBUG = True

##
## Tunable parameters (Algorithms: smoothing, hysteresis, schedule)
##
MA_WINDOW = 10        # moving average window (samples). Use 1 to disable smoothing.
HYSTERESIS_F = 1.0    # +/- F around setPoint to reduce chattering. Use 0.0 to disable.
SAMPLE_PERIOD_S = 1   # main sample/display refresh period
STATUS_PERIOD_S = 30  # serial status period (seconds)
SCHEDULE_ENABLED = True  # set False for manual-only control

##
## Daily schedule (time-of-day setpoints). Sorted by start time.
## The setpoint in effect is the last entry whose start time is <= current time.
## Example: 06:00 -> 70F, 22:00 -> 65F
##
SCHEDULE_ENTRIES = [
    {"start": "06:00", "setpoint": 70},
    {"start": "22:00", "setpoint": 65}
]

##
## Helper to convert "HH:MM" to minutes since midnight
##
def _hhmm_to_minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)

##
## Preprocess schedule for binary search
##
_schedule_minutes = []
_schedule_values = []
for entry in sorted(SCHEDULE_ENTRIES, key=lambda e: e["start"]):
    _schedule_minutes.append(_hhmm_to_minutes(entry["start"]))
    _schedule_values.append(int(entry["setpoint"]))


##
## Create an I2C instance so that we can communicate with
## devices on the I2C bus.
##
i2c = board.I2C()

##
## Initialize our Temperature and Humidity sensor
##
try:
    thSensor = adafruit_ahtx0.AHTx0(i2c)
except Exception as e:
    thSensor = None
    if (DEBUG):
        print(f"* Sensor init failed: {e}")

##
## Create a lock for I2C access
##
i2c_lock = Lock()

##
## Initialize our serial connection
##
try:
    ser = serial.Serial(
        port='/dev/ttyS0',
        baudrate=115200,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        bytesize=serial.EIGHTBITS,
        timeout=1
    )
except Exception as e:
    ser = None
    if (DEBUG):
        print(f"* Serial init failed: {e}")

##
## Our two LEDs, utilizing GPIO 18, and GPIO 23
##
redLight = PWMLED(18)
blueLight = PWMLED(23)


##
## ManagedDisplay - Class intended to manage the 16x2
## Display
##
class ManagedDisplay():
    def __init__(self):
        self.lcd_rs = digitalio.DigitalInOut(board.D17)
        self.lcd_en = digitalio.DigitalInOut(board.D27)
        self.lcd_d4 = digitalio.DigitalInOut(board.D5)
        self.lcd_d5 = digitalio.DigitalInOut(board.D6)
        self.lcd_d6 = digitalio.DigitalInOut(board.D13)
        self.lcd_d7 = digitalio.DigitalInOut(board.D26)

        self.lcd_columns = 16
        self.lcd_rows = 2

        self.lcd = characterlcd.Character_LCD_Mono(self.lcd_rs, self.lcd_en,
                                                   self.lcd_d4, self.lcd_d5, self.lcd_d6, self.lcd_d7,
                                                   self.lcd_columns, self.lcd_rows)

        self.lcd.clear()

    def cleanupDisplay(self):
        try:
            self.lcd.clear()
            self.lcd.display = False
            sleep(0.05)
        except Exception:
            pass
        self.lcd_rs.deinit()
        self.lcd_en.deinit()
        self.lcd_d4.deinit()
        self.lcd_d5.deinit()
        self.lcd_d6.deinit()
        self.lcd_d7.deinit()

    def clear(self):
        self.lcd.clear()

    def updateScreen(self, message):
        parts = message.split("\n", 1)
        line1 = (parts[0] if len(parts) > 0 else "").ljust(16)[:16]
        line2 = (parts[1] if len(parts) > 1 else "").ljust(16)[:16]

        try:
            self.lcd.clear()
            self.lcd.cursor_position(0, 0)
            self.lcd.message = line1
            self.lcd.cursor_position(0, 1)
            self.lcd.message = line2
        except Exception as e:
            if (DEBUG):
                print(f"* LCD write failed: {e}")


##
## Initialize our display
##
screen = ManagedDisplay()

##
## Simple Moving Average helper (optional smoothing)
##
class MovingAverage():
    def __init__(self, window=MA_WINDOW):
        self.window = max(1, int(window))
        self.buf = deque()
        self.sum = 0.0

    def push(self, x):
        self.buf.append(x)
        self.sum += x
        if len(self.buf) > self.window:
            self.sum -= self.buf.popleft()
        return self.value()

    def value(self):
        return (self.sum / len(self.buf)) if self.buf else None


##
## TemperatureMachine - StateMachine implementation
##
class TemperatureMachine(StateMachine):
    off = State(initial=True)
    heat = State()
    cool = State()

    # Base setpoint and a manual offset that comes from buttons.
    # When schedule is enabled, setPoint is computed as baseSetPoint + manualOffset.
    baseSetPoint = 72
    manualOffset = 0
    setPoint = 72  # effective value used by control and display

    # moving average instance for smoothing (shared in this controller)
    ma = MovingAverage(MA_WINDOW)

    cycle = (
        off.to(heat) |
        heat.to(cool) |
        cool.to(off)
    )

    def on_enter_heat(self):
        self.updateLights()
        if (DEBUG):
            print("* Changing state to heat")

    def on_exit_heat(self):
        redLight.off()

    def on_enter_cool(self):
        self.updateLights()
        if (DEBUG):
            print("* Changing state to cool")

    def on_exit_cool(self):
        blueLight.off()

    def on_enter_off(self):
        redLight.off()
        blueLight.off()
        if (DEBUG):
            print("* Changing state to off")

    def processTempStateButton(self):
        if (DEBUG):
            print("Cycling Temperature State")
        self.cycle()

    def processTempIncButton(self):
        if (DEBUG):
            print("Increasing Set Point (manual offset)")
        self.manualOffset = self.manualOffset + 1
        self._refreshEffectiveSetPoint()
        self.updateLights()

    def processTempDecButton(self):
        if (DEBUG):
            print("Decreasing Set Point (manual offset)")
        self.manualOffset = self.manualOffset - 1
        self._refreshEffectiveSetPoint()
        self.updateLights()

    ##
    ## Schedule lookup: compute baseSetPoint from time-of-day,
    ## then apply manualOffset to get the effective setPoint.
    ##
    def _refreshEffectiveSetPoint(self):
        # If schedule is enabled, compute base from schedule; otherwise keep existing base.
        if SCHEDULE_ENABLED and _schedule_minutes:
            now = datetime.now()
            minutes = now.hour * 60 + now.minute
            idx = bisect.bisect_right(_schedule_minutes, minutes) - 1
            if idx < 0:
                # wrap to last entry of previous day
                base = _schedule_values[-1]
            else:
                base = _schedule_values[idx]
            self.baseSetPoint = int(base)

        self.setPoint = int(self.baseSetPoint + self.manualOffset)

    def updateLights(self):
        # Always keep setPoint current with schedule before controlling outputs
        self._refreshEffectiveSetPoint()

        temp = self._getSmoothedFahrenheit()
        redLight.off()
        blueLight.off()

        if (DEBUG):
            print(f"State: {self.current_state.id}")
            print(f"BaseSetPoint: {self.baseSetPoint}  ManualOffset: {self.manualOffset}  EffectiveSetPoint: {self.setPoint}")
            print(f"Temp(smoothed): {('--' if temp is None else round(temp,1))}")

        state = self.current_state.id
        if state == "off":
            return

        # If sensor failed, show steady mode indicator (fail-safe)
        if temp is None:
            if state == "heat":
                redLight.value = 1.0
            elif state == "cool":
                blueLight.value = 1.0
            return

        # Apply simple hysteresis band around the setPoint to reduce flicker
        if state == "heat":
            if temp < (self.setPoint - HYSTERESIS_F):
                redLight.pulse(fade_in_time=0.5, fade_out_time=0.5, n=None, background=True)
            elif temp >= self.setPoint:
                redLight.value = 1.0
            else:
                redLight.value = 1.0
            return

        if state == "cool":
            if temp > (self.setPoint + HYSTERESIS_F):
                blueLight.pulse(fade_in_time=0.5, fade_out_time=0.5, n=None, background=True)
            elif temp <= self.setPoint:
                blueLight.value = 1.0
            else:
                blueLight.value = 1.0
            return

    def run(self):
        myThread = Thread(target=self.manageMyDisplay)
        myThread.start()

    def getFahrenheit(self):
        # safe sensor read with lock (may return None)
        if thSensor is None:
            return None
        try:
            with i2c_lock:
                t = thSensor.temperature
            return (((9 / 5) * t) + 32)
        except Exception as e:
            if (DEBUG):
                print(f"* Sensor read failed: {e}")
            return None

    def _getSmoothedFahrenheit(self):
        # Wrap raw read with optional moving average
        t_f = self.getFahrenheit()
        if t_f is None:
            return None
        return self.ma.push(t_f)

    def setupSerialOutput(self):
        state = self.current_state.id
        temp_sm = self._getSmoothedFahrenheit()
        try:
            temp_f = floor(temp_sm) if temp_sm is not None else "NA"
        except Exception:
            temp_f = "NA"
        output = f"{state},{temp_f},{self.setPoint}"
        return output

    endDisplay = False

    def manageMyDisplay(self):
        counter = 1
        altCounter = 1
        while not self.endDisplay:
            # keep setPoint in sync with schedule on each tick
            self._refreshEffectiveSetPoint()

            current_time = datetime.now()
            lcd_line_1 = current_time.strftime("%m/%d %H:%M:%S").ljust(16) + "\n"

            if (altCounter < 6):
                temp_sm = self._getSmoothedFahrenheit()
                t_show = "--" if temp_sm is None else f"{floor(temp_sm)}"
                lcd_line_2 = f"Temp:{t_show}F Set:{self.setPoint}F".ljust(16)[:16]
                altCounter = altCounter + 1
            else:
                state_label = self.current_state.id.upper()
                lcd_line_2 = f"{state_label}".ljust(16)[:16]
                altCounter = altCounter + 1
                if (altCounter >= 11):
                    self.updateLights()
                    altCounter = 1

            screen.updateScreen(lcd_line_1 + lcd_line_2)

            # Safe serial write every STATUS_PERIOD_S seconds
            if ((counter % STATUS_PERIOD_S) == 0):
                try:
                    if ser is not None:
                        ser.write((self.setupSerialOutput() + "\n").encode("utf-8"))
                except Exception as e:
                    if (DEBUG):
                        print(f"* Serial write failed: {e}")
                counter = 1
            else:
                counter = counter + 1

            sleep(SAMPLE_PERIOD_S)

        screen.cleanupDisplay()


##
## Setup our State Machine
##
tsm = TemperatureMachine()
tsm.run()

##
## Configure buttons
##
blueButton = Button(24)
blueButton.when_pressed = tsm.processTempStateButton

redButton = Button(12)
redButton.when_pressed = tsm.processTempIncButton

yellowButton = Button(25)
yellowButton.when_pressed = tsm.processTempDecButton

##
## Setup loop variable
##
repeat = True

##
## Repeat until user interrupt
##
while repeat:
    try:
        sleep(30)
    except KeyboardInterrupt:
        print("Cleaning up. Exiting...")
        repeat = False
        tsm.endDisplay = True
        sleep(1)
        screen.cleanupDisplay()
        try:
            redLight.off(); blueLight.off()
        except Exception:
            pass
