# Smart Controller System using RTOS
*A table-driven, RTOS-style Smart Climate Control System on YoloUNO (ESP32-S3)*

---

## 1. Project Information

**Project Name:** Smart Controller System using RTOS

**Authors:** Nguyen Lam Minh Hoa (10422030)

             Truong Dang Khoa (10422038)
             
**Instructor:** Dr. Le Trong Nhan

**Course:** Realtime Systems – Vietnamese-German University

**Date:** Summer 2025 – 2026

---

## 2. Abstract

This project implements a Smart Climate Control System on the YoloUNO (ESP32-S3) using a cooperative `asyncio` scheduler to emulate RTOS-style multitasking. A DHT20 sensor feeds temperature/humidity to a mutex-protected shared state, read by three independent control tasks (Heater, Cooler, Humidifier) plus a heartbeat task. All control behavior is driven by one generic, table-driven state-machine engine instead of ad-hoc `if/else` logic.

---

## 3. Features

- 5 concurrent tasks: Blinky (1s), Sensor (5s), Heater/Cooler/Humidifier (0.5s each)
- Single-writer/multiple-reader shared state, protected by a mutex semaphore
- Generic state-machine engine shared by all 3 control tasks (reactive, timed, and timed-then-reactive transitions)
- Sensor error indicator: all actuators blink yellow + LCD shows "Sensor error" on a 0/0 reading, auto-recovers on valid data

---

## 4. System Architecture

```
Read Temperature Task (writer, 5000ms)
            │
            ▼
   Shared State (temp, humid)
     protected by mutex
     │        │        │
     ▼        ▼        ▼
  Heater    Cooler   Humidifier
  (500ms)   (500ms)   (500ms)

Blinky Task (1000ms) — independent, no shared state
```

- **Heater (D3):** SAFE (green, <26°C) → WARNING (yellow, 26–30°C) → CRITICAL (red, >30°C), purely reactive
- **Cooler (D5):** IDLE → COOLING (green, fixed 5s) once T > 30°C, then back to IDLE
- **Humidifier (D7):** IDLE → GREEN (5s) → YELLOW (3s) → RED (2s) once H < 50%, then re-checks humidity to repeat or stop

---

## 5. Installation & Usage

**Requirements:** OhStem Simulator (or YoloUNO board) with DHT20 sensor and 3 RGB LEDs wired to D3/D4, D5/D6, D7/D8.

```bash
git clone https://github.com/nguyenlamminhhoa/Smart_Climate_Control_System_using_FreeRTOS_on_YoloUNO.git
```

Open `rtos_controller_system.py` in the [OhStem Simulator](https://app.ohstem.vn/#!/vr) and run. The Serial Monitor prints `App started`, then `[SENSOR] temp=... humid=...` every 5 seconds, while the LCD and LEDs update live.

---

## 6. Results

All required behaviors were validated in simulation: correct sensor reporting cadence, all 3 heater zones, timed cooler activation/deactivation, the full humidifier green→yellow→red cycle, and the sensor-error blink/recovery override. Full test cases and screenshots are in the [project report](Smart_Controller_System_using_RTOS.pdf).

---

## 7. Limitations

- 5s sensor sampling vs. 0.5s control polling means a state change can take up to ~10s to be detected (observed in Humidifier testing).
- Actuators are simulated via RGB LEDs, no physical output.
- Cooperative `asyncio` scheduler has no task priorities (single-threaded, no preemption).

---

## 8. Future Work

- Event-driven sensor updates instead of fixed-rate polling
- Port to real FreeRTOS on ESP32 with actual actuators and task priorities
- Closed-loop (hysteresis/PID) control
- Wireless monitoring dashboard (MQTT / web server)

---

## 9. References

1. Espressif Systems, *ESP32-S3 Series Datasheet*, 2023.
2. Aosong Electronics, *DHT20 Datasheet*.
3. Real Time Engineers Ltd., *FreeRTOS Documentation* – https://www.freertos.org/
4. Python Software Foundation, *asyncio Documentation* – https://docs.python.org/3/library/asyncio.html
5. OhStem Education, *YoloUNO & Simulator Docs* – https://app.ohstem.vn/#!/vr
