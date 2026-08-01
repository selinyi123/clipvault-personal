cmake_minimum_required(VERSION 3.22.1)
project(clipvault_rime_jni LANGUAGES CXX)

if(NOT ANDROID)
  message(FATAL_ERROR "native-a is an Android NDK-only build")
endif()
if(NOT DEFINED CLIPVAULT_LIBRIME_SOURCE)
  message(FATAL_ERROR "CLIPVAULT_LIBRIME_SOURCE must point to locked librime source")
endif()
if(NOT DEFINED CLIPVAULT_LIBRIME_BUILD)
  if(ANDROID_ABI STREQUAL "arm64-v8a" AND DEFINED CLIPVAULT_LIBRIME_BUILD_ARM64)
    set(CLIPVAULT_LIBRIME_BUILD "${CLIPVAULT_LIBRIME_BUILD_ARM64}")
  elseif(ANDROID_ABI STREQUAL "x86_64" AND DEFINED CLIPVAULT_LIBRIME_BUILD_X86_64)
    set(CLIPVAULT_LIBRIME_BUILD "${CLIPVAULT_LIBRIME_BUILD_X86_64}")
  else()
    message(FATAL_ERROR
      "A matching CLIPVAULT_LIBRIME_BUILD_<ABI> must contain lib/librime.a")
  endif()
endif()
if(NOT DEFINED CLIPVAULT_PREBUILT_ROOT)
  message(FATAL_ERROR "CLIPVAULT_PREBUILT_ROOT must point to the audited local dependency snapshot")
endif()

set(LIBRIME_ARCHIVE "${CLIPVAULT_LIBRIME_BUILD}/lib/librime.a")
foreach(required_path
    "${CLIPVAULT_LIBRIME_SOURCE}/src/rime_api.h"
    "${LIBRIME_ARCHIVE}"
    "${CLIPVAULT_PREBUILT_ROOT}/yaml-cpp/${ANDROID_ABI}/lib/libyaml-cpp.a"
    "${CLIPVAULT_PREBUILT_ROOT}/leveldb/${ANDROID_ABI}/lib/libleveldb.a"
    "${CLIPVAULT_PREBUILT_ROOT}/marisa/${ANDROID_ABI}/lib/libmarisa.a"
    "${CLIPVAULT_PREBUILT_ROOT}/opencc/${ANDROID_ABI}/lib/libopencc.a")
  if(NOT EXISTS "${required_path}")
    message(FATAL_ERROR "Missing locked native input: ${required_path}")
  endif()
endforeach()

add_library(clipvault_rime_jni SHARED clipvault_rime_jni.cc)
target_compile_features(clipvault_rime_jni PRIVATE cxx_std_17)
target_compile_definitions(clipvault_rime_jni PRIVATE RIME_NO_LOGGING)
target_compile_options(clipvault_rime_jni PRIVATE
  -fvisibility=hidden
  -fvisibility-inlines-hidden
  "-ffile-prefix-map=${CMAKE_CURRENT_SOURCE_DIR}=.")
target_include_directories(clipvault_rime_jni PRIVATE
  "${CLIPVAULT_LIBRIME_SOURCE}/src")

# librime registers core modules through static initializers; retain the whole
# archive, then resolve its project-pinned transitive static dependencies.
target_link_libraries(clipvault_rime_jni PRIVATE
  "-Wl,--whole-archive"
  "${LIBRIME_ARCHIVE}"
  "-Wl,--no-whole-archive"
  "${CLIPVAULT_PREBUILT_ROOT}/yaml-cpp/${ANDROID_ABI}/lib/libyaml-cpp.a"
  "${CLIPVAULT_PREBUILT_ROOT}/leveldb/${ANDROID_ABI}/lib/libleveldb.a"
  "${CLIPVAULT_PREBUILT_ROOT}/opencc/${ANDROID_ABI}/lib/libopencc.a"
  "${CLIPVAULT_PREBUILT_ROOT}/marisa/${ANDROID_ABI}/lib/libmarisa.a"
  android)

# Android 15+ devices may use 16 KiB pages. These flags make every LOAD segment
# compatible rather than relying on a host linker default.
target_link_options(clipvault_rime_jni PRIVATE
  "LINKER:-z,max-page-size=16384"
  "LINKER:-z,common-page-size=16384"
  "LINKER:--build-id=none")

set_target_properties(clipvault_rime_jni PROPERTIES
  CXX_VISIBILITY_PRESET hidden
  VISIBILITY_INLINES_HIDDEN YES)
