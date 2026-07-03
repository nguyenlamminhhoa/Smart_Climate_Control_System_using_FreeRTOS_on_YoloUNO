from yolo_uno import *
from pins import *
from lcd1602 import *
from dht20 import *
import asyncio

# =====================================================================
# NOTE: The OhStem simulator only runs a SINGLE .py file (no import of
# custom local modules). Everything (Stage 2, Stage 3, and later Stage 4)
# must live in this one file. Sections below are separated by stage
# markers so the design from Stage 0/1 can still be traced in the code.
# =====================================================================


# =====================================================================
# STAGE 2 - CORE RTOS BUILDING BLOCKS
# ---------------------------------------------------------------------
# 1) SharedState: shared memory for temperature/humidity, protected by
#    ONE Semaphore mutex (Stage 0, Shared Resource Table).
# 2) State / StateMachine: generic "table-driven" state machine engine,
#    reused by every control task in Stage 4 (no per-device if/else).
# =====================================================================

class SharedState:
    """
    Single writer (Sensor Task, Stage 3) / multiple readers
    (Heater/Cooler/Humidifier Control Tasks, Stage 4).
    All access goes through read()/write(), which internally
    acquire/release the mutex - callers never touch the Semaphore
    directly.
    """
    def __init__(self):
        self._temperature = 0.0
        self._humidity = 0.0
        self._mutex = asyncio.Semaphore(1)

    async def write(self, temperature, humidity):
        await self._mutex.acquire()
        try:
            self._temperature = temperature
            self._humidity = humidity
        finally:
            self._mutex.release()

    async def read(self):
        await self._mutex.acquire()
        try:
            return (self._temperature, self._humidity)
        finally:
            self._mutex.release()


class State:
    """
    One entry of a state-transition table (Stage 1, design tables).
      action        : function to run once when entering this state
                       (e.g. set an RGB LED color)
      duration_ms   : None       -> reactive state (checked every tick)
                       an int    -> timed state (held for this long)
      next_if_timed : fixed next-state name used when duration_ms
                       elapses and there is no extra condition
                       (timed-only transition, e.g. Cooler)
      next_fn(temp, humid) -> next state name
                       used for reactive transitions (e.g. Heater) and
                       for timed states that branch after the timer
                       (e.g. Humidifier RED -> GREEN or IDLE)
    """
    def __init__(self, name, action, duration_ms=None,
                 next_if_timed=None, next_fn=None):
        self.name = name
        self.action = action
        self.duration_ms = duration_ms
        self.next_if_timed = next_if_timed
        self.next_fn = next_fn


class StateMachine:
    """
    Generic engine that runs ANY state table built from the State class
    above. It knows nothing about LEDs or sensor thresholds - all of
    that lives in the table passed in by each control task (Stage 4).
    This is what satisfies the teacher's requirement: "code follows the
    design, no ad-hoc if/else feature creep".
    """
    def __init__(self, states, initial_state, shared_state, poll_ms=500):
        self._states = states
        self._shared_state = shared_state
        self._poll_ms = poll_ms
        self._current = initial_state
        self._elapsed_ms = 0
        self._states[self._current].action()

    def _transition_to(self, new_state_name):
        if new_state_name is None or new_state_name == self._current:
            return
        self._current = new_state_name
        self._elapsed_ms = 0
        self._states[self._current].action()

    async def tick(self):
        state = self._states[self._current]
        temp, humid = await self._shared_state.read()

        if state.duration_ms is None:
            # Reactive state (e.g. Heater): re-check condition every tick
            if state.next_fn is not None:
                self._transition_to(state.next_fn(temp, humid))
            return

        # Timed state (e.g. Cooler, Humidifier)
        self._elapsed_ms += self._poll_ms
        if self._elapsed_ms >= state.duration_ms:
            if state.next_fn is not None:
                # timed + reactive branch (e.g. Humidifier RED -> ...)
                self._transition_to(state.next_fn(temp, humid))
            else:
                # timed only (e.g. Cooler COOLING -> IDLE)
                self._transition_to(state.next_if_timed)

    async def run_forever(self):
        while True:
            await asleep_ms(self._poll_ms)
            await self.tick()


# =====================================================================
# STAGE 3 - SENSOR TASK
# ---------------------------------------------------------------------
# The ONLY task allowed to WRITE into SharedState (Stage 0 rule).
# Runs at a fixed 5000 ms period, per the teacher's requirement
# ("LCD checks every 5 seconds, other tasks check every 0.5 second").
# Name is "task_sensor" (not "Producer") - naming is flexible per the
# teacher's note, only the single-writer role matters.
# =====================================================================

lcd1602 = LCD1602()
dht20 = DHT20()

SENSOR_PERIOD_MS = 5000


async def task_sensor(shared_state):
    while True:
        temp = await dht20.atemperature()
        humid = await dht20.ahumidity()

        # STAGE 2 link: write() does mutex.acquire() -> update -> release()
        await shared_state.write(temp, humid)

        lcd1602.clear()
        lcd1602.show("TEMP:", 0, 0)
        lcd1602.show(str(temp), 0, 6)
        lcd1602.show(chr(0) + "C", 0, 6 + len(str(temp)))

        lcd1602.show("HUMI:", 1, 0)
        lcd1602.show(str(humid), 1, 6)
        lcd1602.show("%", 1, 6 + len(str(humid)))

        print("[SENSOR] temp={} humid={}".format(temp, humid))

        await asleep_ms(SENSOR_PERIOD_MS)


# =====================================================================
# STAGE 4 - CONTROL TASKS (Heater / Cooler / Humidifier)
# ---------------------------------------------------------------------
# Each device below does NOT write its own if/elif control logic.
# Instead it only declares:
#   (a) the RGB hardware pin,
#   (b) a state table built from the State class (Stage 2),
#   (c) next_fn "condition functions" that just compare against the
#       thresholds already fixed in Stage 0.
# The actual polling / timing / transition logic is 100% handled by the
# shared StateMachine engine (Stage 2). This is what keeps the code
# "design-first": if a threshold changes, only the table changes, the
# engine never does.
#
# All three control tasks poll every 500 ms, per the teacher's
# requirement ("other tasks check every 0.5 second"), independent of
# the 5000 ms Sensor Task (Stage 3).
# =====================================================================

CONTROL_PERIOD_MS = 500

rgb_led_D3 = RGBLed(D3_PIN, 4)   # Heater indicator
rgb_led_D5 = RGBLed(D5_PIN, 4)   # Cooler indicator
rgb_led_D7 = RGBLed(D7_PIN, 4)   # Humidifier indicator


# ---------------------------------------------------------------------
# STAGE 4.1 - HEATER CONTROL TASK (D3/D4)
# Design reference: Stage 1, table 1 (pure reactive, no timed state).
# Thresholds fixed in Stage 0, table 1:
#   T < 26degC            -> SAFE     (GREEN)
#   26degC <= T <= 30degC -> WARNING  (ORANGE)
#   T > 30degC            -> CRITICAL (RED)
# ---------------------------------------------------------------------
def heater_next_state(temp, humid):
    if temp < 26:
        return "SAFE"
    elif temp <= 30:
        return "WARNING"
    else:
        return "CRITICAL"


heater_states = {
    "SAFE": State(
        "SAFE",
        action=lambda: rgb_led_D3.show(0, hex_to_rgb('#00FF00')),
        next_fn=heater_next_state,
    ),
    "WARNING": State(
        "WARNING",
        action=lambda: rgb_led_D3.show(0, hex_to_rgb('#FF8000')),
        next_fn=heater_next_state,
    ),
    "CRITICAL": State(
        "CRITICAL",
        action=lambda: rgb_led_D3.show(0, hex_to_rgb('#FF0000')),
        next_fn=heater_next_state,
    ),
}


async def task_heater(shared_state):
    machine = StateMachine(
        heater_states, "SAFE", shared_state, poll_ms=CONTROL_PERIOD_MS
    )
    await machine.run_forever()


# ---------------------------------------------------------------------
# STAGE 4.2 - COOLER CONTROL TASK (D5/D6)
# Design reference: Stage 1, table 2 (timed-only transition).
# Thresholds fixed in Stage 0, table 1:
#   T > 30degC -> COOLING (GREEN), held for a fixed 5000 ms
#   after 5000 ms -> back to IDLE, re-check temperature
# ---------------------------------------------------------------------
def cooler_idle_next_state(temp, humid):
    return "COOLING" if temp > 30 else "IDLE"


cooler_states = {
    "IDLE": State(
        "IDLE",
        action=lambda: rgb_led_D5.show(0, hex_to_rgb('#000000')),
        next_fn=cooler_idle_next_state,
    ),
    "COOLING": State(
        "COOLING",
        action=lambda: rgb_led_D5.show(0, hex_to_rgb('#00FF00')),
        duration_ms=5000,
        next_if_timed="IDLE",
    ),
}


async def task_cooler(shared_state):
    machine = StateMachine(
        cooler_states, "IDLE", shared_state, poll_ms=CONTROL_PERIOD_MS
    )
    await machine.run_forever()


# ---------------------------------------------------------------------
# STAGE 4.3 - HUMIDIFIER CONTROL TASK (D7/D8)
# Design reference: Stage 1, table 3 (timed + reactive branch).
# Thresholds fixed in Stage 0, table 1:
#   H < 50%  -> start cycle: GREEN(5000ms) -> YELLOW(3000ms) -> RED(2000ms)
#   after RED: re-check humidity
#     still H < 50%  -> repeat cycle (back to GREEN)
#     H >= 50%       -> IDLE (off)
# ---------------------------------------------------------------------
def humidifier_idle_next_state(temp, humid):
    return "GREEN" if humid < 50 else "IDLE"


def humidifier_red_next_state(temp, humid):
    return "GREEN" if humid < 50 else "IDLE"


humidifier_states = {
    "IDLE": State(
        "IDLE",
        action=lambda: rgb_led_D7.show(0, hex_to_rgb('#000000')),
        next_fn=humidifier_idle_next_state,
    ),
    "GREEN": State(
        "GREEN",
        action=lambda: rgb_led_D7.show(0, hex_to_rgb('#00FF00')),
        duration_ms=5000,
        next_if_timed="YELLOW",
    ),
    "YELLOW": State(
        "YELLOW",
        action=lambda: rgb_led_D7.show(0, hex_to_rgb('#FFFF00')),
        duration_ms=3000,
        next_if_timed="RED",
    ),
    "RED": State(
        "RED",
        action=lambda: rgb_led_D7.show(0, hex_to_rgb('#FF0000')),
        duration_ms=2000,
        next_fn=humidifier_red_next_state,
    ),
}


async def task_humidifier(shared_state):
    machine = StateMachine(
        humidifier_states, "IDLE", shared_state, poll_ms=CONTROL_PERIOD_MS
    )
    await machine.run_forever()


# =====================================================================
# SETUP / MAIN
# Only Stage 3 wired up so far, for testing on the simulator.
# Stage 4 tasks will be create_task()'d here once ready.
# =====================================================================

shared_state = SharedState()

led_D13 = Pins(D13_PIN)


async def task_led_blinky():
    while True:
        await asleep_ms(1000)
        led_D13.toggle()


async def setup():
    print("App started")
    create_task(task_led_blinky())            # Stage 3 support task
    create_task(task_sensor(shared_state))     # Stage 3 - single writer
    create_task(task_heater(shared_state))     # Stage 4.1 - reader
    create_task(task_cooler(shared_state))     # Stage 4.2 - reader
    create_task(task_humidifier(shared_state)) # Stage 4.3 - reader


async def main():
    await setup()
    while True:
        await asleep_ms(100)


run_loop(main())