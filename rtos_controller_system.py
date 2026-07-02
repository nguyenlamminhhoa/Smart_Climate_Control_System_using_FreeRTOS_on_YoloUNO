from yolo_uno import *
from pins import *
import asyncio

# =====================================================================
# PHASE 2 - RTOS CORE MODULE
#
# This module implements the two core components defined in
# Phase 0 and Phase 1:
#
#   1. SharedState
#      - Shared memory protected by a semaphore mutex.
#
#   2. StateMachine
#      - Generic table-driven state machine engine.
#      - The engine does not contain device-specific if/else logic.
#      - Device behavior is completely defined by the state table.
# =====================================================================


# ---------------------------------------------------------------------
# 1. SHARED STATE
#
# According to the shared resource design:
#
#   - Sensor Task writes temperature and humidity every 5000 ms.
#   - Heater, Cooler and Humidifier tasks read the shared data
#     every 500 ms.
#
# A single semaphore mutex protects both shared variables so every
# read/write operation is mutually exclusive.
# ---------------------------------------------------------------------
class SharedState:

    # Initialize shared variables and create the mutex.
    def __init__(self):
        self._temperature = 0.0
        self._humidity = 0.0
        self._mutex = asyncio.Semaphore(1)

    # Update both temperature and humidity safely.
    async def write(self, temperature, humidity):

        # Acquire the mutex before accessing shared memory.
        await self._mutex.acquire()

        try:
            # Store the latest sensor readings.
            self._temperature = temperature
            self._humidity = humidity

        finally:
            # Always release the mutex.
            self._mutex.release()

    # Read the shared data safely.
    async def read(self):

        # Acquire the mutex before reading.
        await self._mutex.acquire()

        try:
            # Return both shared values.
            return (self._temperature, self._humidity)

        finally:
            # Always release the mutex.
            self._mutex.release()


# ---------------------------------------------------------------------
# 2. STATE DEFINITION
#
# Each state consists of four standardized fields:
#
#   action
#       Function executed once when entering the state.
#
#   duration_ms
#       None  -> purely reactive state.
#       int   -> timed state.
#
#   next_if_timed
#       Fixed next state after the timer expires.
#
#   next_fn
#       Function that decides the next state dynamically using
#       the current temperature and humidity.
# ---------------------------------------------------------------------
class State:

    # Store all information describing one state.
    def __init__(self,
                 name,
                 action,
                 duration_ms=None,
                 next_if_timed=None,
                 next_fn=None):

        self.name = name
        self.action = action
        self.duration_ms = duration_ms
        self.next_if_timed = next_if_timed
        self.next_fn = next_fn


# ---------------------------------------------------------------------
# 3. GENERIC STATE MACHINE ENGINE
#
# This engine is completely device-independent.
#
# It knows nothing about LEDs, temperature or humidity.
# It only executes the transition table provided by the application.
#
# The same engine can therefore be reused for Heater, Cooler
# and Humidifier without modification.
# ---------------------------------------------------------------------
class StateMachine:

    # Create a new state machine instance.
    def __init__(self,
                 states,
                 initial_state,
                 shared_state,
                 poll_ms=500):

        # Dictionary mapping state names to State objects.
        self._states = states

        # Reference to the shared memory.
        self._shared_state = shared_state

        # State machine execution period.
        self._poll_ms = poll_ms

        # Current active state.
        self._current = initial_state

        # Time spent in the current state.
        self._elapsed_ms = 0

        # Execute the entry action of the initial state.
        self._states[self._current].action()

    # Change to another state.
    def _transition_to(self, new_state_name):

        # Ignore invalid or identical transitions.
        if new_state_name is None or new_state_name == self._current:
            return

        # Update current state.
        self._current = new_state_name

        # Reset elapsed time for the new state.
        self._elapsed_ms = 0

        # Execute the entry action.
        self._states[self._current].action()

    # Execute one polling cycle.
    async def tick():

        # Get the current state object.
        state = self._states[self._current]

        # Read the latest shared sensor values.
        temp, humid = await self._shared_state.read()

        # -----------------------------
        # Purely reactive state.
        # -----------------------------
        if state.duration_ms is None:

            # Evaluate the transition every polling cycle.
            if state.next_fn is not None:
                self._transition_to(state.next_fn(temp, humid))

            return

        # -----------------------------
        # Timed state.
        # -----------------------------

        # Accumulate elapsed time.
        self._elapsed_ms += self._poll_ms

        # Check whether the timer has expired.
        if self._elapsed_ms >= state.duration_ms:

            # Timed + reactive transition.
            if state.next_fn is not None:
                self._transition_to(state.next_fn(temp, humid))

            # Timed-only transition.
            else:
                self._transition_to(state.next_if_timed)

    # Execute the state machine forever.
    async def run_forever():

        while True:

            # Wait until the next polling period.
            await asleep_ms(self._poll_ms)

            # Execute one state machine cycle.
            await self.tick()