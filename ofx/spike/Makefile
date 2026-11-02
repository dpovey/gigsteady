# Builds the gigsteady OFX probe against Resolve's bundled OFX SDK (headers and BSD C++ support library are taken from
# the Resolve install, not vendored). `make` builds; `sudo make install` copies the bundle to /Library/OFX/Plugins.
SDK ?= /Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/OpenFX
NAME = GigsteadyProbe
BUILD = build
SUP = $(BUILD)/support
CXXFLAGS = -std=c++17 -O2 -fvisibility=hidden -arch arm64 -I"$(SDK)/OpenFX-1.4/include" -I$(SUP)/include
LDFLAGS = -bundle -fvisibility=hidden -arch arm64
SUPPORT_SRC = ofxsCore ofxsImageEffect ofxsInteract ofxsLog ofxsMultiThread ofxsParams ofxsProperty ofxsPropertyValidation
OBJS = $(BUILD)/$(NAME).o $(addprefix $(BUILD)/,$(addsuffix .o,$(SUPPORT_SRC)))
BUNDLE = $(BUILD)/$(NAME).ofx.bundle

all: $(BUNDLE)

$(SUP)/.patched: support.patch.py
	mkdir -p $(BUILD)
	python3 support.patch.py "$(SDK)" $(SUP)
	touch $@

$(BUILD)/$(NAME).o: $(NAME).cpp $(SUP)/.patched
	$(CXX) $(CXXFLAGS) -c $< -o $@

$(BUILD)/%.o: $(SUP)/.patched
	$(CXX) $(CXXFLAGS) -c $(SUP)/Library/$*.cpp -o $@

$(BUNDLE): $(OBJS)
	mkdir -p $(BUNDLE)/Contents/MacOS
	$(CXX) $(OBJS) -o $(BUNDLE)/Contents/MacOS/$(NAME).ofx $(LDFLAGS)
	codesign --force -s - $(BUNDLE)/Contents/MacOS/$(NAME).ofx

install: $(BUNDLE)
	mkdir -p /Library/OFX/Plugins
	rm -rf /Library/OFX/Plugins/$(NAME).ofx.bundle
	cp -R $(BUNDLE) /Library/OFX/Plugins/

uninstall:
	rm -rf /Library/OFX/Plugins/$(NAME).ofx.bundle

clean:
	rm -rf $(BUILD)

.PHONY: all install uninstall clean
