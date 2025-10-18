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
## DEBUG flag - boolean value to indicate whether or not to print
## status messages on the console of the program
##
DEBUG = True

##
## Create an I2C instance so that we can communicate with
## devices on the I2C bus.
##
i2c = board.I2C()

##
## Initialize our Temperature and Humidity sensor
##
thSensor = adafruit_ahtx0.AHTx0(i2c)

##
## Create a lock for I2C access
##
i2c_lock = Lock()

##
## Initialize our serial connection
##
ser = serial.Serial(
    port='/dev/ttyS0',
    baudrate=115200,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    bytesize=serial.EIGHTBITS,
    timeout=1
)

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

        self.lcd.clear()
        self.lcd.cursor_position(0, 0)
        self.lcd.message = line1
        self.lcd.cursor_position(0, 1)
        self.lcd.message = line2


##
## Initialize our display
##
screen = ManagedDisplay()


##
## TemperatureMachine - StateMachine implementation
##
class TemperatureMachine(StateMachine):
    off = State(initial=True)
    heat = State()
    cool = State()

    setPoint = 72

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
            print("Increasing Set Point")
        self.setPoint = self.setPoint + 1
        self.updateLights()

    def processTempDecButton(self):
        if (DEBUG):
            print("Decreasing Set Point")
        self.setPoint = self.setPoint - 1
        self.updateLights()

    def updateLights(self):
        temp = floor(self.getFahrenheit())
        redLight.off()
        blueLight.off()

        if (DEBUG):
            print(f"State: {self.current_state.id}")
            print(f"SetPoint: {self.setPoint}")
            print(f"Temp: {temp}")

        state = self.current_state.id
        if state == "off":
            return

        if state == "heat":
            if temp < self.setPoint:
                redLight.pulse(fade_in_time=0.5, fade_out_time=0.5, n=None, background=True)
            else:
                redLight.value = 1.0
            return

        if state == "cool":
            if temp > self.setPoint:
                blueLight.pulse(fade_in_time=0.5, fade_out_time=0.5, n=None, background=True)
            else:
                blueLight.value = 1.0
            return

    def run(self):
        myThread = Thread(target=self.manageMyDisplay)
        myThread.start()

    def getFahrenheit(self):
        with i2c_lock:
            t = thSensor.temperature
        return (((9 / 5) * t) + 32)

    def setupSerialOutput(self):
        state = self.current_state.id
        temp_f = floor(self.getFahrenheit())
        output = f"{state},{temp_f},{self.setPoint}"
        return output

    endDisplay = False

    def manageMyDisplay(self):
        counter = 1
        altCounter = 1
        while not self.endDisplay:
            current_time = datetime.now()
            lcd_line_1 = current_time.strftime("%m/%d %H:%M:%S").ljust(16) + "\n"

            if (altCounter < 6):
                temp_f = floor(self.getFahrenheit())
                lcd_line_2 = f"Temp:{temp_f}F Set:{self.setPoint}F".ljust(16)[:16]
                altCounter = altCounter + 1
            else:
                state_label = self.current_state.id.upper()
                lcd_line_2 = f"{state_label}".ljust(16)[:16]
                altCounter = altCounter + 1
                if (altCounter >= 11):
                    self.updateLights()
                    altCounter = 1

            screen.updateScreen(lcd_line_1 + lcd_line_2)

            # Counter update silenced
            if ((counter % 30) == 0):
                ser.write((self.setupSerialOutput() + "\n").encode("utf-8"))
                counter = 1
            else:
                counter = counter + 1
            sleep(1)

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
