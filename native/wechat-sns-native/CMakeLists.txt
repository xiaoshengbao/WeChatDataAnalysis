cmake_minimum_required(VERSION 3.20)
project(wechat_sns_native LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(WCS_COMMON_SOURCES
    src/core.cpp
    src/adapter_registry.cpp
    src/pattern_scanner.cpp
)

if(APPLE)
    enable_language(OBJCXX)
    list(APPEND WCS_COMMON_SOURCES src/platform_macos.mm src/bridge_unix.cpp)
elseif(WIN32)
    list(APPEND WCS_COMMON_SOURCES src/platform_windows.cpp)
else()
    message(FATAL_ERROR "wechat-sns-native supports macOS and Windows only")
endif()

add_library(wechat_sns_native SHARED ${WCS_COMMON_SOURCES})
target_include_directories(wechat_sns_native PRIVATE include src)
if(MSVC)
    target_compile_options(wechat_sns_native PRIVATE /W4 /WX)
else()
    target_compile_options(wechat_sns_native PRIVATE -Wall -Wextra -Werror)
endif()
set_target_properties(wechat_sns_native PROPERTIES
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN YES
)

if(APPLE)
    find_library(FOUNDATION_FRAMEWORK Foundation REQUIRED)
    target_link_libraries(wechat_sns_native PRIVATE ${FOUNDATION_FRAMEWORK})
    set_target_properties(wechat_sns_native PROPERTIES
        OUTPUT_NAME wechat_sns_native
        BUILD_RPATH "@loader_path"
        INSTALL_RPATH "@loader_path"
    )
elseif(WIN32)
    target_compile_definitions(wechat_sns_native PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
    target_link_libraries(wechat_sns_native PRIVATE bcrypt version)
endif()

add_executable(wechat-sns-probe src/probe_cli.cpp)
target_include_directories(wechat-sns-probe PRIVATE include)
target_link_libraries(wechat-sns-probe PRIVATE wechat_sns_native)

add_custom_command(TARGET wechat_sns_native POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        ${CMAKE_CURRENT_SOURCE_DIR}/adapters/wechat_sns_adapters.jsonl
        $<TARGET_FILE_DIR:wechat_sns_native>/wechat_sns_adapters.jsonl
)
