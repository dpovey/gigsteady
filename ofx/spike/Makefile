# Builds the gigsteady OFX probe against Resolve's bundled OFX SDK (headers and BSD C++ support library are taken from
# the Resolve install, not vendored). `make` builds; `sudo make install` copies the bundle to /Library/OFX/Plugins.
SDK ?= /Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/OpenFX
NAME = GigsteadyProbe
BUILD = build
SUP = $(BUILD)/support
CXXFLAGS = -std=c++17 -O2 -fvisibility=hidden -arch arm64 -I"$(SDK)/OpenFX-1.4/include" -I$(SUP)/include
LDFLAGS = -bundle -fvisibility=hidden -arch arm64 -framework AppKit
SUPPORT_SRC = ofxsCore ofxsImageEffect ofxsInteract ofxsLog ofxsMultiThread ofxsParams ofxsProperty ofxsPropertyValidation
OBJS = $(BUILD)/$(NAME).o $(BUILD)/GigsteadyControls.o $(BUILD)/Engine.o $(BUILD)/ProgressPanel.o $(addprefix $(BUILD)/,$(addsuffix .o,$(SUPPORT_SRC)))
BUNDLE = $(BUILD)/$(NAME).ofx.bundle
# the analysis the Stabilise button runs: this repo's own gigsteady (uv sync makes it)
TOOL = $(abspath ../../.venv/bin/gigsteady)

all: $(BUNDLE)

$(SUP)/.patched: support.patch.py
	mkdir -p $(BUILD)
	python3 support.patch.py "$(SDK)" $(SUP)
	touch $@

$(BUILD)/$(NAME).o: $(NAME).cpp $(SUP)/.patched
	$(CXX) $(CXXFLAGS) -c $< -o $@

$(BUILD)/ProgressPanel.o: ProgressPanel.mm ProgressPanel.h
	mkdir -p $(BUILD)
	$(CXX) -std=c++17 -O2 -fobjc-arc -fvisibility=hidden -arch arm64 -c $< -o $@

$(BUILD)/Engine.o: Engine.cpp Engine.h ProgressPanel.h
	mkdir -p $(BUILD)
	$(CXX) -std=c++17 -O2 -fvisibility=hidden -arch arm64 -DGS_TOOL='"$(TOOL)"' -c $< -o $@

# Development engine: no sudo, no Resolve restart; the installed plugin reloads it when it changes.
DEV_DIR = $(HOME)/Library/Application Support/Gig-steady/dev
DEV_ENGINE = $(DEV_DIR)/libgigsteady_engine.dylib
STAMP := $(shell date +%s)
engine: Engine.cpp Engine.h ProgressPanel.mm ProgressPanel.h
	mkdir -p "$(DEV_DIR)" $(BUILD)
	$(CXX) -std=c++17 -O2 -fvisibility=hidden -arch arm64 -DGS_ENGINE_DYLIB -DGS_TOOL='"$(TOOL)"' -c Engine.cpp -o $(BUILD)/dev_Engine.o
	$(CXX) -std=c++17 -O2 -fobjc-arc -fvisibility=hidden -arch arm64 -DGS_CANCEL_CLASS=GSCancel_$(STAMP) -c ProgressPanel.mm -o $(BUILD)/dev_ProgressPanel.o
	$(CXX) -dynamiclib -arch arm64 -framework AppKit $(BUILD)/dev_Engine.o $(BUILD)/dev_ProgressPanel.o -o $(BUILD)/libgigsteady_engine.dylib
	codesign --force -s - $(BUILD)/libgigsteady_engine.dylib
	mv -f $(BUILD)/libgigsteady_engine.dylib "$(DEV_ENGINE)"

engine-off:
	rm -f "$(DEV_ENGINE)"

$(BUILD)/GigsteadyControls.o: GigsteadyControls.cpp Engine.h $(SUP)/.patched
	$(CXX) $(CXXFLAGS) -c $< -o $@

$(BUILD)/%.o: $(SUP)/.patched
	$(CXX) $(CXXFLAGS) -c $(SUP)/Library/$*.cpp -o $@

$(BUNDLE): $(OBJS)
	mkdir -p $(BUNDLE)/Contents/MacOS
	$(CXX) $(OBJS) -o $(BUNDLE)/Contents/MacOS/$(NAME).ofx $(LDFLAGS)
	codesign --force -s - $(BUNDLE)/Contents/MacOS/$(NAME).ofx

# load check without Resolve: dlopen the bundle, print both plugin identities, fail unless there are 2
check: $(BUNDLE)
	$(CXX) -std=c++17 -arch arm64 -I"$(SDK)/OpenFX-1.4/include" hostcheck.cpp -o $(BUILD)/hostcheck
	$(BUILD)/hostcheck $(BUNDLE)/Contents/MacOS/$(NAME).ofx 2

# engine test without Resolve (see enginetest.cpp, enginetest.py)
enginetest: $(BUILD)/Engine.o $(BUILD)/ProgressPanel.o enginetest.cpp
	$(CXX) -std=c++17 -O2 -arch arm64 enginetest.cpp $(BUILD)/Engine.o $(BUILD)/ProgressPanel.o -framework AppKit -o $(BUILD)/enginetest

# install only copies (run `make` first as yourself): building under sudo would leave root-owned build files
install:
	test -f $(BUNDLE)/Contents/MacOS/$(NAME).ofx || { echo "run make first (not as root)"; exit 1; }
	mkdir -p /Library/OFX/Plugins
	rm -rf /Library/OFX/Plugins/$(NAME).ofx.bundle
	cp -R $(BUNDLE) /Library/OFX/Plugins/

uninstall:
	rm -rf /Library/OFX/Plugins/$(NAME).ofx.bundle

clean:
	rm -rf $(BUILD)

.PHONY: all check enginetest engine engine-off install uninstall clean
