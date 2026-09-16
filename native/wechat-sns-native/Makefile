CXX ?= clang++
BUILD_DIR ?= build
MIN_MACOS ?= 15.0
INCLUDES := -Iinclude -Isrc
COMMON_FLAGS := -std=c++17 -O2 -Wall -Wextra -Werror -fvisibility=hidden $(INCLUDES)
COMMON_SOURCES := src/core.cpp src/adapter_registry.cpp src/pattern_scanner.cpp
ADAPTERS := adapters/wechat_sns_adapters.jsonl

UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S),Darwin)
LIBRARY := $(BUILD_DIR)/libwechat_sns_native.dylib
PROBE := $(BUILD_DIR)/wechat-sns-probe
PLATFORM_SOURCES := src/platform_macos.mm src/bridge_unix.cpp
PLATFORM_FLAGS := -mmacosx-version-min=$(MIN_MACOS)
PLATFORM_LIBS := -framework Foundation
else
$(error wechat-sns-native currently builds with this Makefile on macOS only)
endif

.PHONY: all clean probe install-python

all: $(LIBRARY) $(PROBE) $(BUILD_DIR)/wechat_sns_adapters.jsonl

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(LIBRARY): $(COMMON_SOURCES) $(PLATFORM_SOURCES) include/wechat_sns_native.h include/wechat_sns_bridge_protocol.h src/internal.hpp | $(BUILD_DIR)
	$(CXX) $(COMMON_FLAGS) $(PLATFORM_FLAGS) -dynamiclib -fPIC -Wl,-install_name,@rpath/libwechat_sns_native.dylib $(COMMON_SOURCES) $(PLATFORM_SOURCES) $(PLATFORM_LIBS) -o $@

$(PROBE): src/probe_cli.cpp $(LIBRARY) | $(BUILD_DIR)
	$(CXX) $(COMMON_FLAGS) $(PLATFORM_FLAGS) src/probe_cli.cpp -L$(BUILD_DIR) -lwechat_sns_native -Wl,-rpath,@loader_path -o $@

$(BUILD_DIR)/wechat_sns_adapters.jsonl: $(ADAPTERS) | $(BUILD_DIR)
	cp $(ADAPTERS) $@

probe: all
	$(PROBE)

install-python: all
	cp $(LIBRARY) ../../src/wechat_decrypt_tool/native/libwechat_sns_native.dylib
	cp $(BUILD_DIR)/wechat_sns_adapters.jsonl ../../src/wechat_decrypt_tool/native/wechat_sns_adapters.jsonl

clean:
	rm -rf $(BUILD_DIR)
