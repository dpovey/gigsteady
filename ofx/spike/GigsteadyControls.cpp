// gigsteady OFX spike: the planned control layout, plus the feasibility tests for a "Stabilise" button that works like
// Resolve's own stabiliser (analyse in place with a progress bar, keep the result in the project, re-run when settings
// change, effect off = original). The picture passes through unchanged except for the optional test pattern.
//
// Stabilise (test): fetches each frame of the clip inside the button press, with a cancellable progress bar, computes a
// stand-in per-frame result, and stores it in a hidden parameter sized like a real 3-minute analysis (4500 frames),
// to learn whether Resolve allows frame fetches outside render, saves a ~150 KB parameter with the project, and which
// frame range the clip covers. Test pattern: white and red squares, to see whether Resolve's grade and zoom are
// applied after this effect. Results go to ~/Library/Logs/gigsteady-ofx-spike.log.
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <memory>
#include <string>

#include "ProgressPanel.h"
#include "ofxsImageEffect.h"
#include "ofxsSupportPrivate.h"

#define kName "Gig-steady"
#define kGrouping "Gig-steady"
#define kIdentifier "com.dpovey.gigsteady.preview"  // the real plugin gets com.dpovey.gigsteady

namespace {

enum Mode { kAuto, kLock, kTrack };
constexpr int kSizeTestFrames = 4500;  // a 3-minute clip at 25 fps

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
        updateEnabled();  // setEnabled is allowed in any action; setValue is not, so the status waits for a change
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
        // copy where the source covers the render window, transparent black elsewhere
        const OfxRectI& w = a.renderWindow;
        const OfxRectI sb = src->getBounds();
        const int x1 = std::max(w.x1, sb.x1), x2 = std::min(w.x2, sb.x2);
        for (int y = w.y1; y < w.y2; ++y) {
            float* d = static_cast<float*>(dst->getPixelAddress(w.x1, y));
            if (!d) continue;
            std::memset(d, 0, sizeof(float) * 4 * (w.x2 - w.x1));
            const float* s = (y >= sb.y1 && y < sb.y2 && x1 < x2)
                                 ? static_cast<const float*>(src->getPixelAddress(x1, y)) : nullptr;
            if (s) std::memcpy(d + 4 * (x1 - w.x1), s, sizeof(float) * 4 * (x2 - x1));
        }
        if (m_Pattern->getValueAtTime(a.time) || patternFlag()) drawPattern(dst.get(), w, a.renderScale);
    }

private:
    // White square at the frame centre and a red one to its right, 1/10 of the width each. After a grade they come
    // out tinted; after a zoom they come out bigger. Coordinates scale with the render scale.
    void drawPattern(OFX::Image* dst, const OfxRectI& w, OfxPointD rs)
    {
        const OfxRectI b = dst->getBounds();
        const int cx = (b.x1 + b.x2) / 2, cy = (b.y1 + b.y2) / 2, h = static_cast<int>(192 * rs.x);
        const int boxes[2][2] = {{cx, cy}, {cx + static_cast<int>(768 * rs.x), cy}};
        const float colour[2][3] = {{1, 1, 1}, {1, 0, 0}};
        for (int k = 0; k < 2; ++k)
            for (int y = std::max(w.y1, boxes[k][1] - h); y < std::min(w.y2, boxes[k][1] + h); ++y)
                for (int x = std::max(w.x1, boxes[k][0] - h); x < std::min(w.x2, boxes[k][0] + h); ++x)
                    if (float* p = static_cast<float*>(dst->getPixelAddress(x, y))) {
                        p[0] = colour[k][0];
                        p[1] = colour[k][1];
                        p[2] = colour[k][2];
                        p[3] = 1;
                    }
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
        char b[64];
        std::snprintf(b, sizeof b, "budget=%.3f", m_Budget->getValue());
        return b;
    }

    // The feasibility test behind the Stabilise button. Logs one JSON line with what it learned.
    void stabilise(double now)
    {
        const OfxRangeD src = m_Src->getFrameRange(), dst = m_Dst->getFrameRange();
        const double dur = getEffectDuration();
        double ir0 = -1, ir1 = -1;
        try {
            ir0 = getPropertySet().propGetDouble(kOfxImageEffectPropFrameRange, 0, false);
            ir1 = getPropertySet().propGetDouble(kOfxImageEffectPropFrameRange, 1, false);
        } catch (...) {
        }
        // which range to analyse: the narrowest one Resolve reports, else 250 frames around the playhead
        double t0 = src.min, t1 = src.max;
        const char* from = "source";
        if (dst.max > dst.min && dst.max - dst.min < t1 - t0) t0 = dst.min, t1 = dst.max, from = "output";
        if (ir1 > ir0 && ir1 - ir0 < t1 - t0) t0 = ir0, t1 = ir1, from = "instance";
        // 751 frames: long enough (~10 s) to see the progress window
        if (t1 - t0 > 1000) t0 = std::max(src.min, now - 375), t1 = std::min(src.max, now + 375), from = "playhead";

        const int n = static_cast<int>(t1 - t0) + 1;
        std::string lines;
        int fetched = 0, failed = 0, threw = 0;
        bool cancelled = false;
        auto start = std::chrono::steady_clock::now();
        // Resolve's progress suite: record whether it exists and accepts a start (it drew nothing in the first test)
        int suite = OFX::Private::gProgressSuiteV2 ? 2 : OFX::Private::gProgressSuiteV1 ? 1 : 0;
        int startStat = -999;
        if (OFX::Private::gProgressSuiteV2)
            startStat = OFX::Private::gProgressSuiteV2->progressStart((void*)getHandle(), "Gig-steady: analysing",
                                                                       "gigsteady.analyse");
        else if (OFX::Private::gProgressSuiteV1)
            startStat = OFX::Private::gProgressSuiteV1->progressStart((void*)getHandle(), "Gig-steady: analysing");
        ProgressPanel* panel = progressOpen("Gig-steady", "Analysing camera movement...");
        const bool native = panel != nullptr;
        for (int i = 0; i < n; ++i) {
            double mean = -1;
            try {
                std::unique_ptr<OFX::Image> im(m_Src->fetchImage(t0 + i));
                if (im) {
                    ++fetched;
                    mean = meanGrid(im.get());
                } else {
                    ++failed;
                }
            } catch (...) {
                ++threw;
            }
            char b[96];
            std::snprintf(b, sizeof b, "%d %.4f %.4f %.5f %.5f\n", static_cast<int>(t0) + i, mean, 0.0, 0.0, 1.0);
            lines += b;
            char text[96];
            std::snprintf(text, sizeof text, "Analysing camera movement: frame %d of %d", i + 1, n);
            bool go = progressStep(panel, static_cast<double>(i + 1) / n, text);
            if (startStat == kOfxStatOK && OFX::Private::gProgressSuiteV2 &&
                OFX::Private::gProgressSuiteV2->progressUpdate((void*)getHandle(), (i + 1.0) / n) == kOfxStatReplyNo)
                go = false;
            if (!go) {
                cancelled = true;
                break;
            }
        }
        progressClose(panel);
        if (startStat == kOfxStatOK && OFX::Private::gProgressSuiteV2)
            OFX::Private::gProgressSuiteV2->progressEnd((void*)getHandle());
        const double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
        // pad to a real 3-minute analysis so the save/reload test uses the real size
        const std::string real = lines;
        while (std::count(lines.begin(), lines.end(), '\n') < kSizeTestFrames && !real.empty()) lines += real;
        if (!cancelled) {
            m_Analysis->setValue(lines);
            m_Key->setValue(key());
        }
        std::string back;
        m_Analysis->getValue(back);

        char b[900];
        std::snprintf(b, sizeof b,
                      "{\"event\":\"stabilise\",\"now\":%.1f,\"src_range\":[%.1f,%.1f],\"dst_range\":[%.1f,%.1f],"
                      "\"effect_duration\":%.1f,\"instance_range\":[%.1f,%.1f],\"analysed\":[%.1f,%.1f],\"from\":\"%s\","
                      "\"frames\":%d,\"fetched\":%d,\"failed\":%d,\"threw\":%d,\"cancelled\":%s,\"secs\":%.2f,"
                      "\"stored_bytes\":%zu,\"read_back_bytes\":%zu,\"progress_suite\":%d,\"progress_start\":%d,"
                      "\"native_panel\":%s}",
                      now, src.min, src.max, dst.min, dst.max, dur, ir0, ir1, t0, t1, from, n, fetched, failed, threw,
                      cancelled ? "true" : "false", secs, cancelled ? 0 : lines.size(), back.size(), suite, startStat,
                      native ? "true" : "false");
        logLine(b);
        char msg[300];
        if (cancelled)
            std::snprintf(msg, sizeof msg, "Cancelled after %d of %d frames; previous result kept.", fetched + failed + threw, n);
        else
            std::snprintf(msg, sizeof msg, "Analysed %d frames (%d read, %d failed) in %.1f s; stored %zu KB.", n, fetched,
                          failed + threw, secs, back.size() / 1024);
        m_Status->setValue(msg);
    }

    static double meanGrid(OFX::Image* im)
    {
        if (im->getPixelDepth() != OFX::eBitDepthFloat || im->getPixelComponents() != OFX::ePixelComponentRGBA) return -1;
        const OfxRectI b = im->getBounds();
        double sum = 0;
        int k = 0;
        for (int j = 0; j < 32; ++j)
            for (int i = 0; i < 32; ++i) {
                int x = b.x1 + (b.x2 - b.x1) * (2 * i + 1) / 64, y = b.y1 + (b.y2 - b.y1) * (2 * j + 1) / 64;
                if (const float* p = static_cast<const float*>(im->getPixelAddress(x, y))) {
                    sum += 0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2];
                    ++k;
                }
            }
        return k ? sum / k : -1;
    }

    // grey out what doesn't apply: crop budget is track-only, zoom needs "Manual zoom", de-blur settings need de-blur
    void updateStatus()
    {
        std::string stored;
        m_Key->getValue(stored);
        if (!stored.empty() && stored != key()) {
            m_Status->setValue("Settings changed: press Stabilise to re-analyse (showing the previous result).");
            return;
        }
        if (!stored.empty()) {
            std::string a;
            m_Analysis->getValue(a);
            m_Status->setValue("Stabilised (" + std::to_string(a.size() / 1024) + " KB stored with the project).");
            return;
        }
        std::string path = getPropertySet().propGetString("OfxImageEffectPropSrcFilePath", false);
        m_Status->setValue(path.empty() ? "Controls preview. No source file (multicam?): would look up by multicam frame."
                                        : "Controls preview: picture passes through. Source: " + path);
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
        d.setPluginDescription("Concert-footage stabiliser (controls preview: the picture passes through unchanged)");
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
        go->setLabels("Stabilise", "Stabilise", "Stabilise");
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
        reload->setLabels("Reload analysis", "Reload analysis", "Reload analysis");
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
