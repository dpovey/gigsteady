// gigsteady OFX spike: the planned control layout, so it can be tried in Resolve's Inspector before any of it works.
// The picture passes through unchanged. Mode, framing and de-blur choices will select from what the CLI analysis wrote
// to the shot's sidecar (it stores both the lock and the track path), so switching mode needs no re-analysis.
#include <algorithm>
#include <cstring>
#include <memory>
#include <string>

#include "ofxsImageEffect.h"

#define kName "Gig-steady"
#define kGrouping "Gig-steady"
#define kIdentifier "com.dpovey.gigsteady"

namespace {

enum Mode { kAuto, kLock, kTrack };
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
        std::string path = getPropertySet().propGetString("OfxImageEffectPropSrcFilePath", false);
        m_Status->setValue(path.empty() ? "Controls preview. No source file (multicam?): would look up by multicam frame."
                                        : "Controls preview: picture passes through. Source: " + path);
        updateEnabled();
    }

    void changedParam(const OFX::InstanceChangedArgs&, const std::string& name) override
    {
        if (name == "reload")
            sendMessage(OFX::Message::eMessageMessage, "", "Reload: not built yet (controls preview).");
        updateEnabled();
    }

    void render(const OFX::RenderArguments& a) override
    {
        std::unique_ptr<OFX::Image> dst(m_Dst->fetchImage(a.time));
        std::unique_ptr<OFX::Image> src(m_Src->fetchImage(a.time));
        if (!dst || !src) OFX::throwSuiteStatusException(kOfxStatFailed);
        if (dst->getPixelDepth() != OFX::eBitDepthFloat || dst->getPixelComponents() != OFX::ePixelComponentRGBA ||
            src->getPixelDepth() != OFX::eBitDepthFloat || src->getPixelComponents() != OFX::ePixelComponentRGBA)
            OFX::throwSuiteStatusException(kOfxStatErrUnsupported);
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
    // grey out what doesn't apply: crop budget is track-only, zoom needs "Manual zoom", de-blur settings need de-blur
    void updateEnabled()
    {
        int mode = 0, deblur = 0;
        m_Mode->getValue(mode);
        m_Deblur->getValue(deblur);
        m_Zoom->setEnabled(m_ManualZoom->getValue());
        m_Budget->setEnabled(mode != kLock);
        const bool fuse = deblur != kDeblurOff;
        m_Neighbours->setEnabled(fuse);
        m_Guard->setEnabled(fuse);
        m_Denoise->setEnabled(fuse);
    }

    OFX::Clip *m_Dst, *m_Src;
    OFX::ChoiceParam *m_Mode, *m_Deblur;
    OFX::BooleanParam* m_ManualZoom;
    OFX::DoubleParam *m_Zoom, *m_Budget, *m_Guard, *m_Denoise;
    OFX::IntParam* m_Neighbours;
    OFX::StringParam* m_Status;
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
        status->setAnimates(false);
        status->setEvaluateOnChange(false);
        page->addChild(*status);

        OFX::GroupParamDescriptor* shot = group(d, page, "shot", "Stabilise");
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
