#include <jni.h>

#include <atomic>
#include <cstddef>
#include <limits>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include "clipvault/librime_backend.h"
#include "clipvault/rime_bridge.h"
#include "clipvault/utf8.h"

namespace clipvault::rime_poc::jni {
namespace {

struct Session final {
  explicit Session(std::unique_ptr<Bridge> value) : bridge(std::move(value)) {}
  std::unique_ptr<Bridge> bridge;
};

std::mutex& registry_mutex() {
  static std::mutex mutex;
  return mutex;
}

std::unordered_map<jlong, std::shared_ptr<Session>>& registry() {
  static std::unordered_map<jlong, std::shared_ptr<Session>> sessions;
  return sessions;
}

std::atomic<jlong>& next_handle() {
  static std::atomic<jlong> value{1};
  return value;
}

void throw_java(JNIEnv* env, const char* class_name, const std::string& message) {
  if (env->ExceptionCheck()) {
    return;
  }
  jclass error_class = env->FindClass(class_name);
  if (error_class == nullptr) {
    return;
  }
  env->ThrowNew(error_class, message.c_str());
  env->DeleteLocalRef(error_class);
}

class JavaString final {
 public:
  JavaString(JNIEnv* env, jstring value, const char* field_name)
      : env_(env), value_(value) {
    if (value_ == nullptr) {
      throw std::invalid_argument(std::string(field_name) + " must not be null");
    }
    length_ = env_->GetStringLength(value_);
    chars_ = env_->GetStringChars(value_, nullptr);
    if (chars_ == nullptr) {
      throw std::runtime_error(std::string("cannot read ") + field_name);
    }
    try {
      std::u16string utf16;
      utf16.reserve(static_cast<std::size_t>(length_));
      for (jsize index = 0; index < length_; ++index) {
        utf16.push_back(static_cast<char16_t>(chars_[index]));
      }
      utf8_ = utf16_to_utf8(utf16);
      if (utf8_.find('\0') != std::string::npos) {
        throw std::invalid_argument(std::string(field_name) +
                                    " must not contain U+0000");
      }
    } catch (...) {
      env_->ReleaseStringChars(value_, chars_);
      chars_ = nullptr;
      throw;
    }
  }

  ~JavaString() {
    if (chars_ != nullptr) {
      env_->ReleaseStringChars(value_, chars_);
    }
  }

  JavaString(const JavaString&) = delete;
  JavaString& operator=(const JavaString&) = delete;

  const std::string& utf8() const { return utf8_; }

 private:
  JNIEnv* env_;
  jstring value_;
  const jchar* chars_ = nullptr;
  jsize length_ = 0;
  std::string utf8_;
};

std::shared_ptr<Session> require_session(jlong handle) {
  if (handle <= 0) {
    throw std::invalid_argument("native session handle must be positive");
  }
  std::scoped_lock lock(registry_mutex());
  const auto found = registry().find(handle);
  if (found == registry().end()) {
    throw std::logic_error("native session handle is unknown or already destroyed");
  }
  return found->second;
}

jstring new_string(JNIEnv* env, const std::string& value) {
  const auto utf16 = utf8_to_utf16(value);
  if (utf16.size() > static_cast<std::size_t>(std::numeric_limits<jsize>::max())) {
    throw std::overflow_error("string is too large for JNI");
  }
  std::vector<jchar> units;
  units.reserve(utf16.size());
  for (const auto unit : utf16) {
    units.push_back(static_cast<jchar>(unit));
  }
  return env->NewString(units.data(), static_cast<jsize>(units.size()));
}

jobjectArray encode_snapshot(JNIEnv* env,
                             bool handled,
                             const Snapshot& snapshot) {
  constexpr jsize kHeaderEntries = 3;
  const std::size_t entry_count =
      static_cast<std::size_t>(kHeaderEntries) + snapshot.candidates.size() * 2;
  if (entry_count > static_cast<std::size_t>(std::numeric_limits<jsize>::max())) {
    throw std::overflow_error("candidate snapshot is too large for a Java array");
  }

  jclass string_class = env->FindClass("java/lang/String");
  if (string_class == nullptr) {
    return nullptr;
  }
  jobjectArray result = env->NewObjectArray(static_cast<jsize>(entry_count),
                                             string_class, nullptr);
  env->DeleteLocalRef(string_class);
  if (result == nullptr) {
    return nullptr;
  }

  auto set = [&](jsize index, const std::string& value) {
    jstring item = new_string(env, value);
    if (item == nullptr) {
      return false;
    }
    env->SetObjectArrayElement(result, index, item);
    env->DeleteLocalRef(item);
    return !env->ExceptionCheck();
  };

  if (!set(0, handled ? "1" : "0") ||
      !set(1, snapshot.composition) ||
      !set(2, snapshot.commit)) {
    return nullptr;
  }

  jsize index = kHeaderEntries;
  for (const auto& candidate : snapshot.candidates) {
    if (!set(index++, candidate.text) || !set(index++, candidate.comment)) {
      return nullptr;
    }
  }
  return result;
}

template <typename Function, typename Result>
Result translate_exceptions(JNIEnv* env, Result fallback, Function&& function) {
  try {
    return std::forward<Function>(function)();
  } catch (const std::invalid_argument& error) {
    throw_java(env, "java/lang/IllegalArgumentException", error.what());
  } catch (const std::out_of_range& error) {
    throw_java(env, "java/lang/IndexOutOfBoundsException", error.what());
  } catch (const std::logic_error& error) {
    throw_java(env, "java/lang/IllegalStateException", error.what());
  } catch (const std::exception& error) {
    throw_java(env, "java/lang/RuntimeException", error.what());
  } catch (...) {
    throw_java(env, "java/lang/RuntimeException", "unknown native Rime failure");
  }
  return fallback;
}

}  // namespace
}  // namespace clipvault::rime_poc::jni

using clipvault::rime_poc::Bridge;
using clipvault::rime_poc::InitOptions;
using clipvault::rime_poc::LibrimeBackend;
using clipvault::rime_poc::jni::JavaString;
using clipvault::rime_poc::jni::Session;
using clipvault::rime_poc::jni::encode_snapshot;
using clipvault::rime_poc::jni::next_handle;
using clipvault::rime_poc::jni::registry;
using clipvault::rime_poc::jni::registry_mutex;
using clipvault::rime_poc::jni::require_session;
using clipvault::rime_poc::jni::translate_exceptions;

extern "C" JNIEXPORT jlong JNICALL
Java_org_clipvault_rime_poc_NativeRimeBridge_nativeCreate(
    JNIEnv* env,
    jclass,
    jstring shared_data_dir,
    jstring user_data_dir,
    jstring schema_id) {
  return translate_exceptions(env, static_cast<jlong>(0), [&] {
    JavaString shared(env, shared_data_dir, "shared_data_dir");
    JavaString user(env, user_data_dir, "user_data_dir");
    JavaString schema(env, schema_id, "schema_id");

    std::scoped_lock lock(registry_mutex());
    if (!registry().empty()) {
      throw std::logic_error(
          "the PoC permits only one active native Rime session");
    }

    auto bridge = std::make_unique<Bridge>(std::make_unique<LibrimeBackend>());
    bridge->initialize(
        InitOptions{shared.utf8(), user.utf8(), schema.utf8()});
    auto session = std::make_shared<Session>(std::move(bridge));

    const jlong handle = next_handle().fetch_add(1, std::memory_order_relaxed);
    if (handle <= 0) {
      throw std::overflow_error("native session handle space exhausted");
    }
    const auto inserted = registry().emplace(handle, std::move(session));
    if (!inserted.second) {
      throw std::logic_error("native session handle collision");
    }
    return handle;
  });
}

extern "C" JNIEXPORT jobjectArray JNICALL
Java_org_clipvault_rime_poc_NativeRimeBridge_nativeProcessKey(
    JNIEnv* env,
    jclass,
    jlong handle,
    jint keycode,
    jint mask) {
  return translate_exceptions(env, static_cast<jobjectArray>(nullptr), [&] {
    const auto session = require_session(handle);
    const auto result = session->bridge->process_key(keycode, mask);
    return encode_snapshot(env, result.handled, result.state);
  });
}

extern "C" JNIEXPORT jobjectArray JNICALL
Java_org_clipvault_rime_poc_NativeRimeBridge_nativeSelectCandidate(
    JNIEnv* env,
    jclass,
    jlong handle,
    jint index) {
  return translate_exceptions(env, static_cast<jobjectArray>(nullptr), [&] {
    if (index < 0) {
      throw std::invalid_argument("candidate index must be non-negative");
    }
    const auto session = require_session(handle);
    return encode_snapshot(
        env, true, session->bridge->select_candidate(static_cast<std::size_t>(index)));
  });
}

extern "C" JNIEXPORT jobjectArray JNICALL
Java_org_clipvault_rime_poc_NativeRimeBridge_nativeReset(
    JNIEnv* env,
    jclass,
    jlong handle) {
  return translate_exceptions(env, static_cast<jobjectArray>(nullptr), [&] {
    const auto session = require_session(handle);
    return encode_snapshot(env, true, session->bridge->reset());
  });
}

extern "C" JNIEXPORT void JNICALL
Java_org_clipvault_rime_poc_NativeRimeBridge_nativeDestroy(
    JNIEnv* env,
    jclass,
    jlong handle) {
  translate_exceptions(env, false, [&] {
    if (handle <= 0) {
      return false;
    }
    std::scoped_lock lock(registry_mutex());
    const auto found = registry().find(handle);
    if (found == registry().end()) {
      return false;
    }
    auto session = std::move(found->second);
    registry().erase(found);
    session->bridge->shutdown();
    return true;
  });
}
