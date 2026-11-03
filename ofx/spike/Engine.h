// The Gig-steady engine: everything that touches pixels or runs the analysis, behind a plain C interface so a
// development build can be reloaded into a running Resolve (see EngineLoader in GigsteadyControls.cpp). The OFX
// plugin keeps the controls, the saved result and the host calls; it hands the engine images and callbacks.
#pragma once
#include <cstddef>

extern "C" {

#define GS_ENGINE_API 1  // bump when any struct below changes; the loader refuses a dylib with another value

struct GsImage {  // float RGBA, rows bottom-up as OFX gives them
    float* data;   // pixel (x1, y1)
    int x1, y1, x2, y2;
    long rowBytes;
};

struct GsRender {
    double time;
    GsImage src, dst;
    int wx1, wy1, wx2, wy2;  // render window
    double scale;            // render scale (x)
    int pattern;             // draw the test squares
    const char* analysis;    // the stored result (may be empty)
};

struct GsHost {  // callbacks into the plugin, valid for one gs_analyse call
    void* ctx;
    int (*fetch)(void* ctx, double t, GsImage* out, void** handle);  // 1 = ok; release handle afterwards
    void (*release)(void* ctx, void* handle);
    void (*log)(const char* line);
};

struct GsAnalyse {
    double t0, t1;      // inclusive frame range to analyse
    double budget;      // crop budget (part of the result's key)
    int padToFrames;    // spike: repeat the result to this many lines to test the stored size
};

struct GsAnalyseResult {
    char* text;  // the result to store; free with gs_free
    int frames, fetched, failed, cancelled;
    double secs;
    int nativePanel;
    char status[256];  // shown in the Status line
};

typedef int (*gs_api_fn)();
typedef const char* (*gs_build_fn)();
typedef void (*gs_render_fn)(const GsRender*);
typedef void (*gs_analyse_fn)(const GsHost*, const GsAnalyse*, GsAnalyseResult*);
typedef void (*gs_free_fn)(char*);

int gs_api();
const char* gs_build();  // build stamp, shown in the status line so a reload is visible
void gs_render(const GsRender* r);
void gs_analyse(const GsHost* host, const GsAnalyse* a, GsAnalyseResult* out);
void gs_free(char* p);
}
