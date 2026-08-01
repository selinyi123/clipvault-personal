cmake_minimum_required(VERSION 3.22.1)
project(clipvault_rime_bridge LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(clipvault_rime_bridge STATIC
  src/rime_bridge.cpp
)
target_include_directories(clipvault_rime_bridge PUBLIC include)
target_compile_options(clipvault_rime_bridge PRIVATE
  $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall;-Wextra;-Wpedantic;-Werror>
)

include(CTest)
if(BUILD_TESTING)
  add_executable(clipvault_rime_bridge_test tests/rime_bridge_test.cpp)
  target_link_libraries(clipvault_rime_bridge_test PRIVATE clipvault_rime_bridge)
  target_compile_options(clipvault_rime_bridge_test PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall;-Wextra;-Wpedantic;-Werror>
  )
  add_test(NAME clipvault_rime_bridge_test COMMAND clipvault_rime_bridge_test)
endif()
