#include <jni.h>
#include <rime_api.h>

#include <algorithm>
#include <cstdint>
#include <mutex>
#include <string>
#include <string_view>
#include <vector>

namespace {

std::mutex g_mutex;
bool g_initialized = false;
std::string g_shared_dir;
std::string g_user_dir;
std::string g_prebuilt_dir;
std::string g_staging_dir;

static RIME_MODULE_LIST(kClipVaultModules, "default");

class UtfChars final {
 public:
  UtfChars(JNIEnv* env, jstring value) : env_(env), value_(value) {
    if (value_) chars_ = env_->GetStringUTFChars(value_, nullptr);
  }
  ~UtfChars() {
    if (chars_) env_->ReleaseStringUTFChars(value_, chars_);
  }
  const char* get() const { return chars_; }

 private:
  JNIEnv* env_;
  jstring value_;
  const char* chars_ = nullptr;
};

std::u16string Utf8ToUtf16(std::string_view input) {
  std::u16string output;
  output.reserve(input.size());
  for (size_t index = 0; index < input.size();) {
    const unsigned char lead = static_cast<unsigned char>(input[index]);
    uint32_t code_point = 0;
    size_t width = 0;
    if (lead < 0x80) {
      code_point = lead;
      width = 1;
    } else if ((lead & 0xe0) == 0xc0) {
      code_point = lead & 0x1f;
      width = 2;
    } else if ((lead & 0xf0) == 0xe0) {
      code_point = lead & 0x0f;
      width = 3;
    } else if ((lead & 0xf8) == 0xf0) {
      code_point = lead & 0x07;
      width = 4;
    }
    bool valid = width != 0 && index + width <= input.size();
    for (size_t offset = 1; valid && offset < width; ++offset) {
      const unsigned char continuation =
          static_cast<unsigned char>(input[index + offset]);
      valid = (continuation & 0xc0) == 0x80;
      code_point = (code_point << 6) | (continuation & 0x3f);
    }
    if (valid) {
      valid = (width == 1 || code_point >= 0x80) &&
              (width <= 2 || code_point >= 0x800) &&
              (width <= 3 || code_point >= 0x10000) &&
              code_point <= 0x10ffff &&
              !(code_point >= 0xd800 && code_point <= 0xdfff);
    }
    if (!valid) {
      output.push_back(u'\ufffd');
      ++index;
      continue;
    }
    if (code_point <= 0xffff) {
      output.push_back(static_cast<char16_t>(code_point));
    } else {
      code_point -= 0x10000;
      output.push_back(static_cast<char16_t>(0xd800 + (code_point >> 10)));
      output.push_back(static_cast<char16_t>(0xdc00 + (code_point & 0x3ff)));
    }
    index += width;
  }
  return output;
}

jstring MakeString(JNIEnv* env, std::string_view utf8) {
  const std::u16string utf16 = Utf8ToUtf16(utf8);
  return env->NewString(reinterpret_cast<const jchar*>(utf16.data()),
                        static_cast<jsize>(utf16.size()));
}

jobjectArray MakeStringArray(JNIEnv* env, const std::vector<std::string>& values) {
  jclass string_class = env->FindClass("java/lang/String");
  if (!string_class) return nullptr;
  jobjectArray result = env->NewObjectArray(values.size(), string_class, nullptr);
  env->DeleteLocalRef(string_class);
  if (!result) return nullptr;
  for (jsize index = 0; index < static_cast<jsize>(values.size()); ++index) {
    jstring value = MakeString(env, values[index]);
    if (!value) return nullptr;
    env->SetObjectArrayElement(result, index, value);
    env->DeleteLocalRef(value);
    if (env->ExceptionCheck()) return nullptr;
  }
  return result;
}

RimeSessionId ToSession(jlong value) {
  return static_cast<RimeSessionId>(static_cast<uintptr_t>(value));
}

}  // namespace

extern "C" JNIEXPORT jboolean JNICALL
Java_com_clipvault_app_ime_rime_NativeRimeBridge_nativeInitialize(
    JNIEnv* env, jobject, jstring shared_dir, jstring user_dir) {
  std::lock_guard<std::mutex> lock(g_mutex);
  if (g_initialized) return JNI_TRUE;

  UtfChars shared(env, shared_dir);
  UtfChars user(env, user_dir);
  if (!shared.get() || !user.get()) return JNI_FALSE;
  g_shared_dir = shared.get();
  g_user_dir = user.get();
  g_prebuilt_dir = g_shared_dir + "/build";
  g_staging_dir = g_user_dir + "/build";

  RIME_STRUCT(RimeTraits, traits);
  traits.shared_data_dir = g_shared_dir.c_str();
  traits.user_data_dir = g_user_dir.c_str();
  traits.prebuilt_data_dir = g_prebuilt_dir.c_str();
  traits.staging_dir = g_staging_dir.c_str();
  traits.distribution_name = "ClipVault";
  traits.distribution_code_name = "clipvault";
  traits.distribution_version = "2";
  traits.app_name = "rime.clipvault";
  traits.modules = kClipVaultModules;
  traits.min_log_level = 3;
  traits.log_dir = "";

  RimeApi* api = rime_get_api();
  if (!api) return JNI_FALSE;
  api->setup(&traits);
  api->initialize(&traits);
  if (api->start_maintenance(False)) api->join_maintenance_thread();
  g_initialized = true;
  return JNI_TRUE;
}

extern "C" JNIEXPORT jlong JNICALL
Java_com_clipvault_app_ime_rime_NativeRimeBridge_nativeCreateSession(
    JNIEnv*, jobject) {
  std::lock_guard<std::mutex> lock(g_mutex);
  if (!g_initialized) return 0;
  return static_cast<jlong>(rime_get_api()->create_session());
}

extern "C" JNIEXPORT jboolean JNICALL
Java_com_clipvault_app_ime_rime_NativeRimeBridge_nativeSelectSchema(
    JNIEnv* env, jobject, jlong session, jstring schema_id) {
  std::lock_guard<std::mutex> lock(g_mutex);
  if (!g_initialized || session == 0) return JNI_FALSE;
  UtfChars schema(env, schema_id);
  if (!schema.get()) return JNI_FALSE;
  return rime_get_api()->select_schema(ToSession(session), schema.get()) ? JNI_TRUE
                                                                        : JNI_FALSE;
}

extern "C" JNIEXPORT void JNICALL
Java_com_clipvault_app_ime_rime_NativeRimeBridge_nativeSetOption(
    JNIEnv* env, jobject, jlong session, jstring option, jboolean enabled) {
  std::lock_guard<std::mutex> lock(g_mutex);
  if (!g_initialized || session == 0) return;
  UtfChars option_name(env, option);
  if (!option_name.get()) return;
  rime_get_api()->set_option(ToSession(session), option_name.get(),
                             enabled ? True : False);
}

extern "C" JNIEXPORT jboolean JNICALL
Java_com_clipvault_app_ime_rime_NativeRimeBridge_nativeProcessKey(
    JNIEnv*, jobject, jlong session, jint keycode, jint mask) {
  std::lock_guard<std::mutex> lock(g_mutex);
  if (!g_initialized || session == 0) return JNI_FALSE;
  return rime_get_api()->process_key(ToSession(session), keycode, mask) ? JNI_TRUE
                                                                        : JNI_FALSE;
}

extern "C" JNIEXPORT jobjectArray JNICALL
Java_com_clipvault_app_ime_rime_NativeRimeBridge_nativeSnapshot(
    JNIEnv* env, jobject, jlong session) {
  std::lock_guard<std::mutex> lock(g_mutex);
  std::vector<std::string> values;
  values.emplace_back("");
  values.emplace_back("0");
  values.emplace_back("0");
  values.emplace_back("1");
  if (!g_initialized || session == 0) return MakeStringArray(env, values);

  RIME_STRUCT(RimeContext, context);
  RimeApi* api = rime_get_api();
  if (!api->get_context(ToSession(session), &context)) {
    return MakeStringArray(env, values);
  }
  values[0] = context.composition.preedit ? context.composition.preedit : "";
  const std::string preedit = context.composition.preedit
                                  ? context.composition.preedit
                                  : "";
  const size_t caret_bytes = std::min(
      static_cast<size_t>(std::max(context.composition.cursor_pos, 0)),
      preedit.size());
  values[1] =
      std::to_string(Utf8ToUtf16(std::string_view(preedit).substr(0, caret_bytes)).size());
  values[2] = std::to_string(context.menu.page_no);
  values[3] = context.menu.is_last_page ? "1" : "0";
  for (int index = 0; index < context.menu.num_candidates; ++index) {
    const RimeCandidate& candidate = context.menu.candidates[index];
    values.emplace_back(candidate.text ? candidate.text : "");
    values.emplace_back(candidate.comment ? candidate.comment : "");
  }
  api->free_context(&context);
  return MakeStringArray(env, values);
}

extern "C" JNIEXPORT jstring JNICALL
Java_com_clipvault_app_ime_rime_NativeRimeBridge_nativeTakeCommit(
    JNIEnv* env, jobject, jlong session) {
  std::lock_guard<std::mutex> lock(g_mutex);
  if (!g_initialized || session == 0) return nullptr;
  RIME_STRUCT(RimeCommit, commit);
  RimeApi* api = rime_get_api();
  if (!api->get_commit(ToSession(session), &commit)) return nullptr;
  jstring result = commit.text ? MakeString(env, commit.text) : nullptr;
  api->free_commit(&commit);
  return result;
}

extern "C" JNIEXPORT jboolean JNICALL
Java_com_clipvault_app_ime_rime_NativeRimeBridge_nativeSelectCandidate(
    JNIEnv*, jobject, jlong session, jint index_on_page) {
  std::lock_guard<std::mutex> lock(g_mutex);
  if (!g_initialized || session == 0 || index_on_page < 0) return JNI_FALSE;
  return rime_get_api()->select_candidate_on_current_page(
             ToSession(session), static_cast<size_t>(index_on_page))
             ? JNI_TRUE
             : JNI_FALSE;
}

extern "C" JNIEXPORT jboolean JNICALL
Java_com_clipvault_app_ime_rime_NativeRimeBridge_nativeCommitComposition(
    JNIEnv*, jobject, jlong session) {
  std::lock_guard<std::mutex> lock(g_mutex);
  if (!g_initialized || session == 0) return JNI_FALSE;
  return rime_get_api()->commit_composition(ToSession(session)) ? JNI_TRUE
                                                                : JNI_FALSE;
}

extern "C" JNIEXPORT void JNICALL
Java_com_clipvault_app_ime_rime_NativeRimeBridge_nativeClearComposition(
    JNIEnv*, jobject, jlong session) {
  std::lock_guard<std::mutex> lock(g_mutex);
  if (g_initialized && session != 0) {
    rime_get_api()->clear_composition(ToSession(session));
  }
}

extern "C" JNIEXPORT void JNICALL
Java_com_clipvault_app_ime_rime_NativeRimeBridge_nativeDestroySession(
    JNIEnv*, jobject, jlong session) {
  std::lock_guard<std::mutex> lock(g_mutex);
  if (g_initialized && session != 0) {
    rime_get_api()->destroy_session(ToSession(session));
  }
}

extern "C" JNIEXPORT void JNICALL
Java_com_clipvault_app_ime_rime_NativeRimeBridge_nativeFinalize(
    JNIEnv*, jobject) {
  std::lock_guard<std::mutex> lock(g_mutex);
  if (!g_initialized) return;
  rime_get_api()->finalize();
  g_initialized = false;
  g_shared_dir.clear();
  g_user_dir.clear();
  g_prebuilt_dir.clear();
  g_staging_dir.clear();
}
