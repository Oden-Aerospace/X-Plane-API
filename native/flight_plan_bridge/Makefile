REPO_ROOT := $(abspath ../..)
SDK_ROOT ?= $(REPO_ROOT)/../.local/xplane-sdk/4.3.0/SDK
BUILD_DIR := $(REPO_ROOT)/build/flight-plan-bridge
PLUGIN := $(BUILD_DIR)/mac.xpl
TEST_BINARY := $(BUILD_DIR)/flight_plan_bridge_test
ARCH ?= $(shell uname -m)
CXX := clang++

SDK_HEADERS := $(SDK_ROOT)/CHeaders/XPLM
XPLM_FRAMEWORKS := $(SDK_ROOT)/Libraries/Mac
SDK_VERSION_FLAGS := -DXPLM420 -DXPLM411 -DXPLM410 -DXPLM400 \
	-DXPLM303 -DXPLM301 -DXPLM300 -DXPLM210 -DXPLM200
SDK_CPPFLAGS := -DAPL=1 -DIBM=0 -DLIN=0 -DXPLM=0 $(SDK_VERSION_FLAGS)
PLUGIN_CXXFLAGS := -std=c++17 -O2 -Wall -Wextra -Werror -fvisibility=hidden
PLUGIN_LDFLAGS := -arch $(ARCH) -bundle -F$(XPLM_FRAMEWORKS) -framework XPLM
TEST_LDFLAGS := -arch $(ARCH)

.PHONY: all test
all: $(PLUGIN)

$(PLUGIN): flight_plan_bridge.cpp
	test -f "$(SDK_HEADERS)/XPLMDefs.h" || { echo "XPLM SDK headers not found at $(SDK_ROOT)" >&2; exit 1; }
	mkdir -p "$(BUILD_DIR)"
	$(CXX) $(SDK_CPPFLAGS) $(PLUGIN_CXXFLAGS) -isystem "$(SDK_HEADERS)" \
		$(PLUGIN_LDFLAGS) -o "$@" "$<"

test: $(TEST_BINARY)
	"$(TEST_BINARY)"

$(TEST_BINARY): test_flight_plan_bridge.cpp flight_plan_bridge.cpp
	test -f "$(SDK_HEADERS)/XPLMDefs.h" || { echo "XPLM SDK headers not found at $(SDK_ROOT)" >&2; exit 1; }
	mkdir -p "$(BUILD_DIR)"
	$(CXX) $(SDK_CPPFLAGS) $(PLUGIN_CXXFLAGS) -isystem "$(SDK_HEADERS)" \
		$(TEST_LDFLAGS) -o "$@" "$<"
