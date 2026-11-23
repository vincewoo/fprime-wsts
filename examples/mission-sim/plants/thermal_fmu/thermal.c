/*
 * Thermal plant of Payload's heated panel as an FMI 2.0 co-simulation FMU (docs/dynamics.md).
 *
 * The same model as ../thermal.py, with its arithmetic in the same order, so that the two
 * give the same numbers: C dT/dt = P * heater - G * (T - T_env), integrated with fixed
 * sub-steps of RK4. Build it with build.py beside this file (or fprime_wsts.fmi.build_fmu).
 *
 * The FMI types and functions are declared here rather than taken from the standard's
 * headers: an FMU is a shared library with C functions of known names and signatures.
 */
#include <math.h>
#include <stdlib.h>
#include <string.h>

typedef void* fmi2Component;
typedef void* fmi2ComponentEnvironment;
typedef void* fmi2FMUstate;
typedef unsigned int fmi2ValueReference;
typedef double fmi2Real;
typedef int fmi2Integer;
typedef int fmi2Boolean;
typedef char fmi2Char;
typedef const fmi2Char* fmi2String;
typedef char fmi2Byte;
typedef enum { fmi2OK, fmi2Warning, fmi2Discard, fmi2Error, fmi2Fatal, fmi2Pending } fmi2Status;
typedef enum { fmi2ModelExchange, fmi2CoSimulation } fmi2Type;
typedef struct {
    void (*logger)(fmi2ComponentEnvironment, fmi2String, fmi2Status, fmi2String, fmi2String, ...);
    void* (*allocateMemory)(size_t, size_t);
    void (*freeMemory)(void*);
    void (*stepFinished)(fmi2ComponentEnvironment, fmi2Status);
    fmi2ComponentEnvironment componentEnvironment;
} fmi2CallbackFunctions;

#define EXPORT __attribute__((visibility("default")))

/* Value references: those of modelDescription.xml */
enum { VR_C, VR_P, VR_G, VR_T_ENV, VR_T0, VR_SUBSTEP, VR_HEATER, VR_T, VR_COUNT };

typedef struct {
    double v[VR_COUNT];  /* parameters, the input, the output: by value reference */
    double time;
} State;

static const double START[VR_COUNT] = {5.0, 10.0, 0.1, 21.5, 21.5, 0.01, 0.0, 21.5};

static double derivative(const State* s, double temperature, double heater) {
    return (s->v[VR_P] * heater - s->v[VR_G] * (temperature - s->v[VR_T_ENV])) / s->v[VR_C];
}

EXPORT const char* fmi2GetTypesPlatform(void) { return "default"; }
EXPORT const char* fmi2GetVersion(void) { return "2.0"; }

EXPORT fmi2Component fmi2Instantiate(fmi2String name, fmi2Type type, fmi2String guid, fmi2String resources,
                                     const fmi2CallbackFunctions* functions, fmi2Boolean visible,
                                     fmi2Boolean logging) {
    (void)name; (void)resources; (void)functions; (void)visible; (void)logging;
    if (type != fmi2CoSimulation || guid == NULL || strcmp(guid, "{6f1ac3e2-5b1d-4f0e-9d5c-2a6e8b7d3c41}") != 0) {
        return NULL;
    }
    State* s = calloc(1, sizeof(State));
    if (s != NULL) {
        memcpy(s->v, START, sizeof(START));
    }
    return s;
}

EXPORT void fmi2FreeInstance(fmi2Component c) { free(c); }
EXPORT fmi2Status fmi2SetDebugLogging(fmi2Component c, fmi2Boolean on, size_t n, const fmi2String categories[]) {
    (void)c; (void)on; (void)n; (void)categories;
    return fmi2OK;
}
EXPORT fmi2Status fmi2SetupExperiment(fmi2Component c, fmi2Boolean toleranceDefined, fmi2Real tolerance,
                                      fmi2Real startTime, fmi2Boolean stopTimeDefined, fmi2Real stopTime) {
    (void)toleranceDefined; (void)tolerance; (void)stopTimeDefined; (void)stopTime;
    ((State*)c)->time = startTime;
    return fmi2OK;
}
EXPORT fmi2Status fmi2EnterInitializationMode(fmi2Component c) { (void)c; return fmi2OK; }
EXPORT fmi2Status fmi2ExitInitializationMode(fmi2Component c) {
    State* s = c;
    s->v[VR_T] = s->v[VR_T0];
    return (s->v[VR_C] > 0.0 && s->v[VR_SUBSTEP] > 0.0) ? fmi2OK : fmi2Error;
}
EXPORT fmi2Status fmi2Terminate(fmi2Component c) { (void)c; return fmi2OK; }
EXPORT fmi2Status fmi2Reset(fmi2Component c) {
    State* s = c;
    memcpy(s->v, START, sizeof(START));
    s->time = 0.0;
    return fmi2OK;
}

EXPORT fmi2Status fmi2SetReal(fmi2Component c, const fmi2ValueReference vr[], size_t n, const fmi2Real value[]) {
    State* s = c;
    for (size_t i = 0; i < n; i++) {
        if (vr[i] >= VR_COUNT || vr[i] == VR_T) {
            return fmi2Error;
        }
        s->v[vr[i]] = value[i];
    }
    return fmi2OK;
}
EXPORT fmi2Status fmi2GetReal(fmi2Component c, const fmi2ValueReference vr[], size_t n, fmi2Real value[]) {
    const State* s = c;
    for (size_t i = 0; i < n; i++) {
        if (vr[i] >= VR_COUNT) {
            return fmi2Error;
        }
        value[i] = s->v[vr[i]];
    }
    return fmi2OK;
}
EXPORT fmi2Status fmi2SetInteger(fmi2Component c, const fmi2ValueReference vr[], size_t n, const fmi2Integer v[]) {
    (void)c; (void)vr; (void)v;
    return n == 0 ? fmi2OK : fmi2Error;
}
EXPORT fmi2Status fmi2GetInteger(fmi2Component c, const fmi2ValueReference vr[], size_t n, fmi2Integer v[]) {
    (void)c; (void)vr; (void)v;
    return n == 0 ? fmi2OK : fmi2Error;
}
EXPORT fmi2Status fmi2SetBoolean(fmi2Component c, const fmi2ValueReference vr[], size_t n, const fmi2Boolean v[]) {
    (void)c; (void)vr; (void)v;
    return n == 0 ? fmi2OK : fmi2Error;
}
EXPORT fmi2Status fmi2GetBoolean(fmi2Component c, const fmi2ValueReference vr[], size_t n, fmi2Boolean v[]) {
    (void)c; (void)vr; (void)v;
    return n == 0 ? fmi2OK : fmi2Error;
}

EXPORT fmi2Status fmi2DoStep(fmi2Component c, fmi2Real now, fmi2Real h, fmi2Boolean noSetPrior) {
    (void)noSetPrior;
    State* s = c;
    if (h <= 0.0) {
        return fmi2Error;
    }
    const double heater = s->v[VR_HEATER] != 0.0 ? 1.0 : 0.0;
    long count = lround(h / s->v[VR_SUBSTEP]);
    if (count < 1) {
        count = 1;
    }
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
    s->time = now + h;
    return fmi2OK;
}

/* The state, saved and restored whole (canGetAndSetFMUstate, canSerializeFMUstate) */
EXPORT fmi2Status fmi2GetFMUstate(fmi2Component c, fmi2FMUstate* state) {
    State* copy = *state != NULL ? *state : malloc(sizeof(State));
    if (copy == NULL) {
        return fmi2Error;
    }
    *copy = *(State*)c;
    *state = copy;
    return fmi2OK;
}
EXPORT fmi2Status fmi2SetFMUstate(fmi2Component c, fmi2FMUstate state) {
    *(State*)c = *(State*)state;
    return fmi2OK;
}
EXPORT fmi2Status fmi2FreeFMUstate(fmi2Component c, fmi2FMUstate* state) {
    (void)c;
    free(*state);
    *state = NULL;
    return fmi2OK;
}
EXPORT fmi2Status fmi2SerializedFMUstateSize(fmi2Component c, fmi2FMUstate state, size_t* size) {
    (void)c; (void)state;
    *size = sizeof(State);
    return fmi2OK;
}
EXPORT fmi2Status fmi2SerializeFMUstate(fmi2Component c, fmi2FMUstate state, fmi2Byte bytes[], size_t size) {
    (void)c;
    if (size != sizeof(State)) {
        return fmi2Error;
    }
    memcpy(bytes, state, sizeof(State));
    return fmi2OK;
}
EXPORT fmi2Status fmi2DeSerializeFMUstate(fmi2Component c, const fmi2Byte bytes[], size_t size, fmi2FMUstate* state) {
    (void)c;
    if (size != sizeof(State)) {
        return fmi2Error;
    }
    State* copy = malloc(sizeof(State));
    if (copy == NULL) {
        return fmi2Error;
    }
    memcpy(copy, bytes, sizeof(State));
    *state = copy;
    return fmi2OK;
}
