/*
 * The thermal plant of examples/mission-sim/plants (thermal.py, thermal_fmu/thermal.c) as an
 * FMI 3.0 co-simulation FMU, for the tests of fprime_wsts.fmi. Same arithmetic, same order.
 * Also a parameter of each of three other types (an Int32 sub-step count, a Boolean that
 * turns the heater off for good, a Float32 gain), to test setting them by name.
 */
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef void* fmi3Instance;
typedef void* fmi3InstanceEnvironment;
typedef void* fmi3FMUState;
typedef uint32_t fmi3ValueReference;
typedef double fmi3Float64;
typedef float fmi3Float32;
typedef int32_t fmi3Int32;
typedef bool fmi3Boolean;
typedef const char* fmi3String;
typedef uint8_t fmi3Byte;
typedef enum { fmi3OK, fmi3Warning, fmi3Discard, fmi3Error, fmi3Fatal } fmi3Status;
typedef void (*fmi3LogMessageCallback)(fmi3InstanceEnvironment, fmi3Status, fmi3String, fmi3String);

#define EXPORT __attribute__((visibility("default")))

enum { VR_C, VR_P, VR_G, VR_T_ENV, VR_T0, VR_SUBSTEP, VR_HEATER, VR_T, VR_REAL_COUNT, VR_DISABLED = 20, VR_GAIN = 21,
       VR_STEPS = 22 };

typedef struct {
    double v[VR_REAL_COUNT];
    bool disabled;
    float gain;
    int32_t steps;  /* steps taken: an Int32 output */
    double time;
    fmi3LogMessageCallback log;
    fmi3InstanceEnvironment env;
} State;

static const double START[VR_REAL_COUNT] = {5.0, 10.0, 0.1, 21.5, 21.5, 0.01, 0.0, 21.5};

static double derivative(const State* s, double temperature, double heater) {
    return (s->v[VR_P] * heater - s->v[VR_G] * (temperature - s->v[VR_T_ENV])) / s->v[VR_C];
}

EXPORT const char* fmi3GetVersion(void) { return "3.0"; }

EXPORT fmi3Instance fmi3InstantiateCoSimulation(fmi3String name, fmi3String token, fmi3String resources,
                                                fmi3Boolean visible, fmi3Boolean logging, fmi3Boolean eventMode,
                                                fmi3Boolean earlyReturn, const fmi3ValueReference required[],
                                                size_t nRequired, fmi3InstanceEnvironment env,
                                                fmi3LogMessageCallback log, void* intermediate) {
    (void)name; (void)resources; (void)visible; (void)logging; (void)eventMode; (void)earlyReturn;
    (void)required; (void)nRequired; (void)intermediate;
    if (token == NULL || strcmp(token, "{0c1d2e3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f}") != 0) {
        if (log != NULL) {
            log(env, fmi3Error, "logStatusError", "wrong instantiation token");
        }
        return NULL;
    }
    State* s = calloc(1, sizeof(State));
    if (s != NULL) {
        memcpy(s->v, START, sizeof(START));
        s->gain = 1.0f;
        s->log = log;
        s->env = env;
    }
    return s;
}

EXPORT void fmi3FreeInstance(fmi3Instance c) { free(c); }
EXPORT fmi3Status fmi3EnterInitializationMode(fmi3Instance c, fmi3Boolean tolDefined, fmi3Float64 tol,
                                              fmi3Float64 start, fmi3Boolean stopDefined, fmi3Float64 stop) {
    (void)tolDefined; (void)tol; (void)stopDefined; (void)stop;
    ((State*)c)->time = start;
    return fmi3OK;
}
EXPORT fmi3Status fmi3ExitInitializationMode(fmi3Instance c) {
    State* s = c;
    s->v[VR_T] = s->v[VR_T0];
    if (s->v[VR_C] <= 0.0) {
        if (s->log != NULL) {
            s->log(s->env, fmi3Error, "logStatusError", "C must be positive");
        }
        return fmi3Error;
    }
    return fmi3OK;
}
EXPORT fmi3Status fmi3Terminate(fmi3Instance c) { (void)c; return fmi3OK; }

EXPORT fmi3Status fmi3SetFloat64(fmi3Instance c, const fmi3ValueReference vr[], size_t n, const fmi3Float64 v[],
                                 size_t nv) {
    State* s = c;
    if (n != nv) return fmi3Error;
    for (size_t i = 0; i < n; i++) {
        if (vr[i] >= VR_REAL_COUNT || vr[i] == VR_T) return fmi3Error;
        s->v[vr[i]] = v[i];
    }
    return fmi3OK;
}
EXPORT fmi3Status fmi3GetFloat64(fmi3Instance c, const fmi3ValueReference vr[], size_t n, fmi3Float64 v[], size_t nv) {
    const State* s = c;
    if (n != nv) return fmi3Error;
    for (size_t i = 0; i < n; i++) {
        if (vr[i] >= VR_REAL_COUNT) return fmi3Error;
        v[i] = s->v[vr[i]];
    }
    return fmi3OK;
}
EXPORT fmi3Status fmi3SetFloat32(fmi3Instance c, const fmi3ValueReference vr[], size_t n, const fmi3Float32 v[],
                                 size_t nv) {
    State* s = c;
    for (size_t i = 0; i < n && i < nv; i++) {
        if (vr[i] != VR_GAIN) return fmi3Error;
        s->gain = v[i];
    }
    return fmi3OK;
}
EXPORT fmi3Status fmi3SetBoolean(fmi3Instance c, const fmi3ValueReference vr[], size_t n, const fmi3Boolean v[],
                                 size_t nv) {
    State* s = c;
    for (size_t i = 0; i < n && i < nv; i++) {
        if (vr[i] != VR_DISABLED) return fmi3Error;
        s->disabled = v[i];
    }
    return fmi3OK;
}
EXPORT fmi3Status fmi3GetInt32(fmi3Instance c, const fmi3ValueReference vr[], size_t n, fmi3Int32 v[], size_t nv) {
    const State* s = c;
    for (size_t i = 0; i < n && i < nv; i++) {
        if (vr[i] != VR_STEPS) return fmi3Error;
        v[i] = s->steps;
    }
    return fmi3OK;
}

EXPORT fmi3Status fmi3DoStep(fmi3Instance c, fmi3Float64 now, fmi3Float64 h, fmi3Boolean noSetPrior,
                             fmi3Boolean* eventNeeded, fmi3Boolean* terminate, fmi3Boolean* early,
                             fmi3Float64* lastTime) {
    (void)noSetPrior;
    State* s = c;
    *eventNeeded = false;
    *terminate = false;
    *early = false;
    const double heater = (s->v[VR_HEATER] != 0.0 && !s->disabled) ? 1.0 : 0.0;
    long count = lround(h / s->v[VR_SUBSTEP]);
    if (count < 1) count = 1;
    const double d = h / (double)count;
    double temperature = s->v[VR_T];
    for (long i = 0; i < count; i++) {
        const double k1 = derivative(s, temperature, heater);
        const double k2 = derivative(s, temperature + d / 2 * k1, heater);
        const double k3 = derivative(s, temperature + d / 2 * k2, heater);
        const double k4 = derivative(s, temperature + d * k3, heater);
        temperature += d / 6 * (k1 + 2 * k2 + 2 * k3 + k4);
    }
    s->v[VR_T] = temperature;
    s->steps++;
    s->time = now + h;
    *lastTime = s->time;
    if (temperature > 1000.0) {
        *terminate = true;  /* the panel has melted: the FMU ends the simulation */
    }
    return fmi3OK;
}

EXPORT fmi3Status fmi3GetFMUState(fmi3Instance c, fmi3FMUState* state) {
    State* copy = *state != NULL ? *state : malloc(sizeof(State));
    if (copy == NULL) return fmi3Error;
    *copy = *(State*)c;
    *state = copy;
    return fmi3OK;
}
EXPORT fmi3Status fmi3SetFMUState(fmi3Instance c, fmi3FMUState state) {
    State* s = c;
    fmi3LogMessageCallback log = s->log;
    fmi3InstanceEnvironment env = s->env;
    *s = *(State*)state;
    s->log = log;
    s->env = env;
    return fmi3OK;
}
EXPORT fmi3Status fmi3FreeFMUState(fmi3Instance c, fmi3FMUState* state) {
    (void)c;
    free(*state);
    *state = NULL;
    return fmi3OK;
}
EXPORT fmi3Status fmi3SerializedFMUStateSize(fmi3Instance c, fmi3FMUState state, size_t* size) {
    (void)c; (void)state;
    *size = sizeof(State);
    return fmi3OK;
}
EXPORT fmi3Status fmi3SerializeFMUState(fmi3Instance c, fmi3FMUState state, fmi3Byte bytes[], size_t size) {
    (void)c;
    if (size != sizeof(State)) return fmi3Error;
    memcpy(bytes, state, sizeof(State));
    return fmi3OK;
}
EXPORT fmi3Status fmi3DeserializeFMUState(fmi3Instance c, const fmi3Byte bytes[], size_t size, fmi3FMUState* state) {
    (void)c;
    if (size != sizeof(State)) return fmi3Error;
    State* copy = malloc(sizeof(State));
    if (copy == NULL) return fmi3Error;
    memcpy(copy, bytes, sizeof(State));
    *state = copy;
    return fmi3OK;
}
