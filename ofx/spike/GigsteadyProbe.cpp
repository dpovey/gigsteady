// gigsteady OFX spike: a pass-through effect that logs what Resolve tells a plugin, to answer the questions in
// docs/OFX-EXPLORATION.md before building the real renderer:
//   - is the source file path (OfxImageEffectPropSrcFilePath) and source frame (OfxImageEffectPropSrcFrame) set,
//     on plain clips, trimmed clips, speed changes, multicam clips, compound clips, and in Deliver renders?
//   - what time does render() get, and the clip's frame range (mapped and unmapped)?
//   - can neighbouring frames (t-r .. t+r) be fetched, at the clip edges too, and how long does each fetch take?
// CPU only, float RGBA. One JSON line per render call, appended to ~/Library/Logs/gigsteady-ofx-spike.log.
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>

#include "ofxsImageEffect.h"
#include "ofxsMultiThread.h"

#define kPluginName "gigsteady probe"
#define kPluginGrouping "gigsteady"
#define kPluginIdentifier "com.dpovey.gigsteady.probe"

static std::mutex gLogMutex;

static void logLine(const std::string& s)
{
    std::lock_guard<std::mutex> lock(gLogMutex);
    const char* home = std::getenv("HOME");
    std::string path = std::string(home ? home : "/tmp") + "/Library/Logs/gigsteady-ofx-spike.log";
    if (FILE* f = std::fopen(path.c_str(), "a")) {
        std::fprintf(f, "%s\n", s.c_str());
        std::fclose(f);
    }
}

static std::string esc(const std::string& s)
{
    std::string o;
    for (char c : s) {
        if (c == '"' || c == '\\') {
            o += '\\';
            o += c;
        } else if (static_cast<unsigned char>(c) < 0x20) {
            char b[8];
            std::snprintf(b, sizeof b, "\\u%04x", c);
            o += b;
        } else {
            o += c;
        }
    }
    return o;
}

// Sum of a 16x16 grid of pixels: tells whether two fetches returned the same frame (Resolve may clamp at edges).
static double sig(OFX::Image* im)
{
    if (!im || im->getPixelDepth() != OFX::eBitDepthFloat || im->getPixelComponents() != OFX::ePixelComponentRGBA)
        return -1;
    const OfxRectI b = im->getBounds();
    double sum = 0;
    for (int j = 0; j < 16; ++j)
        for (int i = 0; i < 16; ++i) {
            int x = b.x1 + (b.x2 - b.x1) * (2 * i + 1) / 32, y = b.y1 + (b.y2 - b.y1) * (2 * j + 1) / 32;
            if (const float* p = static_cast<const float*>(im->getPixelAddress(x, y))) sum += p[0] + p[1] + p[2];
        }
    return sum;
}

class ProbeEffect : public OFX::ImageEffect {
public:
    explicit ProbeEffect(OfxImageEffectHandle h) : OFX::ImageEffect(h)
    {
        m_Dst = fetchClip(kOfxImageEffectOutputClipName);
        m_Src = fetchClip(kOfxImageEffectSimpleSourceClipName);
        m_Radius = fetchIntParam("radius");
        logLine("{\"event\":\"instance\",\"src_path\":\"" + esc(srcPath()) + "\"}");
    }

    std::string srcPath()
    {
        return getPropertySet().propGetString("OfxImageEffectPropSrcFilePath", false);
    }

    void render(const OFX::RenderArguments& a) override
    {
        const double t = a.time;
        const int r = m_Radius->getValueAtTime(t);
        OfxRangeD fr = m_Src->getFrameRange(), ur = m_Src->getUnmappedFrameRange();
        std::unique_ptr<OFX::Image> dst(m_Dst->fetchImage(t));
        auto t0 = std::chrono::steady_clock::now();
        std::unique_ptr<OFX::Image> src(m_Src->fetchImage(t));
        double ms0 = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();

        std::string fetches;
        for (int d = -r; d <= r; ++d) {
            if (d == 0) continue;
            auto s = std::chrono::steady_clock::now();
            std::unique_ptr<OFX::Image> n;
            const char* err = "";
            try {
                n.reset(m_Src->fetchImage(t + d));
            } catch (...) {  // the support library throws on any status but kOfxStatFailed; log it, don't abort
                err = "threw";
            }
            double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - s).count();
            char b[160];
            std::snprintf(b, sizeof b, "%s{\"d\":%d,\"ok\":%s,\"err\":\"%s\",\"ms\":%.2f,\"sig\":%.4f}",
                          fetches.empty() ? "" : ",", d, n ? "true" : "false", err, ms, sig(n.get()));
            fetches += b;
        }

        char head[640];
        std::snprintf(head, sizeof head,
                      "{\"event\":\"render\",\"time\":%.3f,\"src_frame\":%d,\"range\":[%.1f,%.1f],\"unmapped\":[%.1f,%.1f],"
                      "\"fps\":%.3f,\"scale\":[%.3f,%.3f],\"window\":[%d,%d,%d,%d],\"draft\":%s,\"interactive\":%s,"
                      "\"sequential\":%s,\"metal\":%s,\"src_ms\":%.2f,\"src_sig\":%.4f,",
                      t, a.srcFrame, fr.min, fr.max, ur.min, ur.max, m_Src->getFrameRate(), a.renderScale.x,
                      a.renderScale.y, a.renderWindow.x1, a.renderWindow.y1, a.renderWindow.x2, a.renderWindow.y2,
                      a.renderQualityDraft ? "true" : "false", a.interactiveRenderStatus ? "true" : "false",
                      a.sequentialRenderStatus ? "true" : "false", a.isEnabledMetalRender ? "true" : "false", ms0,
                      sig(src.get()));
        logLine(std::string(head) + "\"src_path\":\"" + esc(srcPath()) + "\",\"fetch\":[" + fetches + "]}");

        if (!dst || !src) OFX::throwSuiteStatusException(kOfxStatFailed);
        if (dst->getPixelDepth() != OFX::eBitDepthFloat || dst->getPixelComponents() != OFX::ePixelComponentRGBA ||
            src->getPixelDepth() != OFX::eBitDepthFloat || src->getPixelComponents() != OFX::ePixelComponentRGBA)
            OFX::throwSuiteStatusException(kOfxStatErrUnsupported);
        // copy the part of the render window the source covers; the source can be smaller than the output
        const OfxRectI& w = a.renderWindow;
        const OfxRectI sb = src->getBounds();
        const int x1 = std::max(w.x1, sb.x1), x2 = std::min(w.x2, sb.x2);
        for (int y = std::max(w.y1, sb.y1); y < std::min(w.y2, sb.y2) && x1 < x2; ++y) {
            float* d = static_cast<float*>(dst->getPixelAddress(x1, y));
            const float* s = static_cast<const float*>(src->getPixelAddress(x1, y));
            if (d && s) std::memcpy(d, s, sizeof(float) * 4 * (x2 - x1));
        }
    }

private:
    OFX::Clip* m_Dst;
    OFX::Clip* m_Src;
    OFX::IntParam* m_Radius;
};

class ProbeFactory : public OFX::PluginFactoryHelper<ProbeFactory> {
public:
    ProbeFactory() : OFX::PluginFactoryHelper<ProbeFactory>(kPluginIdentifier, 0, 1) {}
    void describe(OFX::ImageEffectDescriptor& d) override
    {
        d.setLabels(kPluginName, kPluginName, kPluginName);
        d.setPluginGrouping(kPluginGrouping);
        d.setPluginDescription("Pass-through probe: logs source path/frame and neighbour-fetch cost (gigsteady spike)");
        d.addSupportedContext(OFX::eContextFilter);
        d.addSupportedBitDepth(OFX::eBitDepthFloat);
        d.setSingleInstance(false);
        d.setHostFrameThreading(false);
        d.setSupportsMultiResolution(false);
        d.setSupportsTiles(false);
        d.setTemporalClipAccess(true);
        d.setRenderTwiceAlways(false);
        d.setSupportsMultipleClipPARs(false);
        d.setRenderThreadSafety(OFX::eRenderFullySafe);
    }
    void describeInContext(OFX::ImageEffectDescriptor& d, OFX::ContextEnum) override
    {
        OFX::ClipDescriptor* src = d.defineClip(kOfxImageEffectSimpleSourceClipName);
        src->addSupportedComponent(OFX::ePixelComponentRGBA);
        src->setTemporalClipAccess(true);
        src->setSupportsTiles(false);
        src->setIsMask(false);
        OFX::ClipDescriptor* dst = d.defineClip(kOfxImageEffectOutputClipName);
        dst->addSupportedComponent(OFX::ePixelComponentRGBA);
        dst->setSupportsTiles(false);
        OFX::PageParamDescriptor* page = d.definePageParam("Controls");
        OFX::IntParamDescriptor* r = d.defineIntParam("radius");
        r->setLabels("Neighbours", "Neighbours", "Neighbours");
        r->setHint("Frames fetched each side (logged, not used)");
        r->setDefault(2);
        r->setRange(0, 8);
        r->setDisplayRange(0, 8);
        page->addChild(*r);
    }
    OFX::ImageEffect* createInstance(OfxImageEffectHandle h, OFX::ContextEnum) override { return new ProbeEffect(h); }
};

void OFX::Plugin::getPluginIDs(PluginFactoryArray& ids)
{
    static ProbeFactory p;
    ids.push_back(&p);
}
