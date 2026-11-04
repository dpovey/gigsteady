// Engine test without Resolve: frames from ffmpeg stand in for the host. Runs gs_analyse on the frames around a
// playhead (through the real gigsteady analyse-frames), then gs_render on chosen frames, writing each output as raw
// float RGBA (top row first) for enginetest.py to compare with the CLI's warp.
//   enginetest <clip> <playhead> <lo> <hi> <outdir> <frame>...
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

#include "Engine.h"

namespace {

int W = 0, H = 0;

// one ffmpeg reader, restarted only when a fetch isn't the next frame (the analysis reads in order)
FILE* gPipe = nullptr;
long gNext = -1;

bool decode(const char* clip, long frame, std::vector<float>& rgba)
{
    if (!gPipe || frame != gNext) {
        if (gPipe) pclose(gPipe);
        char cmd[2048];
        std::snprintf(cmd, sizeof cmd,
                      "/opt/homebrew/bin/ffmpeg -nostdin -v error -hwaccel videotoolbox -ss %.6f -i '%s' -map 0:v:0 "
                      "-vf scale=in_color_matrix=bt709:in_range=pc,format=rgb48le -f rawvideo -",
                      (frame - 0.25) / 25.0, clip);
        gPipe = popen(cmd, "r");
        if (!gPipe) return false;
    }
    std::vector<unsigned short> raw(static_cast<size_t>(W) * H * 3);
    const size_t got = std::fread(raw.data(), 2, raw.size(), gPipe);
    gNext = frame + 1;
    if (got != raw.size()) return false;
    rgba.assign(static_cast<size_t>(W) * H * 4, 1.0f);
    for (int y = 0; y < H; ++y)  // OFX rows run bottom-up
        for (int x = 0; x < W; ++x)
            for (int k = 0; k < 3; ++k)
                rgba[(static_cast<size_t>(H - 1 - y) * W + x) * 4 + k] = raw[(static_cast<size_t>(y) * W + x) * 3 + k] / 65535.0f;
    return true;
}

struct Ctx {
    const char* clip;
};

int fetch(void* c, double t, GsImage* out, void** handle)
{
    auto* buf = new std::vector<float>;
    if (!decode(static_cast<Ctx*>(c)->clip, static_cast<long>(t), *buf)) {
        delete buf;
        return 0;
    }
    *out = GsImage{buf->data(), 0, 0, W, H, static_cast<long>(W) * 16};
    *handle = buf;
    return 1;
}

}  // namespace

int main(int argc, char** argv)
{
    if (argc < 7) {
        std::fprintf(stderr, "usage: enginetest <clip> <playhead> <lo> <hi> <outdir> <frame>...\n");
        return 2;
    }
    W = 3840, H = 2160;  // the test clips are UHD
    Ctx ctx{argv[1]};
    GsHost host{&ctx, fetch, [](void*, void* h) { delete static_cast<std::vector<float>*>(h); },
                [](const char* s) { std::fprintf(stderr, "%s\n", s); }};
    GsAnalyse req{std::atof(argv[2]), std::atof(argv[3]), std::atof(argv[4]), "mode=auto budget=1.100"};
    GsAnalyseResult res{};
    std::thread([&] { gs_analyse(&host, &req, &res); }).join();  // off the main thread: no progress window
    std::printf("%s | frames %d fetched %d failed %d secs %.1f stored %zu bytes\n", res.status, res.frames, res.fetched,
                res.failed, res.secs, res.text ? std::strlen(res.text) : 0);
    if (!res.text) return 1;
    const std::string dir = argv[5];
    if (FILE* f = std::fopen((dir + "/analysis.json").c_str(), "w")) {
        std::fputs(res.text, f);
        std::fclose(f);
    }
    for (int i = 6; i < argc; ++i) {
        const long frame = std::atol(argv[i]);
        std::vector<float> src, dst(static_cast<size_t>(W) * H * 4);
        if (!decode(argv[1], frame, src)) return 1;
        GsRender r{};
        r.time = frame;
        r.src = GsImage{src.data(), 0, 0, W, H, static_cast<long>(W) * 16};
        r.dst = GsImage{dst.data(), 0, 0, W, H, static_cast<long>(W) * 16};
        r.wx2 = W, r.wy2 = H, r.scale = 1;
        r.analysis = res.text;
        r.settings = "";
        gs_render(&r);
        const std::string path = dir + "/render_" + std::to_string(frame) + ".f32";
        FILE* f = std::fopen(path.c_str(), "wb");
        for (int y = H - 1; y >= 0; --y) std::fwrite(&dst[static_cast<size_t>(y) * W * 4], 16, W, f);  // top row first
        std::fclose(f);
        std::printf("wrote %s\n", path.c_str());
    }
    gs_free(res.text);
    return 0;
}
