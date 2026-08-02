cmake_minimum_required(VERSION 3.22.1)
project(clipvault_rime_bridge LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(clipvault_rime_bridge STATIC
  src/rime_bridge.cpp
  src/utf8.cpp
)
target_include_directories(clipvault_rime_bridge PUBLIC include)
target_compile_options(clipvault_rime_bridge PRIVATE
  $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall;-Wextra;-Wpedantic;-Werror>
)

option(
  CLIPVAULT_BUILD_ANDROID_CONTRACT
  "Compile the full JNI/librime bridge as Android object files without linking librime"
  OFF
)

if(CLIPVAULT_BUILD_ANDROID_CONTRACT)
  if(NOT ANDROID)
    message(FATAL_ERROR "CLIPVAULT_BUILD_ANDROID_CONTRACT requires the Android NDK toolchain")
  endif()
  if(NOT DEFINED RIME_API_INCLUDE_DIR OR NOT EXISTS "${RIME_API_INCLUDE_DIR}/rime_api.h")
    message(FATAL_ERROR "RIME_API_INCLUDE_DIR must contain the locked rime_api.h")
  endif()
  if(NOT DEFINED GENERATED_JNI_INCLUDE_DIR OR
     NOT EXISTS "${GENERATED_JNI_INCLUDE_DIR}/org_clipvault_rime_poc_NativeRimeBridge.h")
    message(FATAL_ERROR "GENERATED_JNI_INCLUDE_DIR must contain the javac-generated JNI header")
  endif()

  # This target deliberately stops at object files. It proves that the complete
  # project-owned bridge/JNI boundary compiles for each locked Android ABI while
  # avoiding any false claim that librime or its dependency closure already links.
  add_library(clipvault_rime_android_contract OBJECT
    src/rime_bridge.cpp
    src/utf8.cpp
    src/librime_backend.cpp
    src/jni_bridge.cpp
  )
  target_include_directories(clipvault_rime_android_contract PRIVATE
    include
    "${RIME_API_INCLUDE_DIR}"
    "${GENERATED_JNI_INCLUDE_DIR}"
  )
  target_compile_options(clipvault_rime_android_contract PRIVATE
    $<$<CXX_COMPILER_ID:Clang>:-Wall;-Wextra;-Wpedantic;-Werror;-Wno-missing-field-initializers>
    -include
    "${GENERATED_JNI_INCLUDE_DIR}/org_clipvault_rime_poc_NativeRimeBridge.h"
  )
  set_target_properties(clipvault_rime_android_contract PROPERTIES
    POSITION_INDEPENDENT_CODE ON
  )
endif()

include(CTest)
if(BUILD_TESTING)
  add_executable(clipvault_rime_bridge_test tests/rime_bridge_test.cpp)
  target_link_libraries(clipvault_rime_bridge_test PRIVATE clipvault_rime_bridge)
  target_compile_options(clipvault_rime_bridge_test PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall;-Wextra;-Wpedantic;-Werror>
  )
  add_test(NAME clipvault_rime_bridge_test COMMAND clipvault_rime_bridge_test)

  add_executable(clipvault_utf8_test tests/utf8_test.cpp)
  target_link_libraries(clipvault_utf8_test PRIVATE clipvault_rime_bridge)
  target_compile_options(clipvault_utf8_test PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall;-Wextra;-Wpedantic;-Werror>
  )
  add_test(NAME clipvault_utf8_test COMMAND clipvault_utf8_test)
endif()
