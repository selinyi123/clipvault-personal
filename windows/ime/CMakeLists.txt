cmake_minimum_required(VERSION 3.24)
cmake_policy(SET CMP0091 NEW)
project(ClipVaultWindowsImeNative LANGUAGES CXX)

if(NOT WIN32 OR NOT MSVC)
  message(FATAL_ERROR "ClipVault Windows IME native slice requires MSVC on Windows")
endif()

option(CLIPVAULT_ENABLE_ARM64_EXPERIMENTAL
       "Allow the not-yet-released ARM64 TSF build" OFF)
if(CMAKE_GENERATOR_PLATFORM STREQUAL "ARM64" AND
   NOT CLIPVAULT_ENABLE_ARM64_EXPERIMENTAL)
  message(FATAL_ERROR
    "ARM64 remains an explicit release gate; set CLIPVAULT_ENABLE_ARM64_EXPERIMENTAL=ON for development only")
endif()

set(_clipvault_default_build_host OFF)
if(CMAKE_SIZEOF_VOID_P EQUAL 8 AND
   NOT CMAKE_GENERATOR_PLATFORM STREQUAL "ARM64")
  set(_clipvault_default_build_host ON)
endif()
option(CLIPVAULT_BUILD_HOST
       "Build the single per-user x64 engine Host" ${_clipvault_default_build_host})
option(CLIPVAULT_REQUIRE_RIME_RUNTIME
       "Fail the production build unless the x64 Host ships with Rime SDK and data" OFF)
option(CLIPVAULT_ENABLE_INSECURE_DEVELOPMENT_TRUST
       "Compile the explicit unsigned pipe-trust escape hatch for local development only" OFF)
if(CLIPVAULT_BUILD_HOST AND NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
  message(FATAL_ERROR "ClipVaultImeHost is x64-only; Win32 builds the TSF DLL only")
endif()
if(CLIPVAULT_REQUIRE_RIME_RUNTIME AND
   (NOT CLIPVAULT_BUILD_HOST OR NOT CLIPVAULT_RIME_SDK_DIR OR
    NOT CLIPVAULT_RIME_DATA_DIR))
  message(FATAL_ERROR
    "CLIPVAULT_REQUIRE_RIME_RUNTIME requires x64 Host, CLIPVAULT_RIME_SDK_DIR, and CLIPVAULT_RIME_DATA_DIR")
endif()

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
# Every first-party DLL/EXE/test is app-local and statically links the MSVC
# runtime. The pinned official rime.dll is audited separately below the build;
# it imports only Windows system DLLs. This keeps clean-machine installation
# independent from an external VC Redistributable.
set(CMAKE_MSVC_RUNTIME_LIBRARY
    "MultiThreaded$<$<CONFIG:Debug>:Debug>")

set(CLIPVAULT_RIME_SDK_DIR "" CACHE PATH
    "Extracted official librime SDK containing include/rime_api.h and lib/rime.dll")
set(CLIPVAULT_RIME_DATA_DIR "" CACHE PATH
    "Rime shared-data directory copied beside ClipVaultImeHost for isolated tests")

set(CLIPVAULT_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
foreach(kind RUNTIME LIBRARY ARCHIVE)
  set(CMAKE_${kind}_OUTPUT_DIRECTORY "${CLIPVAULT_OUTPUT_DIRECTORY}")
  foreach(config Debug Release RelWithDebInfo MinSizeRel)
    string(TOUPPER "${config}" config_upper)
    set(CMAKE_${kind}_OUTPUT_DIRECTORY_${config_upper} "${CLIPVAULT_OUTPUT_DIRECTORY}")
  endforeach()
endforeach()

add_library(clipvault_ime_protocol STATIC
  common/diagnostics.cpp
  common/diagnostics.h
  common/protocol.cpp
  common/protocol.h
)
target_include_directories(clipvault_ime_protocol PUBLIC common)
target_compile_definitions(clipvault_ime_protocol PUBLIC
  UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX _WIN32_WINNT=0x0A00
)
if(CLIPVAULT_ENABLE_INSECURE_DEVELOPMENT_TRUST)
  target_compile_definitions(clipvault_ime_protocol PUBLIC
    CLIPVAULT_ENABLE_INSECURE_DEVELOPMENT_TRUST=1)
endif()
target_compile_options(clipvault_ime_protocol PRIVATE /W4 /WX /permissive- /utf-8)
target_link_libraries(clipvault_ime_protocol PUBLIC ole32)

# OTP wire is deliberately separate from crypto/WinCred. The x64 Host links
# only this bounded local client protocol; the TSF DLL links no OTP target.
add_library(clipvault_otp_wire STATIC
  ../otp-relay/broker/broker_protocol.cpp
  ../otp-relay/broker/broker_protocol.h
)
target_include_directories(clipvault_otp_wire PUBLIC
  ../otp-relay/broker ../otp-relay/crypto)
target_compile_definitions(clipvault_otp_wire PUBLIC
  UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX _WIN32_WINNT=0x0A00)
if(CLIPVAULT_ENABLE_INSECURE_DEVELOPMENT_TRUST)
  target_compile_definitions(clipvault_otp_wire PUBLIC
    CLIPVAULT_ENABLE_INSECURE_DEVELOPMENT_TRUST=1)
endif()
target_compile_options(clipvault_otp_wire PRIVATE
  /W4 /WX /permissive- /utf-8)

add_library(clipvault_otp_crypto STATIC
  ../otp-relay/crypto/otp_aead_cng.cpp
  ../otp-relay/crypto/otp_aead_cng.h
)
target_include_directories(clipvault_otp_crypto PUBLIC ../otp-relay/crypto)
target_compile_definitions(clipvault_otp_crypto PUBLIC
  UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX _WIN32_WINNT=0x0A00)
target_compile_options(clipvault_otp_crypto PRIVATE
  /W4 /WX /permissive- /utf-8)
target_link_libraries(clipvault_otp_crypto PUBLIC bcrypt)

if(CLIPVAULT_BUILD_HOST)
  add_library(clipvault_otp_broker STATIC
    ../otp-relay/authority/pair_credential.cpp
    ../otp-relay/authority/pair_credential.h
    ../otp-relay/broker/broker_server.cpp
    ../otp-relay/broker/broker_server.h
    ../otp-relay/broker/client_identity.cpp
    ../otp-relay/broker/client_identity.h
    ../otp-relay/broker/otp_broker_core.cpp
    ../otp-relay/broker/otp_broker_core.h
    ../otp-relay/broker/otp_broker_service.cpp
    ../otp-relay/broker/otp_broker_service.h
    ../otp-relay/broker/otp_prompt.cpp
    ../otp-relay/broker/otp_prompt.h
  )
  target_include_directories(clipvault_otp_broker PUBLIC
    ../otp-relay/authority ../otp-relay/broker ../otp-relay/crypto)
  target_compile_definitions(clipvault_otp_broker PUBLIC
    UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX _WIN32_WINNT=0x0A00)
  if(CLIPVAULT_ENABLE_INSECURE_DEVELOPMENT_TRUST)
    target_compile_definitions(clipvault_otp_broker PUBLIC
      CLIPVAULT_ENABLE_INSECURE_DEVELOPMENT_TRUST=1)
  endif()
  target_compile_options(clipvault_otp_broker PRIVATE
    /W4 /WX /permissive- /utf-8)
  target_link_libraries(clipvault_otp_broker PUBLIC
    clipvault_otp_wire clipvault_otp_crypto advapi32 bcrypt user32 wintrust)

  add_executable(ClipVaultOtpBroker WIN32 ../otp-relay/broker/main.cpp)
  target_compile_options(ClipVaultOtpBroker PRIVATE
    /W4 /WX /permissive- /utf-8)
  target_link_libraries(ClipVaultOtpBroker PRIVATE clipvault_otp_broker)
endif()

if(CLIPVAULT_BUILD_HOST)
  add_library(clipvault_ime_replay_ledger STATIC
    host/replay_ledger.cpp
    host/replay_ledger.h
  )
  target_include_directories(clipvault_ime_replay_ledger PUBLIC host)
  target_compile_definitions(clipvault_ime_replay_ledger PUBLIC
    UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX _WIN32_WINNT=0x0A00)
  target_compile_options(clipvault_ime_replay_ledger PRIVATE
    /W4 /WX /permissive- /utf-8)
  target_link_libraries(clipvault_ime_replay_ledger PUBLIC ole32)

  add_library(clipvault_runtime_snapshot_host STATIC
    host/runtime_snapshot.cpp
    host/runtime_snapshot.h
  )
  target_include_directories(clipvault_runtime_snapshot_host PUBLIC host)
  target_compile_options(clipvault_runtime_snapshot_host PRIVATE
    /W4 /WX /permissive- /utf-8)
  target_link_libraries(clipvault_runtime_snapshot_host PUBLIC
    clipvault_ime_protocol advapi32 wintrust)

  add_executable(ClipVaultImeHost
    host/main.cpp
    host/otp_broker_client.cpp
    host/otp_broker_client.h
    host/rime_engine.cpp
    host/rime_engine.h
  )
  target_compile_options(ClipVaultImeHost PRIVATE /W4 /WX /permissive- /utf-8)
  target_link_libraries(ClipVaultImeHost PRIVATE
    clipvault_ime_protocol clipvault_ime_replay_ledger
    clipvault_runtime_snapshot_host clipvault_otp_wire advapi32)
  if(CLIPVAULT_REQUIRE_RIME_RUNTIME)
    target_compile_definitions(ClipVaultImeHost PRIVATE
      CLIPVAULT_REQUIRE_RIME_RUNTIME=1)
  endif()
endif()

if(CLIPVAULT_RIME_SDK_DIR)
  if(NOT CLIPVAULT_BUILD_HOST)
    message(FATAL_ERROR "CLIPVAULT_RIME_SDK_DIR is valid only for the x64 Host build")
  endif()
  set(CLIPVAULT_RIME_HEADER "${CLIPVAULT_RIME_SDK_DIR}/include/rime_api.h")
  set(CLIPVAULT_RIME_DLL "${CLIPVAULT_RIME_SDK_DIR}/lib/rime.dll")
  if(NOT EXISTS "${CLIPVAULT_RIME_HEADER}" OR NOT EXISTS "${CLIPVAULT_RIME_DLL}")
    message(FATAL_ERROR
      "CLIPVAULT_RIME_SDK_DIR must contain include/rime_api.h and lib/rime.dll")
  endif()
  target_compile_definitions(ClipVaultImeHost PRIVATE CLIPVAULT_WITH_RIME=1)
  target_include_directories(ClipVaultImeHost PRIVATE
    "${CLIPVAULT_RIME_SDK_DIR}/include")
  add_custom_command(TARGET ClipVaultImeHost POST_BUILD
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different
            "${CLIPVAULT_RIME_DLL}" "$<TARGET_FILE_DIR:ClipVaultImeHost>/rime.dll"
    VERBATIM)
endif()

if(CLIPVAULT_RIME_DATA_DIR)
  if(NOT CLIPVAULT_BUILD_HOST)
    message(FATAL_ERROR "CLIPVAULT_RIME_DATA_DIR is valid only for the x64 Host build")
  endif()
  if(NOT IS_DIRECTORY "${CLIPVAULT_RIME_DATA_DIR}")
    message(FATAL_ERROR "CLIPVAULT_RIME_DATA_DIR must be an existing directory")
  endif()
  add_custom_command(TARGET ClipVaultImeHost POST_BUILD
    COMMAND "${CMAKE_COMMAND}" -E copy_directory
            "${CLIPVAULT_RIME_DATA_DIR}"
            "$<TARGET_FILE_DIR:ClipVaultImeHost>/rime-data"
    VERBATIM)
endif()

add_library(clipvault_candidate_layout STATIC
  tsf/candidate_layout.cpp
  tsf/candidate_layout.h
)
target_include_directories(clipvault_candidate_layout PUBLIC tsf)
target_compile_definitions(clipvault_candidate_layout PUBLIC
  UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX _WIN32_WINNT=0x0A00)
target_compile_options(clipvault_candidate_layout PRIVATE
  /W4 /WX /permissive- /utf-8)

add_library(ClipVaultTextService SHARED
  tsf/candidate_window.cpp
  tsf/candidate_window.h
  tsf/class_factory.cpp
  tsf/dllmain.cpp
  tsf/globals.cpp
  tsf/globals.h
  tsf/key_translation.h
  tsf/registration.cpp
  tsf/text_service.cpp
  tsf/text_service.h
  tsf/ClipVaultTextService.def
)
target_include_directories(ClipVaultTextService PRIVATE tsf)
if(CMAKE_GENERATOR_PLATFORM STREQUAL "x64")
  target_compile_definitions(ClipVaultTextService PRIVATE
    CLIPVAULT_TSF_PROFILE_OWNER=1)
else()
  # x86 needs its own COM registry view, but the shared TSF language profile
  # has exactly one owner. ARM64 remains non-owner until its release design is
  # reviewed independently.
  target_compile_definitions(ClipVaultTextService PRIVATE
    CLIPVAULT_TSF_PROFILE_OWNER=0)
endif()
target_compile_options(ClipVaultTextService PRIVATE /W4 /WX /permissive- /utf-8)
target_link_libraries(ClipVaultTextService PRIVATE
  clipvault_candidate_layout clipvault_ime_protocol
  advapi32 ole32 user32 uuid rpcrt4
)

include(CTest)
if(BUILD_TESTING)
  # Manual evidence host: a clean Win32 EDIT control that loads the currently
  # selected TSF profile without touching user documents or application data.
  add_executable(clipvault_tsf_evidence_editor WIN32
    tests/tsf_evidence_editor.cpp)
  target_compile_definitions(clipvault_tsf_evidence_editor PRIVATE
    UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX _WIN32_WINNT=0x0A00)
  target_compile_options(clipvault_tsf_evidence_editor PRIVATE
    /W4 /WX /permissive- /utf-8)
  target_link_libraries(clipvault_tsf_evidence_editor PRIVATE user32 gdi32)

  # Manual diagnostics capture: listens to the current session's DBWIN channel
  # and prints only content-free ClipVaultIme event lines with their source PID.
  add_executable(clipvault_dbwin_diagnostics_capture
    tests/dbwin_diagnostics_capture.cpp)
  target_compile_definitions(clipvault_dbwin_diagnostics_capture PRIVATE
    WIN32_LEAN_AND_MEAN NOMINMAX _WIN32_WINNT=0x0A00)
  target_compile_options(clipvault_dbwin_diagnostics_capture PRIVATE
    /W4 /WX /permissive- /utf-8)

  add_executable(clipvault_candidate_layout_tests
    tests/candidate_layout_tests.cpp)
  target_compile_options(clipvault_candidate_layout_tests PRIVATE
    /W4 /WX /permissive- /utf-8)
  target_link_libraries(clipvault_candidate_layout_tests PRIVATE
    clipvault_candidate_layout)
  add_test(NAME clipvault-ime-candidate-layout
           COMMAND clipvault_candidate_layout_tests)

  add_executable(clipvault_otp_aead_vectors
    ../otp-relay/tests/otp_aead_vectors.cpp)
  target_compile_options(clipvault_otp_aead_vectors PRIVATE
    /W4 /WX /permissive- /utf-8)
  target_link_libraries(clipvault_otp_aead_vectors PRIVATE
    clipvault_otp_crypto)
  add_test(NAME clipvault-otp-aead-v001 COMMAND clipvault_otp_aead_vectors)

  if(CLIPVAULT_BUILD_HOST)
    add_executable(clipvault_ime_replay_ledger_tests
      tests/replay_ledger_tests.cpp)
    target_compile_options(clipvault_ime_replay_ledger_tests PRIVATE
      /W4 /WX /permissive- /utf-8)
    target_link_libraries(clipvault_ime_replay_ledger_tests PRIVATE
      clipvault_ime_replay_ledger)
    add_test(NAME clipvault-ime-engine-v2-replay-ledger
             COMMAND clipvault_ime_replay_ledger_tests)
    set_tests_properties(clipvault-ime-engine-v2-replay-ledger
      PROPERTIES TIMEOUT 10)

    add_executable(clipvault_otp_broker_core_tests
      ../otp-relay/tests/otp_broker_core_tests.cpp)
    target_compile_options(clipvault_otp_broker_core_tests PRIVATE
      /W4 /WX /permissive- /utf-8)
    target_link_libraries(clipvault_otp_broker_core_tests PRIVATE
      clipvault_otp_broker)
    add_test(NAME clipvault-otp-broker-core COMMAND clipvault_otp_broker_core_tests)

    add_executable(clipvault_otp_broker_pipe_tests
      ../otp-relay/tests/otp_broker_pipe_tests.cpp)
    target_compile_options(clipvault_otp_broker_pipe_tests PRIVATE
      /W4 /WX /permissive- /utf-8)
    target_link_libraries(clipvault_otp_broker_pipe_tests PRIVATE
      clipvault_otp_broker)
    add_test(NAME clipvault-otp-broker-pipe COMMAND clipvault_otp_broker_pipe_tests)
    set_tests_properties(clipvault-otp-broker-pipe PROPERTIES TIMEOUT 10)

    add_executable(clipvault_otp_credential_tests
      ../otp-relay/tests/otp_credential_tests.cpp)
    target_compile_options(clipvault_otp_credential_tests PRIVATE
      /W4 /WX /permissive- /utf-8)
    target_link_libraries(clipvault_otp_credential_tests PRIVATE
      clipvault_otp_broker)
    add_test(NAME clipvault-otp-wincred-cvpk-v1
             COMMAND clipvault_otp_credential_tests)
    set_tests_properties(clipvault-otp-wincred-cvpk-v1 PROPERTIES TIMEOUT 10)
  endif()

  add_executable(clipvault_ime_protocol_tests tests/protocol_tests.cpp)
  target_compile_options(clipvault_ime_protocol_tests PRIVATE /W4 /WX /permissive- /utf-8)
  target_link_libraries(clipvault_ime_protocol_tests PRIVATE clipvault_ime_protocol)
  add_test(NAME clipvault-ime-protocol COMMAND clipvault_ime_protocol_tests)

  add_executable(clipvault_ime_connect_budget_tests tests/connect_budget_tests.cpp)
  target_compile_options(clipvault_ime_connect_budget_tests PRIVATE
    /W4 /WX /permissive- /utf-8)
  target_include_directories(clipvault_ime_connect_budget_tests PRIVATE tsf)
  target_link_libraries(clipvault_ime_connect_budget_tests PRIVATE
    clipvault_ime_protocol)
  add_test(NAME clipvault-ime-connect-budget
           COMMAND clipvault_ime_connect_budget_tests)
  set_tests_properties(clipvault-ime-connect-budget PROPERTIES TIMEOUT 5)

  add_executable(clipvault_ime_diagnostics_tests tests/diagnostics_tests.cpp)
  target_compile_options(clipvault_ime_diagnostics_tests PRIVATE
    /W4 /WX /permissive- /utf-8)
  target_link_libraries(clipvault_ime_diagnostics_tests PRIVATE
    clipvault_ime_protocol)
  add_test(NAME clipvault-ime-diagnostics COMMAND clipvault_ime_diagnostics_tests)

  add_executable(clipvault_ime_pipe_deadline_tests tests/pipe_deadline_tests.cpp)
  target_compile_options(clipvault_ime_pipe_deadline_tests PRIVATE
    /W4 /WX /permissive- /utf-8)
  target_include_directories(clipvault_ime_pipe_deadline_tests PRIVATE tsf)
  target_link_libraries(clipvault_ime_pipe_deadline_tests PRIVATE
    clipvault_ime_protocol)
  add_test(NAME clipvault-ime-pipe-deadline
           COMMAND clipvault_ime_pipe_deadline_tests)
  set_tests_properties(clipvault-ime-pipe-deadline PROPERTIES TIMEOUT 10)

  if(CLIPVAULT_BUILD_HOST)
    add_executable(clipvault_runtime_snapshot_tests
      tests/runtime_snapshot_tests.cpp)
    target_compile_options(clipvault_runtime_snapshot_tests PRIVATE
      /W4 /WX /permissive- /utf-8)
    target_link_libraries(clipvault_runtime_snapshot_tests PRIVATE
      clipvault_runtime_snapshot_host)
    add_test(NAME clipvault-runtime-snapshot-v1
             COMMAND clipvault_runtime_snapshot_tests)
    set_tests_properties(clipvault-runtime-snapshot-v1 PROPERTIES TIMEOUT 10)

    add_executable(clipvault_runtime_snapshot_pipe_tests
      tests/runtime_snapshot_pipe_tests.cpp)
    target_compile_options(clipvault_runtime_snapshot_pipe_tests PRIVATE
      /W4 /WX /permissive- /utf-8)
    target_link_libraries(clipvault_runtime_snapshot_pipe_tests PRIVATE
      clipvault_runtime_snapshot_host)
    add_test(NAME clipvault-runtime-snapshot-pipe
             COMMAND clipvault_runtime_snapshot_pipe_tests)
    set_tests_properties(clipvault-runtime-snapshot-pipe PROPERTIES TIMEOUT 10)

    add_executable(clipvault_ime_host_smoke tests/host_smoke.cpp)
    target_compile_options(clipvault_ime_host_smoke PRIVATE /W4 /WX /permissive- /utf-8)
    target_link_libraries(clipvault_ime_host_smoke PRIVATE clipvault_ime_protocol)
    add_test(NAME clipvault-ime-host-smoke
             COMMAND clipvault_ime_host_smoke $<TARGET_FILE:ClipVaultImeHost> --echo)
    set_tests_properties(clipvault-ime-host-smoke PROPERTIES TIMEOUT 15)

    add_executable(clipvault_ime_engine_v2_semantics
      tests/engine_v2_semantics.cpp)
    target_compile_options(clipvault_ime_engine_v2_semantics PRIVATE
      /W4 /WX /permissive- /utf-8)
    target_link_libraries(clipvault_ime_engine_v2_semantics PRIVATE
      clipvault_ime_protocol)
    add_test(NAME clipvault-ime-engine-v2-semantics
             COMMAND clipvault_ime_engine_v2_semantics
                     $<TARGET_FILE:ClipVaultImeHost>)
    set_tests_properties(clipvault-ime-engine-v2-semantics PROPERTIES TIMEOUT 15)

    add_executable(clipvault_ime_host_restart_smoke tests/host_restart_smoke.cpp)
    target_compile_options(clipvault_ime_host_restart_smoke PRIVATE
      /W4 /WX /permissive- /utf-8)
    target_link_libraries(clipvault_ime_host_restart_smoke PRIVATE
      clipvault_ime_protocol)
    add_test(NAME clipvault-ime-host-restart-smoke
             COMMAND clipvault_ime_host_restart_smoke
                     $<TARGET_FILE:ClipVaultImeHost>)
    set_tests_properties(clipvault-ime-host-restart-smoke PROPERTIES TIMEOUT 20)

    add_executable(clipvault_ime_host_ready_smoke tests/host_ready_smoke.cpp)
    target_compile_options(clipvault_ime_host_ready_smoke PRIVATE
      /W4 /WX /permissive- /utf-8)
    target_link_libraries(clipvault_ime_host_ready_smoke PRIVATE
      clipvault_ime_protocol)
    add_test(NAME clipvault-ime-host-ready-before-rime
             COMMAND clipvault_ime_host_ready_smoke
                     $<TARGET_FILE:ClipVaultImeHost>)
    set_tests_properties(clipvault-ime-host-ready-before-rime PROPERTIES TIMEOUT 10)

    if(CLIPVAULT_RIME_SDK_DIR AND CLIPVAULT_RIME_DATA_DIR)
      add_test(NAME clipvault-ime-rime-host-smoke
               COMMAND clipvault_ime_host_smoke
                       $<TARGET_FILE:ClipVaultImeHost> --require-rime)
      set_tests_properties(clipvault-ime-rime-host-smoke PROPERTIES TIMEOUT 90)

      add_executable(clipvault_ime_rime_upgrade_smoke
        tests/host_rime_upgrade_smoke.cpp)
      target_compile_options(clipvault_ime_rime_upgrade_smoke PRIVATE
        /W4 /WX /permissive- /utf-8)
      target_link_libraries(clipvault_ime_rime_upgrade_smoke PRIVATE
        clipvault_ime_protocol)
      add_test(NAME clipvault-ime-rime-upgrade-from-echo
               COMMAND clipvault_ime_rime_upgrade_smoke
                       $<TARGET_FILE:ClipVaultImeHost>)
      set_tests_properties(clipvault-ime-rime-upgrade-from-echo PROPERTIES TIMEOUT 90)

      add_executable(clipvault_ime_rime_first_run_stability
        tests/host_rime_first_run_stability.cpp)
      target_compile_options(clipvault_ime_rime_first_run_stability PRIVATE
        /W4 /WX /permissive- /utf-8)
      target_link_libraries(clipvault_ime_rime_first_run_stability PRIVATE
        clipvault_ime_protocol)
      add_test(NAME clipvault-ime-rime-clean-first-run-stability
               COMMAND clipvault_ime_rime_first_run_stability
                       $<TARGET_FILE:ClipVaultImeHost>)
      set_tests_properties(clipvault-ime-rime-clean-first-run-stability
        PROPERTIES TIMEOUT 180)
    endif()
  endif()
endif()
