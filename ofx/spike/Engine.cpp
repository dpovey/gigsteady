// Gig-steady engine (spike): pass-through render with the test pattern, and the stand-in analysis behind Stabilise.
// Built twice: into the plugin bundle (the fallback) and as a development dylib that the plugin reloads when it
// changes (make engine). See Engine.h.
#include "Engine.h"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#include "ProgressPanel.h"

#ifdef GS_ENGINE_DYLIB
#define GS_EXPORT __attribute__((visibility("default")))
#else
#define GS_EXPORT
#endif

namespace {

float* px(const GsImage& im, int x, int y)
{
    if (x < im.x1 || x >= im.x2 || y < im.y1 || y >= im.y2) return nullptr;
    return reinterpret_cast<float*>(reinterpret_cast<char*>(im.data) + (y - im.y1) * im.rowBytes) + 4 * (x - im.x1);
}

// White square at the frame centre and a red one to its right, 1/10 of the width each
void drawPattern(const GsRender* r)
{
    const GsImage& d = r->dst;
    const int cx = (d.x1 + d.x2) / 2, cy = (d.y1 + d.y2) / 2, h = static_cast<int>(192 * r->scale);
    const int boxes[2][2] = {{cx, cy}, {cx + static_cast<int>(768 * r->scale), cy}};
    const float colour[2][3] = {{1, 1, 1}, {1, 0, 0}};
    for (int k = 0; k < 2; ++k)
        for (int y = std::max(r->wy1, boxes[k][1] - h); y < std::min(r->wy2, boxes[k][1] + h); ++y)
            for (int x = std::max(r->wx1, boxes[k][0] - h); x < std::min(r->wx2, boxes[k][0] + h); ++x)
                if (float* p = px(d, x, y)) {
                    p[0] = colour[k][0];
                    p[1] = colour[k][1];
                    p[2] = colour[k][2];
                    p[3] = 1;
                }
}

double meanGrid(const GsImage& im)
{
    double sum = 0;
    int k = 0;
    for (int j = 0; j < 32; ++j)
        for (int i = 0; i < 32; ++i) {
            int x = im.x1 + (im.x2 - im.x1) * (2 * i + 1) / 64, y = im.y1 + (im.y2 - im.y1) * (2 * j + 1) / 64;
            if (const float* p = px(im, x, y)) {
                sum += 0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2];
                ++k;
            }
        }
    return k ? sum / k : -1;
}

}  // namespace

extern "C" {

GS_EXPORT int gs_api() { return GS_ENGINE_API; }

GS_EXPORT const char* gs_build()
{
#ifdef GS_ENGINE_DYLIB
    return "dev engine " __DATE__ " " __TIME__;
#else
    return "built-in engine";
#endif
}

GS_EXPORT void gs_render(const GsRender* r)
{
    // copy where the source covers the render window, transparent black elsewhere
    const int x1 = std::max(r->wx1, r->src.x1), x2 = std::min(r->wx2, r->src.x2);
    for (int y = r->wy1; y < r->wy2; ++y) {
        float* d = px(r->dst, r->wx1, y);
        if (!d) continue;
        std::memset(d, 0, sizeof(float) * 4 * (r->wx2 - r->wx1));
        const float* s = x1 < x2 ? px(r->src, x1, y) : nullptr;
        if (s) std::memcpy(d + 4 * (x1 - r->wx1), s, sizeof(float) * 4 * (x2 - x1));
    }
    if (r->pattern) drawPattern(r);
}

GS_EXPORT void gs_analyse(const GsHost* host, const GsAnalyse* a, GsAnalyseResult* out)
{
    const int n = static_cast<int>(a->t1 - a->t0) + 1;
    std::string lines;
    int fetched = 0, failed = 0;
    bool cancelled = false;
    auto start = std::chrono::steady_clock::now();
    ProgressPanel* panel = progressOpen("Gig-steady", "Analysing camera movement...");
    for (int i = 0; i < n; ++i) {
        GsImage im{};
        void* h = nullptr;
        double mean = -1;
        if (host->fetch(host->ctx, a->t0 + i, &im, &h)) {
            ++fetched;
            mean = meanGrid(im);
            host->release(host->ctx, h);
        } else {
            ++failed;
        }
        char b[96];
        std::snprintf(b, sizeof b, "%d %.4f %.4f %.5f %.5f\n", static_cast<int>(a->t0) + i, mean, 0.0, 0.0, 1.0);
        lines += b;
        char text[96];
        std::snprintf(text, sizeof text, "Analysing camera movement: frame %d of %d", i + 1, n);
        if (!progressStep(panel, static_cast<double>(i + 1) / n, text)) {
            cancelled = true;
            break;
        }
    }
    progressClose(panel);
    const std::string real = lines;
    while (!real.empty() && static_cast<int>(std::count(lines.begin(), lines.end(), '\n')) < a->padToFrames) lines += real;

    out->secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    out->frames = n;
    out->fetched = fetched;
    out->failed = failed;
    out->cancelled = cancelled;
    out->nativePanel = panel != nullptr;
    out->text = cancelled ? nullptr : strdup(lines.c_str());
    if (cancelled)
        std::snprintf(out->status, sizeof out->status, "Cancelled after %d of %d frames; previous result kept.",
                      fetched + failed, n);
    else
        std::snprintf(out->status, sizeof out->status, "Analysed %d frames (%d failed) in %.1f s; stored %zu KB.", n,
                      failed, out->secs, lines.size() / 1024);
}

GS_EXPORT void gs_free(char* p) { std::free(p); }
}
