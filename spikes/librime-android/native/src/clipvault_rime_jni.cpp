#include <jni.h>

#include <cstdint>
#include <memory>
#include <string>

#include "clipvault_rime_engine.h"

namespace {

using clipvault::rime_poc::EnginePaths;
using clipvault::rime_poc::RimeEngine;
using clipvault::rime_poc::Snapshot;

RimeEngine* FromHandle(jlong handle) {
  return reinterpret_cast<RimeEngine*>(static_cast<std::uintptr_t>(handle));
}

jlong ToHandle(RimeEngine* engine) {
  return static_cast<jlong>(
      reinterpret_cast<std::uintptr_t>(engine));
}

void ThrowState(JNIEnv* env, const char* operation,
                const std::string& detail = {}) {
  jclass exception_class = env->FindClass("java/lang/IllegalStateException");
  if (exception_class == nullptr) {
    return;
  }
  std::string message = operation;
  if (!detail.empty()) {
    message.append(":").append(detail);
  }
  env->ThrowNew(exception_class, message.c_str());
}

bool RequireEngine(JNIEnv* env, jlong handle, RimeEngine** engine) {
  *engine = FromHandle(handle);
  if (*engine == nullptr) {
    ThrowState(env, "native_handle_invalid");
    return false;
  }
  return true;
}

std::string ToUtf8(JNIEnv* env, jstring value, const char* operation) {
  if (value == nullptr) {
    ThrowState(env, operation);
    return {};
  }
  const char* chars = env->GetStringUTFChars(value, nullptr);
  if (chars == nullptr) {
    return {};
  }
  std::string result(chars);
  env->ReleaseStringUTFChars(value, chars);
  return result;
}

jstring NewString(JNIEnv* env, const std::string& value) {
  return env->NewStringUTF(value.c_str());
}

}  // namespace

extern "C" JNIEXPORT jlong JNICALL
Java_com_clipvault_poc_rime_RimeNativeEngine_nativeOpen(
    JNIEnv* env, jclass clazz, jstring shared_data_dir,
    jstring user_data_dir) {
  (void)clazz;
  EnginePaths paths{
      ToUtf8(env, shared_data_dir, "shared_data_dir_missing"),
      ToUtf8(env, user_data_dir, "user_data_dir_missing"),
  };
  if (env->ExceptionCheck()) {
    return 0;
  }

  std::string error;
  std::unique_ptr<RimeEngine> engine = RimeEngine::Open(paths, &error);
  if (engine == nullptr) {
    ThrowState(env, "native_open_failed", error);
    return 0;
  }
  return ToHandle(engine.release());
}

extern "C" JNIEXPORT void JNICALL
Java_com_clipvault_poc_rime_RimeNativeEngine_nativeClose(
    JNIEnv* env, jclass clazz, jlong handle) {
  (void)env;
  (void)clazz;
  delete FromHandle(handle);
}

extern "C" JNIEXPORT jboolean JNICALL
Java_com_clipvault_poc_rime_RimeNativeEngine_nativeReset(
    JNIEnv* env, jclass clazz, jlong handle) {
  (void)clazz;
  RimeEngine* engine = nullptr;
  if (!RequireEngine(env, handle, &engine)) {
    return JNI_FALSE;
  }
  std::string error;
  if (!engine->Reset(&error)) {
    ThrowState(env, "native_reset_failed", error);
    return JNI_FALSE;
  }
  return JNI_TRUE;
}

extern "C" JNIEXPORT jboolean JNICALL
Java_com_clipvault_poc_rime_RimeNativeEngine_nativeProcessKey(
    JNIEnv* env, jclass clazz, jlong handle, jint keycode, jint mask) {
  (void)clazz;
  RimeEngine* engine = nullptr;
  if (!RequireEngine(env, handle, &engine)) {
    return JNI_FALSE;
  }
  std::string error;
  const bool handled = engine->ProcessKey(keycode, mask, &error);
  if (!error.empty()) {
    ThrowState(env, "native_process_key_failed", error);
    return JNI_FALSE;
  }
  return handled ? JNI_TRUE : JNI_FALSE;
}

extern "C" JNIEXPORT jobjectArray JNICALL
Java_com_clipvault_poc_rime_RimeNativeEngine_nativeSnapshot(
    JNIEnv* env, jclass clazz, jlong handle) {
  (void)clazz;
  RimeEngine* engine = nullptr;
  if (!RequireEngine(env, handle, &engine)) {
    return nullptr;
  }
  Snapshot snapshot;
  std::string error;
  if (!engine->GetSnapshot(&snapshot, &error)) {
    ThrowState(env, "native_snapshot_failed", error);
    return nullptr;
  }

  jclass string_class = env->FindClass("java/lang/String");
  if (string_class == nullptr) {
    return nullptr;
  }
  const jsize size =
      static_cast<jsize>(2 + snapshot.candidates.size() * 2);
  jobjectArray result = env->NewObjectArray(size, string_class, nullptr);
  if (result == nullptr) {
    return nullptr;
  }

  env->SetObjectArrayElement(result, 0,
                             NewString(env, snapshot.composition));
  env->SetObjectArrayElement(
      result, 1,
      NewString(env, std::to_string(snapshot.highlighted_candidate_index)));
  for (std::size_t index = 0; index < snapshot.candidates.size(); ++index) {
    const auto& candidate = snapshot.candidates[index];
    const jsize offset = static_cast<jsize>(2 + index * 2);
    env->SetObjectArrayElement(result, offset,
                               NewString(env, candidate.text));
    env->SetObjectArrayElement(result, offset + 1,
                               NewString(env, candidate.comment));
  }
  return result;
}

extern "C" JNIEXPORT jboolean JNICALL
Java_com_clipvault_poc_rime_RimeNativeEngine_nativeSelectCandidate(
    JNIEnv* env, jclass clazz, jlong handle, jint index) {
  (void)clazz;
  if (index < 0) {
    ThrowState(env, "candidate_index_invalid");
    return JNI_FALSE;
  }
  RimeEngine* engine = nullptr;
  if (!RequireEngine(env, handle, &engine)) {
    return JNI_FALSE;
  }
  std::string error;
  if (!engine->SelectCandidate(static_cast<std::size_t>(index), &error)) {
    ThrowState(env, "native_select_candidate_failed", error);
    return JNI_FALSE;
  }
  return JNI_TRUE;
}

extern "C" JNIEXPORT jstring JNICALL
Java_com_clipvault_poc_rime_RimeNativeEngine_nativeTakeCommit(
    JNIEnv* env, jclass clazz, jlong handle) {
  (void)clazz;
  RimeEngine* engine = nullptr;
  if (!RequireEngine(env, handle, &engine)) {
    return nullptr;
  }
  std::string error;
  const auto commit = engine->TakeCommit(&error);
  if (!error.empty()) {
    ThrowState(env, "native_take_commit_failed", error);
    return nullptr;
  }
  return commit.has_value() ? NewString(env, *commit) : nullptr;
}

extern "C" JNIEXPORT jstring JNICALL
Java_com_clipvault_poc_rime_RimeNativeEngine_nativeEngineVersion(
    JNIEnv* env, jclass clazz, jlong handle) {
  (void)clazz;
  RimeEngine* engine = nullptr;
  if (!RequireEngine(env, handle, &engine)) {
    return nullptr;
  }
  return NewString(env, engine->EngineVersion());
}
