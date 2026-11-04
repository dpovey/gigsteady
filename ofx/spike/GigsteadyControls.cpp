// Gig-steady OFX plugin (spike): the controls, and a Stabilise button that works like Resolve's own stabiliser:
// analyse in place with a progress window, keep the result in the project, re-run when settings change, effect off =
// original. Stabilise hands the frames around the playhead to the engine (Engine.cpp), which runs gigsteady's
// analysis on them; render warps each frame by the stored path. De-blur and the overlay are controls only so far.
// Events go to ~/Library/Logs/gigsteady-ofx-spike.log.
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <memory>
#include <string>

#include <dlfcn.h>
#include <sys/stat.h>
#include <unistd.h>

#include "Engine.h"
#include "ofxsImageEffect.h"

#define kName "Gig-steady"
#define kGrouping "Gig-steady"
#define kIdentifier "com.dpovey.gigsteady.preview"  // the real plugin gets com.dpovey.gigsteady

namespace {

enum Mode { kAuto, kLock, kTrack };
// Resolve sizes a push button to its label; spaces give the side padding its own Stabilize button has
constexpr const char* kStabiliseLabel = "            Stabilise            ";
constexpr const char* kRestabiliseLabel = "         Stabilise again         ";
constexpr const char* kReloadLabel = "        Reload analysis        ";  // a 3-minute clip at 25 fps

std::mutex gLogMutex;

void logLine(const std::string& s)
{
    std::lock_guard<std::mutex> lock(gLogMutex);
    const char* home = std::getenv("HOME");
    std::string path = std::string(home ? home : "/tmp") + "/Library/Logs/gigsteady-ofx-spike.log";
    if (FILE* f = std::fopen(path.c_str(), "a")) {
        std::fprintf(f, "%s\n", s.c_str());
        std::fclose(f);
    }
}
enum DeblurMode { kDeblurAuto, kDeblurOn, kDeblurOff };

// Development engine: if ~/Library/Application Support/Gig-steady/dev/libgigsteady_engine.dylib exists, use it, and
// load it again whenever the file changes (make engine), so pixel and analysis changes need no Resolve restart.
// Each version is copied to a unique temporary name first, because dlopen caches by path; old copies stay loaded,
// since a render on another thread may still be inside one. Otherwise, or if it won't load, the built-in engine.
struct Engine {
    gs_render_fn render;
    gs_analyse_fn analyse;
    gs_free_fn free;
    gs_build_fn build;
};

const Engine kBuiltin{gs_render, gs_analyse, gs_free, gs_build};

const Engine& engine()
{
    static std::mutex m;
    static Engine dev{};
    static bool haveDev = false;
    static long long loadedStamp = -1;
    std::lock_guard<std::mutex> lock(m);
    const char* home = std::getenv("HOME");
    const std::string path =
        std::string(home ? home : "") + "/Library/Application Support/Gig-steady/dev/libgigsteady_engine.dylib";
    struct stat st{};
    if (stat(path.c_str(), &st) != 0) return haveDev ? dev : kBuiltin;  // keep a loaded dev engine if the file is mid-write
    const long long stamp = static_cast<long long>(st.st_mtimespec.tv_sec) * 1000000000LL + st.st_mtimespec.tv_nsec;
    if (stamp == loadedStamp) return haveDev ? dev : kBuiltin;
    loadedStamp = stamp;
    const char* tmp = std::getenv("TMPDIR");
    const std::string copy = std::string(tmp ? tmp : "/tmp/") + "gigsteady_engine_" + std::to_string(stamp) + ".dylib";
    std::string cmd = "/bin/cp -f '" + path + "' '" + copy + "'";
    void* h = (std::system(cmd.c_str()) == 0) ? dlopen(copy.c_str(), RTLD_NOW | RTLD_LOCAL) : nullptr;
    Engine e{};
    if (h) {
        auto api = reinterpret_cast<gs_api_fn>(dlsym(h, "gs_api"));
        e.render = reinterpret_cast<gs_render_fn>(dlsym(h, "gs_render"));
        e.analyse = reinterpret_cast<gs_analyse_fn>(dlsym(h, "gs_analyse"));
        e.free = reinterpret_cast<gs_free_fn>(dlsym(h, "gs_free"));
        e.build = reinterpret_cast<gs_build_fn>(dlsym(h, "gs_build"));
        if (api && api() == GS_ENGINE_API && e.render && e.analyse && e.free && e.build) {
            dev = e;
            haveDev = true;
            logLine(std::string("{\"event\":\"engine_loaded\",\"build\":\"") + e.build() + "\"}");
            return dev;
        }
    }
    logLine(std::string("{\"event\":\"engine_load_failed\",\"error\":\"") + (h ? "api mismatch" : "dlopen") + "\"}");
    return haveDev ? dev : kBuiltin;
}

class ControlsEffect : public OFX::ImageEffect {
public:
    explicit ControlsEffect(OfxImageEffectHandle h) : OFX::ImageEffect(h)
    {
        m_Dst = fetchClip(kOfxImageEffectOutputClipName);
        m_Src = fetchClip(kOfxImageEffectSimpleSourceClipName);
        m_Mode = fetchChoiceParam("mode");
        m_ManualZoom = fetchBooleanParam("manual_zoom");
        m_Zoom = fetchDoubleParam("zoom");
        m_Budget = fetchDoubleParam("crop_budget");
        m_Deblur = fetchChoiceParam("deblur");
        m_Neighbours = fetchIntParam("neighbours");
        m_Guard = fetchDoubleParam("moving_guard");
        m_Denoise = fetchDoubleParam("denoise");
        m_Status = fetchStringParam("status");
        m_Analysis = fetchStringParam("analysis");
        m_Key = fetchStringParam("analysis_key");
        m_Pattern = fetchBooleanParam("test_pattern");
        m_Shot = fetchGroupParam("shot");
        m_Go = fetchPushButtonParam("stabilise");
        // labels and enabled flags are properties, which can be set in any action (values only in a change), so the
        // heading shows the saved state as soon as the clip loads
        updateEnabled();
        updateLabels();
    }

    void changedParam(const OFX::InstanceChangedArgs& args, const std::string& name) override
    {
        if (name == "reload")
            sendMessage(OFX::Message::eMessageMessage, "", "Reload: not built yet (controls preview).");
        if (name == "stabilise") {
            stabilise(args.time);
            return;
        }
        if (name != "status" && name != "analysis" && name != "analysis_key") {
            updateStatus();
            updateEnabled();
        }
    }

    void changedClip(const OFX::InstanceChangedArgs&, const std::string&) override { updateStatus(); }

    void render(const OFX::RenderArguments& a) override
    {
        std::unique_ptr<OFX::Image> dst(m_Dst->fetchImage(a.time));
        std::unique_ptr<OFX::Image> src(m_Src->fetchImage(a.time));
        if (!dst || !src) OFX::throwSuiteStatusException(kOfxStatFailed);
        if (dst->getPixelDepth() != OFX::eBitDepthFloat || dst->getPixelComponents() != OFX::ePixelComponentRGBA ||
            src->getPixelDepth() != OFX::eBitDepthFloat || src->getPixelComponents() != OFX::ePixelComponentRGBA)
            OFX::throwSuiteStatusException(kOfxStatErrUnsupported);
        GsRender r{};
        r.time = a.time;
        r.src = wrap(src.get());
        r.dst = wrap(dst.get());
        r.wx1 = a.renderWindow.x1, r.wy1 = a.renderWindow.y1, r.wx2 = a.renderWindow.x2, r.wy2 = a.renderWindow.y2;
        r.scale = a.renderScale.x;
        r.pattern = m_Pattern->getValueAtTime(a.time) || patternFlag();
        std::string analysis;
        m_Analysis->getValueAtTime(a.time, analysis);
        r.analysis = analysis.c_str();
        char settings[32] = "";
        if (m_ManualZoom->getValueAtTime(a.time))
            std::snprintf(settings, sizeof settings, "zoom=%.3f", m_Zoom->getValueAtTime(a.time));
        r.settings = settings;
        engine().render(&r);
    }

    // the warp reads outside the render window
    void getRegionsOfInterest(const OFX::RegionsOfInterestArguments&, OFX::RegionOfInterestSetter& rois) override
    {
        rois.setRegionOfInterest(*m_Src, m_Src->getRegionOfDefinition(0));
    }

private:
    static GsImage wrap(OFX::Image* im)
    {
        const OfxRectI b = im->getBounds();
        return GsImage{static_cast<float*>(im->getPixelAddress(b.x1, b.y1)), b.x1, b.y1, b.x2, b.y2, im->getRowBytes()};
    }

    // Scripts can't set effect parameters, so the order test turns the pattern on with a flag file instead
    static bool patternFlag()
    {
        const char* home = std::getenv("HOME");
        std::string path = std::string(home ? home : "/tmp") + "/Library/Logs/gigsteady-test-pattern";
        if (FILE* f = std::fopen(path.c_str(), "r")) {
            std::fclose(f);
            return true;
        }
        return false;
    }

    std::string key()
    {
        static const char* modes[] = {"auto", "lock", "track"};
        int mode = 0;
        m_Mode->getValue(mode);
        char b[64];
        std::snprintf(b, sizeof b, "mode=%s budget=%.3f", modes[std::clamp(mode, 0, 2)], m_Budget->getValue());
        return b;
    }

    // Stabilise: the engine picks the frames and analyses them (with its progress window); store the result.
    void stabilise(double now)
    {
        const OfxRangeD src = m_Src->getFrameRange();
        struct Ctx { OFX::Clip* clip; };
        Ctx ctx{m_Src};
        GsHost host{};
        host.ctx = &ctx;
        host.fetch = [](void* c, double t, GsImage* out, void** handle) -> int {
            try {
                OFX::Image* im = static_cast<Ctx*>(c)->clip->fetchImage(t);
                if (!im) return 0;
                if (im->getPixelDepth() != OFX::eBitDepthFloat || im->getPixelComponents() != OFX::ePixelComponentRGBA) {
                    delete im;
                    return 0;
                }
                *out = wrap(im);
                *handle = im;
                return 1;
            } catch (...) {
                return 0;
            }
        };
        host.release = [](void*, void* handle) { delete static_cast<OFX::Image*>(handle); };
        host.log = [](const char* line) { logLine(line); };
        const std::string settings = key();
        GsAnalyse req{now, src.min, src.max, settings.c_str()};
        GsAnalyseResult res{};
        const Engine& e = engine();
        e.analyse(&host, &req, &res);
        if (res.text) {
            m_Analysis->setValue(res.text);
            m_Key->setValue(settings);
            e.free(res.text);
        }
        std::string back;
        m_Analysis->getValue(back);
        char b[900];
        std::snprintf(b, sizeof b,
                      "{\"event\":\"stabilise\",\"engine\":\"%s\",\"settings\":\"%s\",\"now\":%.1f,"
                      "\"analysed\":[%.1f,%.1f],\"frames\":%d,\"fetched\":%d,\"failed\":%d,\"cancelled\":%s,"
                      "\"secs\":%.2f,\"stored_bytes\":%zu,\"status\":\"%s\"}",
                      e.build(), settings.c_str(), now, res.t0, res.t1, res.frames, res.fetched, res.failed,
                      res.cancelled ? "true" : "false", res.secs, back.size(), res.status);
        logLine(b);
        if (!res.text && !res.cancelled) sendMessage(OFX::Message::eMessageError, "", res.status);
        // the box and heading only ever describe the saved result: Resolve saves the box's text with the project and a
        // plugin can't rewrite it on load, so a transient "cancelled" would stick
        updateStatus(std::string(" [") + e.build() + "]");
    }

    enum class State { kNone, kDone, kDirty };
    State state()
    {
        std::string stored;
        m_Key->getValue(stored);
        return stored.empty() ? State::kNone : stored != key() ? State::kDirty : State::kDone;
    }

    // plain-text status: the group heading and the button label (candidates for replacing the Status box)
    void updateLabels()
    {
        const State st = state();
        const char* heading = st == State::kNone  ? "Stabilise: not analysed"
                              : st == State::kDone ? "Stabilise: done"
                                                   : "Stabilise: settings changed";
        m_Shot->setLabels(heading, heading, heading);
        const char* button = st == State::kDirty ? kRestabiliseLabel : kStabiliseLabel;
        m_Go->setLabels(button, button, button);
    }

    // grey out what doesn't apply: crop budget is track-only, zoom needs "Manual zoom", de-blur settings need de-blur
    void updateStatus(const std::string& suffix = "")
    {
        updateLabels();
        const State st = state();
        if (st == State::kDirty) {
            m_Status->setValue("Settings changed: press Stabilise to re-analyse (showing the previous result)." + suffix);
            return;
        }
        if (st == State::kDone) {
            std::string a;
            m_Analysis->getValue(a);
            m_Status->setValue(summary(a) + suffix);
            return;
        }
        m_Status->setValue("Not stabilised: press Stabilise to analyse the frames around the playhead." + suffix);
    }

    // "Stabilised: lock, zoom 1.020, frames 61837-62337" from the stored result (Engine.cpp writes it)
    static std::string summary(const std::string& a)
    {
        auto field = [&](const char* key) -> std::string {
            const std::string k = std::string("\"") + key + "\":";
            const size_t p = a.find(k);
            if (p == std::string::npos) return "?";
            size_t b = p + k.size(), e = a.find_first_of(",}", b);
            std::string v = a.substr(b, e - b);
            if (!v.empty() && v.front() == '"') v = v.substr(1, v.size() - 2);
            return v;
        };
        const long long t0 = std::atoll(field("t0").c_str()), n = std::atoll(field("frames").c_str());
        return "Stabilised: " + field("mode") + ", zoom " + field("zoom") + ", frames " + std::to_string(t0) + "-" +
               std::to_string(t0 + n - 1) + " (others pass through).";
    }

    void updateEnabled()
    {
        int mode = 0, deblur = 0;
        m_Mode->getValue(mode);
        m_Deblur->getValue(deblur);
        m_Zoom->setEnabled(m_ManualZoom->getValue());
        m_Budget->setEnabled(mode != kLock);
        // Auto de-blur is on in Lock, off in Track; in Auto mode the analysis decides, so leave the settings editable
        const bool fuse = deblur == kDeblurOn || (deblur == kDeblurAuto && mode != kTrack);
        m_Neighbours->setEnabled(fuse);
        m_Guard->setEnabled(fuse);
        m_Denoise->setEnabled(fuse);
    }

    OFX::Clip *m_Dst, *m_Src;
    OFX::ChoiceParam *m_Mode, *m_Deblur;
    OFX::BooleanParam* m_ManualZoom;
    OFX::DoubleParam *m_Zoom, *m_Budget, *m_Guard, *m_Denoise;
    OFX::IntParam* m_Neighbours;
    OFX::StringParam *m_Status, *m_Analysis, *m_Key;
    OFX::BooleanParam* m_Pattern;
    OFX::GroupParam* m_Shot;
    OFX::PushButtonParam* m_Go;
};

OFX::GroupParamDescriptor* group(OFX::ImageEffectDescriptor& d, OFX::PageParamDescriptor* page, const char* name,
                                 const char* label)
{
    OFX::GroupParamDescriptor* g = d.defineGroupParam(name);
    g->setLabels(label, label, label);
    g->setOpen(true);
    page->addChild(*g);
    return g;
}

OFX::DoubleParamDescriptor* number(OFX::ImageEffectDescriptor& d, OFX::PageParamDescriptor* page,
                                   OFX::GroupParamDescriptor* g, const char* name, const char* label,
                                   const char* hint, double def, double lo, double hi)
{
    OFX::DoubleParamDescriptor* p = d.defineDoubleParam(name);
    p->setLabels(label, label, label);
    p->setHint(hint);
    p->setDefault(def);
    p->setRange(lo, hi);
    p->setDisplayRange(lo, hi);
    p->setAnimates(false);
    p->setParent(*g);
    page->addChild(*p);
    return p;
}

class ControlsFactory : public OFX::PluginFactoryHelper<ControlsFactory> {
public:
    ControlsFactory() : OFX::PluginFactoryHelper<ControlsFactory>(kIdentifier, 0, 1) {}
    void describe(OFX::ImageEffectDescriptor& d) override
    {
        d.setLabels(kName, kName, kName);
        d.setPluginGrouping(kGrouping);
        d.setPluginDescription("Concert-footage stabiliser");
        d.addSupportedContext(OFX::eContextFilter);
        d.addSupportedBitDepth(OFX::eBitDepthFloat);
        d.setSingleInstance(false);
        d.setHostFrameThreading(false);
        d.setSupportsMultiResolution(false);
        d.setSupportsTiles(false);
        d.setTemporalClipAccess(true);
        d.setRenderThreadSafety(OFX::eRenderFullySafe);
    }

    void describeInContext(OFX::ImageEffectDescriptor& d, OFX::ContextEnum) override
    {
        OFX::ClipDescriptor* src = d.defineClip(kOfxImageEffectSimpleSourceClipName);
        src->addSupportedComponent(OFX::ePixelComponentRGBA);
        src->setTemporalClipAccess(true);
        src->setSupportsTiles(false);
        OFX::ClipDescriptor* dst = d.defineClip(kOfxImageEffectOutputClipName);
        dst->addSupportedComponent(OFX::ePixelComponentRGBA);
        dst->setSupportsTiles(false);
        OFX::PageParamDescriptor* page = d.definePageParam("Controls");

        OFX::StringParamDescriptor* status = d.defineStringParam("status");
        status->setLabels("Status", "Status", "Status");
        status->setStringType(OFX::eStringTypeLabel);
        status->setDefault("Controls preview: the picture passes through unchanged.");
        status->setIsPersistant(false);
        status->setAnimates(false);
        status->setEvaluateOnChange(false);
        page->addChild(*status);

        OFX::GroupParamDescriptor* shot = group(d, page, "shot", "Stabilise");
        OFX::PushButtonParamDescriptor* go = d.definePushButtonParam("stabilise");
        go->setLabels(kStabiliseLabel, kStabiliseLabel, kStabiliseLabel);
        go->setHint("Analyse the clip's camera movement (feasibility test: stand-in result)");
        go->setParent(*shot);
        page->addChild(*go);
        for (const char* name : {"analysis", "analysis_key"}) {
            OFX::StringParamDescriptor* h = d.defineStringParam(name);
            h->setIsSecret(true);  // hidden; saved with the project
            h->setAnimates(false);
            h->setEvaluateOnChange(true);
            h->setStringType(OFX::eStringTypeMultiLine);
            page->addChild(*h);
        }
        OFX::ChoiceParamDescriptor* mode = d.defineChoiceParam("mode");
        mode->setLabels("Mode", "Mode", "Mode");
        mode->setHint("Auto: the camera's preset from the analysis config, else what the analysis found (lock if the "
                      "camera stayed put). Lock: hold the frame still. Track: smooth the camera's own moves.");
        mode->appendOption("Auto");
        mode->appendOption("Lock (static camera)");
        mode->appendOption("Track (smooth moves)");
        mode->setDefault(kAuto);
        mode->setAnimates(false);
        mode->setParent(*shot);
        page->addChild(*mode);
        number(d, page, shot, "crop_budget", "Crop budget", "Track: largest zoom-in allowed to hide the edges",
               1.10, 1.0, 1.5);
        OFX::BooleanParamDescriptor* manual = d.defineBooleanParam("manual_zoom");
        manual->setLabels("Manual zoom", "Manual zoom", "Manual zoom");
        manual->setHint("Off: the smallest zoom that keeps the edges hidden for the whole shot");
        manual->setDefault(false);
        manual->setAnimates(false);
        manual->setParent(*shot);
        page->addChild(*manual);
        number(d, page, shot, "zoom", "Zoom", "Fixed zoom for the whole shot", 1.05, 1.0, 1.5);

        OFX::GroupParamDescriptor* fuse = group(d, page, "deblur_group", "De-blur");
        OFX::ChoiceParamDescriptor* deblur = d.defineChoiceParam("deblur");
        deblur->setLabels("De-blur", "De-blur", "De-blur");
        deblur->setHint("Repairs vibration blur from sharper neighbouring frames. Auto: on in Lock, off in Track.");
        deblur->appendOption("Auto");
        deblur->appendOption("On");
        deblur->appendOption("Off");
        deblur->setDefault(kDeblurAuto);
        deblur->setAnimates(false);
        deblur->setParent(*fuse);
        page->addChild(*deblur);
        OFX::IntParamDescriptor* nb = d.defineIntParam("neighbours");
        nb->setLabels("Neighbours", "Neighbours", "Neighbours");
        nb->setHint("Frames used each side. More repairs more blur and costs more render time.");
        nb->setDefault(4);
        nb->setRange(1, 4);
        nb->setDisplayRange(1, 4);
        nb->setAnimates(false);
        nb->setParent(*fuse);
        page->addChild(*nb);
        number(d, page, fuse, "moving_guard", "Moving-subject guard",
               "Higher keeps more repair, lower avoids doubled edges on moving people (haze). 0 = off.", 0.4, 0.0, 1.0);
        number(d, page, fuse, "denoise", "Denoise", "0 = de-blur only; up to 1 = also average out noise", 0.0, 0.0,
               1.0);

        OFX::GroupParamDescriptor* view = group(d, page, "view", "View");
        OFX::BooleanParamDescriptor* overlay = d.defineBooleanParam("overlay");
        overlay->setLabels("Show overlay", "Show overlay", "Show overlay");
        overlay->setHint("Draw the crop box, mode and analysis version on the picture");
        overlay->setDefault(false);
        overlay->setAnimates(false);
        overlay->setParent(*view);
        page->addChild(*overlay);
        OFX::BooleanParamDescriptor* pattern = d.defineBooleanParam("test_pattern");
        pattern->setLabels("Test pattern", "Test pattern", "Test pattern");
        pattern->setHint("White and red squares: shows whether the grade and zoom are applied after this effect");
        pattern->setDefault(false);
        pattern->setAnimates(false);
        pattern->setParent(*view);
        page->addChild(*pattern);
        OFX::PushButtonParamDescriptor* reload = d.definePushButtonParam("reload");
        reload->setLabels(kReloadLabel, kReloadLabel, kReloadLabel);
        reload->setHint("Re-read the shot's analysis after re-running gigsteady analyse");
        reload->setParent(*view);
        page->addChild(*reload);
    }

    OFX::ImageEffect* createInstance(OfxImageEffectHandle h, OFX::ContextEnum) override
    {
        return new ControlsEffect(h);
    }
};

}  // namespace

OFX::PluginFactory* gigsteadyControlsFactory()
{
    static ControlsFactory f;
    return &f;
}
