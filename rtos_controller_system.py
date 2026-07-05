from yolo_uno import *
from pins import *
from lcd1602 import *
from dht20 import *
import asyncio

# stage 2 core, stage 3 sensor task, stage 4 control tasks: has to live
# here together instead of being split into separate modules.

# Stage 2: shared state protected by a mutex semaphore (02.07.2026)

class SharedState:
    # holds the latest temperature/humidity reading
    # written only by the sensor task, read by the 3 control tasks
    def __init__(self):
        self._temperature = 0.0  # last known temperature
        self._humidity = 0.0     # last known humidity
        self._mutex = asyncio.Semaphore(1)  # value=1 -> classic mutex lock

    async def write(self, temperature, humidity):
        await self._mutex.acquire()  # lock before touching the values
        try:
            self._temperature = temperature  # update temp
            self._humidity = humidity        # update humidity
        finally:
            self._mutex.release()  # always unlock, even if something goes wrong

    async def read(self):
        await self._mutex.acquire()  # lock before reading
        try:
            return (self._temperature, self._humidity)  # return both together
        finally:
            self._mutex.release()  # unlock again


# Stage 2: one row of a state table (stage 2, 02.07.2026)

class State:
    # name          : just for debugging/printing
    # action        : runs once right when we enter this state (e.g. set LED color)
    # duration_ms   : None means reactive (checked every tick), a number means timed
    # next_if_timed : where to go once duration_ms is over, no condition needed
    # next_fn       : function(temp, humid) -> next state name, used for
    #                 reactive states and for timed states that branch after
    #                 the timer runs out
    def __init__(self, name, action, duration_ms=None,
                 next_if_timed=None, next_fn=None):
        self.name = name
        self.action = action
        self.duration_ms = duration_ms
        self.next_if_timed = next_if_timed
        self.next_fn = next_fn


# Stage 2: generic engine that runs any state table above (02.07.2026)
# this class does not know anything about LEDs or thresholds, it just
# follows whatever table it's given, so heater/cooler/humidifier can all
# reuse the same engine instead of each writing their own if/else loop

class StateMachine:
    def __init__(self, states, initial_state, shared_state, poll_ms=500):
        self._states = states              # the state table (dict)
        self._shared_state = shared_state  # reference to the shared temp/humid
        self._poll_ms = poll_ms            # how often tick() gets called
        self._current = initial_state      # current state name
        self._elapsed_ms = 0               # time spent in current state so far
        self._states[self._current].action()  # run the action for the starting state right away

    def _transition_to(self, new_state_name):
        if new_state_name is None or new_state_name == self._current:
            return  # nothing to do, same state or no change requested
        self._current = new_state_name     # move to the new state
        self._elapsed_ms = 0               # reset the timer for the new state
        self._states[self._current].action()  # run its action once

    async def tick(self):
        state = self._states[self._current]      # look up current state config
        temp, humid = await self._shared_state.read()  # read latest sensor values (locked)

        if state.duration_ms is None:
            # reactive state, e.g. heater: just re-check the condition every tick
            if state.next_fn is not None:
                self._transition_to(state.next_fn(temp, humid))
            return

        # timed state, cooler and humidifier: count elapsed time first
        self._elapsed_ms += self._poll_ms
        if self._elapsed_ms >= state.duration_ms:
            if state.next_fn is not None:
                # timed but still branches on a condition once time is up (humidifier RED)
                self._transition_to(state.next_fn(temp, humid))
            else:
                # pure timed transition, no condition (cooler COOLING to IDLE)
                self._transition_to(state.next_if_timed)

    async def run_forever(self):
        while True:
            await asleep_ms(self._poll_ms)  # wait one poll interval
            await self.tick()               # then check/update the state


# Stage 3: sensor task, the only one allowed to write shared state 03.07.2026
# runs every 5000ms as required, updates the LCD and stores the reading

lcd1602 = LCD1602()  # LCD display object
dht20 = DHT20()      # temperature/humidity sensor object

SENSOR_PERIOD_MS = 5000  # fixed period required 


async def task_sensor(shared_state):
    while True:
        temp = await dht20.atemperature()   # read temperature from sensor
        humid = await dht20.ahumidity()     # read humidity from sensor

        await shared_state.write(temp, humid)  # store it (this locks/unlocks internally)

        lcd1602.clear()                     # clear screen before drawing new values
        lcd1602.show("TEMP:", 0, 0)         # label on row 0
        lcd1602.show(str(temp), 0, 6)       # temperature value
        lcd1602.show(chr(0) + "C", 0, 6 + len(str(temp)))  # degree symbol + C

        lcd1602.show("HUMI:", 1, 0)         # label on row 1
        lcd1602.show(str(humid), 1, 6)      # humidity value
        lcd1602.show("%", 1, 6 + len(str(humid)))  # percent sign

        print("[SENSOR] temp={} humid={}".format(temp, humid))  # log to console for debugging

        await asleep_ms(SENSOR_PERIOD_MS)   # wait 5 seconds before reading again


# Stage 4: control tasks, each one just declares a table and lets, 06.07.2026
#StateMachine run it - no manual if/else loops in the tasks below 

CONTROL_PERIOD_MS = 500  # control tasks poll 10x faster than the sensor task

rgb_led_D3 = RGBLed(D3_PIN, 4)   # heater LED
rgb_led_D5 = RGBLed(D5_PIN, 4)   # cooler LED
rgb_led_D7 = RGBLed(D7_PIN, 4)   # humidifier LED


# heater: 3 fixed color zones based on temperature only, no timers 

def heater_next_state(temp, humid):
    if temp < 26:
        return "SAFE"       # below 26C is fine
    elif temp <= 30:
        return "WARNING"    # 26-30C is the warning zone
    else:
        return "CRITICAL"   # above 30C is critical


heater_states = {
    "SAFE": State(
        "SAFE",
        action=lambda: rgb_led_D3.show(0, hex_to_rgb('#00FF00')),  # green
        next_fn=heater_next_state,
    ),
    "WARNING": State(
        "WARNING",
        action=lambda: rgb_led_D3.show(0, hex_to_rgb('#FFFF00')),  #Yellow, because Ohstream don't support orange
        next_fn=heater_next_state,
    ),
    "CRITICAL": State(
        "CRITICAL",
        action=lambda: rgb_led_D3.show(0, hex_to_rgb('#FF0000')),  # red
        next_fn=heater_next_state,
    ),
}


async def task_heater(shared_state):
    # just hand the table to the engine and let it run
    machine = StateMachine(
        heater_states, "SAFE", shared_state, poll_ms=CONTROL_PERIOD_MS
    )
    await machine.run_forever()


# cooler: turns on for a fixed 5s once it's too hot, then re-checks 

def cooler_idle_next_state(temp, humid):
    return "COOLING" if temp > 30 else "IDLE"  # only IDLE decides when to start cooling


cooler_states = {
    "IDLE": State(
        "IDLE",
        action=lambda: rgb_led_D5.show(0, hex_to_rgb('#000000')),  # off
        next_fn=cooler_idle_next_state,
    ),
    "COOLING": State(
        "COOLING",
        action=lambda: rgb_led_D5.show(0, hex_to_rgb('#00FF00')),  # green while cooling
        duration_ms=5000,      # stays on for exactly 5s no matter what
        next_if_timed="IDLE",  # then goes back to IDLE to check again
    ),
}


async def task_cooler(shared_state):
    machine = StateMachine(
        cooler_states, "IDLE", shared_state, poll_ms=CONTROL_PERIOD_MS
    )
    await machine.run_forever()


# humidifier: green to yellow to red cycle while it's too dry 

def humidifier_idle_next_state(temp, humid):
    return "GREEN" if humid < 50 else "IDLE"  # start the cycle if too dry


def humidifier_red_next_state(temp, humid):
    # after the red phase ends, check again: still dry -> restart cycle,
    # otherwise -> turn off
    return "GREEN" if humid < 50 else "IDLE"


humidifier_states = {
    "IDLE": State(
        "IDLE",
        action=lambda: rgb_led_D7.show(0, hex_to_rgb('#000000')),  # off
        next_fn=humidifier_idle_next_state,
    ),
    "GREEN": State(
        "GREEN",
        action=lambda: rgb_led_D7.show(0, hex_to_rgb('#00FF00')),
        duration_ms=5000,        # green lasts 5s
        next_if_timed="YELLOW",  # then always moves to yellow
    ),
    "YELLOW": State(
        "YELLOW",
        action=lambda: rgb_led_D7.show(0, hex_to_rgb('#FFFF00')),
        duration_ms=3000,     # yellow lasts 3s
        next_if_timed="RED",  # then always moves to red
    ),
    "RED": State(
        "RED",
        action=lambda: rgb_led_D7.show(0, hex_to_rgb('#FF0000')),
        duration_ms=2000,                    # red lasts 2s
        next_fn=humidifier_red_next_state,   # then branches based on humidity
    ),
}


async def task_humidifier(shared_state):
    machine = StateMachine(
        humidifier_states, "IDLE", shared_state, poll_ms=CONTROL_PERIOD_MS
    )
    await machine.run_forever()


# then, wiring everything together, 09.07.2026

shared_state = SharedState()  # the one shared temp/humid holder for the whole app

led_D13 = Pins(D13_PIN)  # onboard LED just to show the board is alive


async def task_led_blinky():
    while True:
        await asleep_ms(1000)  # every 1 second
        led_D13.toggle()       # flip the LED on/off


async def setup():
    print("App started")
    create_task(task_led_blinky())              # heartbeat LED
    create_task(task_sensor(shared_state))       # only writer of shared_state
    create_task(task_heater(shared_state))       # reader
    create_task(task_cooler(shared_state))       # reader
    create_task(task_humidifier(shared_state))   # reader


async def main():
    await setup()       # start all tasks first
    while True:
        await asleep_ms(100)  # keep main alive, all real work happens in the tasks above


run_loop(main())  # hand control over to the scheduler