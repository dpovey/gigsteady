// Gig-steady engine (spike). Built twice: into the plugin bundle (the fallback) and as a development dylib that the
// plugin reloads when it changes (make engine). See Engine.h.
//
// Stabilise: fetch the frames around the playhead from Resolve, shrink each to analysis-size luma (960 wide, top row
// first) and stream them to `gigsteady analyse-frames` (src/gigsteady/frames.py), the same analysis as the CLI.
// Its JSON (mode, zoom, per-frame correction in full-frame pixels) is wrapped with the first frame number and stored
// in the project. Render: warp each frame by correction then zoom, as warp.output_matrices does; frames outside the
// analysed range pass through.
#include "Engine.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <fcntl.h>
#include <signal.h>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>

#include "ProgressPanel.h"

#ifdef GS_ENGINE_DYLIB
#define GS_EXPORT __attribute__((visibility("default")))
#else
#define GS_EXPORT
#endif

#ifndef GS_TOOL
#define GS_TOOL "gigsteady"
#endif

namespace {

constexpr int kHalfWindow = 250;        // frames analysed each side of the playhead (the cut's extent isn't known)
constexpr int kAnalysisWidth = 960;     // io.analysis_size
constexpr double kSecsPerFrame = 0.06;  // analysis time, for the progress bar (501 desk frames: 29 s)

float* px(const GsImage& im, int x, int y)
{
    if (x < im.x1 || x >= im.x2 || y < im.y1 || y >= im.y2) return nullptr;
    return reinterpret_cast<float*>(reinterpret_cast<char*>(im.data) + (y - im.y1) * im.rowBytes) + 4 * (x - im.x1);
}

std::string setting(const char* s, const char* key, const char* def)
{
    const std::string all = s ? s : "", k = std::string(key) + "=";
    for (size_t pos = 0; (pos = all.find(k, pos)) != std::string::npos; pos += k.size())
        if (pos == 0 || all[pos - 1] == ' ') {
            const size_t a = pos + k.size(), b = all.find(' ', a);
            return all.substr(a, b == std::string::npos ? std::string::npos : b - a);
        }
    return def;
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

// ---- analysis ----

// Box-average Rec.709 luma into aw x ah bytes, top row first (the analysis is in image coordinates, y down)
void toLuma(const GsImage& im, int aw, int ah, unsigned char* out)
{
    const int w = im.x2 - im.x1, h = im.y2 - im.y1;
    for (int j = 0; j < ah; ++j) {
        const int ya = h - 1 - static_cast<int>(static_cast<long>(j + 1) * h / ah - 1);  // bottom row of the block
        const int yb = h - static_cast<int>(static_cast<long>(j) * h / ah);              // one past its top row
        for (int i = 0; i < aw; ++i) {
            const int xa = static_cast<int>(static_cast<long>(i) * w / aw), xb = static_cast<int>(static_cast<long>(i + 1) * w / aw);
            double sum = 0;
            int n = 0;
            for (int y = ya; y < yb; ++y) {
                const float* p = px(im, im.x1 + xa, im.y1 + y);
                for (int x = xa; x < xb; ++x, p += 4) sum += 0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2], ++n;
            }
            const double v = n ? sum / n : 0;
            out[static_cast<size_t>(j) * aw + i] = static_cast<unsigned char>(std::lround(std::clamp(v, 0.0, 1.0) * 255));
        }
    }
}

bool writeAll(int fd, const unsigned char* p, size_t n)
{
    while (n) {
        const ssize_t k = write(fd, p, n);
        if (k < 0 && errno == EINTR) continue;
        if (k <= 0) return false;
        p += k;
        n -= static_cast<size_t>(k);
    }
    return true;
}

std::string homePath(const char* rel)
{
    const char* home = std::getenv("HOME");
    return std::string(home ? home : "/tmp") + rel;
}

std::string readFile(const std::string& path)
{
    std::string s;
    if (FILE* f = std::fopen(path.c_str(), "rb")) {
        char b[65536];
        size_t k;
        while ((k = std::fread(b, 1, sizeof b, f)) > 0) s.append(b, k);
        std::fclose(f);
    }
    return s;
}

// ---- stored result and warp ----

struct Path {
    int t0 = 0, width = 0, height = 0;
    double zoom = 1;
    std::vector<std::array<double, 4>> p;  // dx, dy (full-frame px), rot (rad), scale
};

double number(const std::string& s, const char* key, size_t from = 0)
{
    const size_t pos = s.find(std::string("\"") + key + "\":", from);
    return pos == std::string::npos ? NAN : std::strtod(s.c_str() + pos + std::strlen(key) + 3, nullptr);
}

std::shared_ptr<const Path> parse(const std::string& s)
{
    auto out = std::make_shared<Path>();
    const size_t res = s.find("\"result\":");
    const size_t pp = s.find("\"p\":[[", res == std::string::npos ? 0 : res);
    if (res == std::string::npos || pp == std::string::npos) return nullptr;
    out->t0 = static_cast<int>(number(s, "t0"));
    out->zoom = number(s, "zoom", res);
    out->width = static_cast<int>(number(s, "width", res));
    out->height = static_cast<int>(number(s, "height", res));
    const char* c = s.c_str() + pp + 5;  // the first inner '['
    while (*c == '[') {
        ++c;
        std::array<double, 4> q{};
        for (double& v : q) {
            char* e = nullptr;
            v = std::strtod(c, &e);
            c = e;
            if (*c == ',') ++c;
        }
        if (*c != ']') return nullptr;
        out->p.push_back(q);
        if (*++c != ',') break;
        ++c;
    }
    if (out->p.empty() || !(out->width > 0) || !(out->height > 0) || !(out->zoom >= 1)) return nullptr;
    return out;
}

std::shared_ptr<const Path> cached(const char* analysis)
{
    static std::mutex m;
    static size_t lastHash = 0, lastSize = 0;
    static std::shared_ptr<const Path> last;
    if (!analysis || !*analysis) return nullptr;
    const std::string s(analysis);
    const size_t h = std::hash<std::string>()(s);
    std::lock_guard<std::mutex> lock(m);
    if (h != lastHash || s.size() != lastSize) {
        last = parse(s);
        lastHash = h;
        lastSize = s.size();
    }
    return last;
}

void copyThrough(const GsRender* r)
{
    const int x1 = std::max(r->wx1, r->src.x1), x2 = std::min(r->wx2, r->src.x2);
    for (int y = r->wy1; y < r->wy2; ++y) {
        float* d = px(r->dst, r->wx1, y);
        if (!d) continue;
        std::memset(d, 0, sizeof(float) * 4 * (r->wx2 - r->wx1));
        const float* s = x1 < x2 ? px(r->src, x1, y) : nullptr;
        if (s) std::memcpy(d + 4 * (x1 - r->wx1), s, sizeof(float) * 4 * (x2 - x1));
    }
}

// Output pixel -> source pixel through the inverse of (zoom about the centre) x (correction), in image coordinates
// (y down, pixel centres on integers, as cv2.warpAffine), then bilinear. OFX rows run bottom-up.
void warp(const GsRender* r, const Path& P, const std::array<double, 4>& q, double zoom)
{
    const double W = P.width, H = P.height, rs = r->scale;
    const double c = q[3] * std::cos(q[2]), si = q[3] * std::sin(q[2]);
    const double a00 = zoom * c, a01 = -zoom * si, a02 = zoom * q[0] + W / 2 * (1 - zoom);
    const double a10 = zoom * si, a11 = zoom * c, a12 = zoom * q[1] + H / 2 * (1 - zoom);
    const double det = a00 * a11 - a01 * a10;
    const double b00 = a11 / det, b01 = -a01 / det, b10 = -a10 / det, b11 = a00 / det;
    const double b02 = -(b00 * a02 + b01 * a12), b12 = -(b10 * a02 + b11 * a12);
    const GsImage& S = r->src;
    auto rows = [&](int ya, int yb) {
        for (int y = ya; y < yb; ++y) {
            float* d = px(r->dst, r->wx1, y);
            if (!d) continue;
            const double ycv = H - 1 - ((y + 0.5) / rs - 0.5);
            for (int x = r->wx1; x < r->wx2; ++x, d += 4) {
                const double X = (x + 0.5) / rs - 0.5;
                const double u = b00 * X + b01 * ycv + b02, v = b10 * X + b11 * ycv + b12;
                const double sx = std::clamp((u + 0.5) * rs - 0.5, double(S.x1), double(S.x2 - 1));
                const double sy = std::clamp((H - 1 - v + 0.5) * rs - 0.5, double(S.y1), double(S.y2 - 1));
                const int ix = std::min(static_cast<int>(sx), S.x2 - 2), iy = std::min(static_cast<int>(sy), S.y2 - 2);
                const float fx = static_cast<float>(sx - ix), fy = static_cast<float>(sy - iy);
                const float *p00 = px(S, ix, iy), *p10 = px(S, ix + 1, iy), *p01 = px(S, ix, iy + 1), *p11 = px(S, ix + 1, iy + 1);
                if (!p00 || !p10 || !p01 || !p11) {
                    d[0] = d[1] = d[2] = d[3] = 0;
                    continue;
                }
                for (int k = 0; k < 4; ++k)
                    d[k] = (p00[k] * (1 - fx) + p10[k] * fx) * (1 - fy) + (p01[k] * (1 - fx) + p11[k] * fx) * fy;
            }
        }
    };
    const int n = r->wy2 - r->wy1, nt = std::clamp(static_cast<int>(std::thread::hardware_concurrency()), 1, 8);
    std::vector<std::thread> ts;
    for (int t = 1; t < nt; ++t) ts.emplace_back(rows, r->wy1 + n * t / nt, r->wy1 + n * (t + 1) / nt);
    rows(r->wy1, r->wy1 + n / nt);
    for (auto& t : ts) t.join();
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
    const auto P = cached(r->analysis);
    const int i = P ? static_cast<int>(std::lround(r->time)) - P->t0 : -1;
    if (P && i >= 0 && i < static_cast<int>(P->p.size())) {
        const double manual = std::atof(setting(r->settings, "zoom", "0").c_str());
        warp(r, *P, P->p[i], manual >= 1 ? manual : P->zoom);
    } else {
        copyThrough(r);
    }
    if (r->pattern) drawPattern(r);
}

GS_EXPORT void gs_analyse(const GsHost* host, const GsAnalyse* a, GsAnalyseResult* out)
{
    const auto start = std::chrono::steady_clock::now();
    auto secs = [&] { return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count(); };
    auto fail = [&](const char* why) {
        std::snprintf(out->status, sizeof out->status, "%s", why);
        out->secs = secs();
    };
    const double t0 = a->hi - a->lo > 2 * kHalfWindow ? std::max(a->lo, a->now - kHalfWindow) : a->lo;
    const double t1 = a->hi - a->lo > 2 * kHalfWindow ? std::min(a->hi, a->now + kHalfWindow) : a->hi;
    const int n = static_cast<int>(t1 - t0) + 1;
    out->t0 = t0, out->t1 = t1, out->frames = n;

    // the first frame sets the size
    GsImage im{};
    void* h = nullptr;
    if (!host->fetch(host->ctx, t0, &im, &h)) return fail("Couldn't read the clip's frames.");
    const int W = im.x2 - im.x1, H = im.y2 - im.y1;
    const int aw = kAnalysisWidth, ah = static_cast<int>(std::lround(H * double(aw) / W / 2)) * 2;
    std::vector<unsigned char> gray(static_cast<size_t>(aw) * ah);

    const std::string mode = setting(a->settings, "mode", "auto"), budget = setting(a->settings, "budget", "1.10");
    const char* tmp = std::getenv("TMPDIR");
    const std::string json = std::string(tmp ? tmp : "/tmp/") + "gigsteady_" + std::to_string(getpid()) + "_" +
                             std::to_string(static_cast<long long>(t0)) + ".json";
    const std::string log = homePath("/Library/Logs/gigsteady-analyse.log");
    unlink(json.c_str());
    const std::string args[] = {GS_TOOL, "analyse-frames", "--frames", std::to_string(n), "--size",
                                std::to_string(aw) + "x" + std::to_string(ah), "--full",
                                std::to_string(W) + "x" + std::to_string(H), "--mode", mode, "--crop-budget", budget,
                                "--json", json};
    std::vector<char*> argv;
    for (const auto& s : args) argv.push_back(const_cast<char*>(s.c_str()));
    argv.push_back(nullptr);
    const std::string envHome = "HOME=" + homePath(""), envTmp = std::string("TMPDIR=") + (tmp ? tmp : "/tmp/");
    char* envp[] = {const_cast<char*>(envHome.c_str()), const_cast<char*>(envTmp.c_str()),
                    const_cast<char*>("PATH=/usr/bin:/bin:/opt/homebrew/bin"), nullptr};

    int fds[2];
    if (pipe(fds) != 0) {
        host->release(host->ctx, h);
        return fail("Couldn't start the analysis (pipe).");
    }
    fcntl(fds[1], F_SETNOSIGPIPE, 1);  // a crashed analysis must not take Resolve down with SIGPIPE
    fcntl(fds[1], F_SETFD, FD_CLOEXEC);
    posix_spawn_file_actions_t fa;
    posix_spawn_file_actions_init(&fa);
    posix_spawn_file_actions_adddup2(&fa, fds[0], 0);
    posix_spawn_file_actions_addclose(&fa, fds[0]);
    posix_spawn_file_actions_addopen(&fa, 1, log.c_str(), O_WRONLY | O_CREAT | O_APPEND, 0644);
    posix_spawn_file_actions_adddup2(&fa, 1, 2);
    pid_t pid = 0;
    const int rc = posix_spawn(&pid, argv[0], &fa, nullptr, argv.data(), envp);
    posix_spawn_file_actions_destroy(&fa);
    close(fds[0]);
    if (rc != 0) {
        close(fds[1]);
        host->release(host->ctx, h);
        return fail("Couldn't start the analysis (gigsteady not found).");
    }

    ProgressPanel* panel = progressOpen("Gig-steady", "Reading frames...");
    bool cancelled = false, pipeOk = true;
    int fetched = 0, failed = 0;
    for (int i = 0; i < n && pipeOk; ++i) {
        if (i > 0) {
            h = nullptr;
            if (!host->fetch(host->ctx, t0 + i, &im, &h)) h = nullptr;
        }
        if (h) {
            if (im.x2 - im.x1 == W && im.y2 - im.y1 == H) {
                toLuma(im, aw, ah, gray.data());
                ++fetched;
            } else {
                ++failed;  // keep the previous frame's picture
            }
            host->release(host->ctx, h);
        } else {
            ++failed;
        }
        pipeOk = writeAll(fds[1], gray.data(), gray.size());
        char text[96];
        std::snprintf(text, sizeof text, "Reading frames: %d of %d", i + 1, n);
        if (!progressStep(panel, 0.3 * (i + 1) / n, text)) {
            cancelled = true;
            break;
        }
    }
    close(fds[1]);
    const double readSecs = secs();
    int status = 0;
    if (cancelled) {
        kill(pid, SIGTERM);
        waitpid(pid, &status, 0);
    } else if (!panel) {
        waitpid(pid, &status, 0);
    } else {
        const double expected = n * kSecsPerFrame;
        while (waitpid(pid, &status, WNOHANG) == 0) {
            const double el = secs() - readSecs;
            char text[96];
            std::snprintf(text, sizeof text, "Analysing camera movement (%.0f s)", el);
            if (!progressStep(panel, 0.3 + 0.7 * std::min(0.97, el / expected), text)) {
                cancelled = true;
                kill(pid, SIGTERM);
                waitpid(pid, &status, 0);
                break;
            }
            usleep(50000);
        }
    }
    progressClose(panel);
    out->fetched = fetched, out->failed = failed, out->cancelled = cancelled;
    if (cancelled) {
        unlink(json.c_str());
        return fail("Cancelled; previous result kept.");
    }
    const std::string result = readFile(json);
    unlink(json.c_str());
    if (!WIFEXITED(status) || WEXITSTATUS(status) != 0 || result.empty() || !pipeOk) {
        char b[200];
        std::snprintf(b, sizeof b, "Analysis failed (exit %d); see ~/Library/Logs/gigsteady-analyse.log",
                      WIFEXITED(status) ? WEXITSTATUS(status) : -1);
        return fail(b);
    }
    const std::string text = "{\"t0\":" + std::to_string(static_cast<long long>(t0)) + ",\"frames\":" +
                             std::to_string(n) + ",\"result\":" + result + "}";
    const auto P = parse(text);
    if (!P || static_cast<int>(P->p.size()) != n) return fail("Analysis result didn't parse.");
    out->text = strdup(text.c_str());
    out->secs = secs();
    std::snprintf(out->status, sizeof out->status, "Analysed frames %lld-%lld (%d, %d unreadable) in %.0f s",
                  static_cast<long long>(t0), static_cast<long long>(t1), n, failed, out->secs);
}

GS_EXPORT void gs_free(char* p) { std::free(p); }
}
