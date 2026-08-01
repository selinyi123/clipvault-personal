cmake_minimum_required(VERSION 3.22.1)
project(clipvault_rime_poc LANGUAGES CXX)

if(NOT ANDROID)
  message(FATAL_ERROR "This isolated target is Android-only")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(CLIPVAULT_RIME_INCLUDE_DIR "" CACHE PATH
    "Directory containing the pinned librime rime_api.h")
set(CLIPVAULT_RIME_LIBRARY "" CACHE FILEPATH
    "Pinned, already-built librime shared library for the current Android ABI")

if(NOT EXISTS "${CLIPVAULT_RIME_INCLUDE_DIR}/rime_api.h")
  message(FATAL_ERROR "CLIPVAULT_RIME_INCLUDE_DIR must contain rime_api.h")
endif()
if(NOT EXISTS "${CLIPVAULT_RIME_LIBRARY}")
  message(FATAL_ERROR "CLIPVAULT_RIME_LIBRARY must point to a built librime library")
endif()

add_library(clipvault_librime SHARED IMPORTED GLOBAL)
set_target_properties(clipvault_librime PROPERTIES
  IMPORTED_LOCATION "${CLIPVAULT_RIME_LIBRARY}"
)

add_library(clipvault_rime_poc SHARED
  src/clipvault_rime_engine.cpp
  src/clipvault_rime_jni.cpp
)

target_include_directories(clipvault_rime_poc PRIVATE
  include
  "${CLIPVAULT_RIME_INCLUDE_DIR}"
)

target_compile_options(clipvault_rime_poc PRIVATE
  -Wall
  -Wextra
  -Werror
  -fvisibility=hidden
  -fvisibility-inlines-hidden
)

target_link_options(clipvault_rime_poc PRIVATE
  "-Wl,-z,max-page-size=16384"
  "-Wl,-z,common-page-size=16384"
)

target_link_libraries(clipvault_rime_poc PRIVATE
  clipvault_librime
)
