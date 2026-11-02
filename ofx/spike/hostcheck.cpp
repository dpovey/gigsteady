// Minimal host-side load check: dlopen the bundle, call the two OFX entry points, print the plugin identity.
#include <cstdio>
#include <dlfcn.h>
#include "ofxCore.h"
int main(int argc, char** argv)
{
    void* h = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!h) { std::printf("dlopen failed: %s\n", dlerror()); return 1; }
    auto n = (int (*)())dlsym(h, "OfxGetNumberOfPlugins");
    auto g = (OfxPlugin* (*)(int))dlsym(h, "OfxGetPlugin");
    if (!n || !g) { std::printf("entry points missing\n"); return 1; }
    int k = n();
    for (int i = 0; i < k; ++i) {
        OfxPlugin* p = g(i);
        std::printf("plugin %d: %s %s v%u.%u\n", i, p->pluginApi, p->pluginIdentifier, p->pluginVersionMajor, p->pluginVersionMinor);
    }
    return k == 1 ? 0 : 1;
}
