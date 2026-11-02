"""Copy Blackmagic's OFX C++ support library (BSD, from the Resolve install) into build/ and patch RenderArguments to
carry Resolve's kOfxImageEffectPropSrcFrame (-1 when the host doesn't set it). Run by the Makefile."""
import shutil
import sys
from pathlib import Path

sdk, out = Path(sys.argv[1]), Path(sys.argv[2])
if out.exists():
    shutil.rmtree(out)
shutil.copytree(sdk / "Support", out)
h = out / "include" / "ofxsImageEffect.h"
s = h.read_text()
old = "    OfxImageEye eyeToRender;\n  };\n\n  /** @brief POD struct to pass rendering arguments into @ref OFX::ImageEffect::isIdentity */"
assert old in s, "RenderArguments layout changed"
h.write_text(s.replace(old, "    OfxImageEye eyeToRender;\n    int       srcFrame;   // gigsteady patch: Resolve's OfxImageEffectPropSrcFrame, -1 if unset\n  };\n\n  /** @brief POD struct to pass rendering arguments into @ref OFX::ImageEffect::isIdentity */", 1))
c = out / "Library" / "ofxsImageEffect.cpp"
s = c.read_text()
old = "      args.renderQualityDraft = inArgs.propGetInt(kOfxImageEffectPropRenderQualityDraft, false) != 0;\n"
assert old in s, "getRenderActionArguments changed"
c.write_text(s.replace(old, old + "      args.srcFrame = inArgs.propGetInt(\"OfxImageEffectPropSrcFrame\", false);  // gigsteady patch\n"
                       "      if (inArgs.propGetDimension(\"OfxImageEffectPropSrcFrame\", false) == 0) args.srcFrame = -1;\n", 1))
print("support library patched into", out)
