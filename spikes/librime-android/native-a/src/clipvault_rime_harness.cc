#include <rime_api.h>

#include <cstdlib>
#include <iostream>
#include <string>

namespace {

static RIME_MODULE_LIST(kClipVaultHarnessModules, "default");

int Fail(const std::string& step) {
  std::cerr << "FAIL step=" << step << '\n';
  return EXIT_FAILURE;
}

bool IsCompositionEmpty(RimeApi* api, RimeSessionId session) {
  RIME_STRUCT(RimeContext, context);
  if (!api->get_context(session, &context)) return true;
  const bool empty = context.composition.length == 0 &&
                     (!context.composition.preedit ||
                      context.composition.preedit[0] == '\0');
  api->free_context(&context);
  return empty;
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 3) {
    std::cerr << "usage: clipvault_rime_harness SHARED_DIR USER_DIR\n";
    return EXIT_FAILURE;
  }

  const std::string shared_dir = argv[1];
  const std::string user_dir = argv[2];
  const std::string prebuilt_dir = shared_dir + "/build";
  const std::string staging_dir = user_dir + "/build";
  RIME_STRUCT(RimeTraits, traits);
  traits.shared_data_dir = shared_dir.c_str();
  traits.user_data_dir = user_dir.c_str();
  traits.prebuilt_data_dir = prebuilt_dir.c_str();
  traits.staging_dir = staging_dir.c_str();
  traits.distribution_name = "ClipVault native acceptance";
  traits.distribution_code_name = "clipvault_native_acceptance";
  traits.distribution_version = "2";
  traits.app_name = "rime.clipvault.native.acceptance";
  traits.modules = kClipVaultHarnessModules;
  traits.min_log_level = 3;
  traits.log_dir = "";

  RimeApi* api = rime_get_api();
  if (!api) return Fail("get_api");
  api->setup(&traits);
  api->initialize(&traits);
  if (api->start_maintenance(True)) api->join_maintenance_thread();

  const RimeSessionId session = api->create_session();
  if (!session) {
    api->finalize();
    return Fail("create_session");
  }
  if (!api->select_schema(session, "luna_pinyin")) {
    api->destroy_session(session);
    api->finalize();
    return Fail("select_schema");
  }
  if (!api->simulate_key_sequence(session, "nihao")) {
    api->destroy_session(session);
    api->finalize();
    return Fail("simulate_nihao");
  }

  RIME_STRUCT(RimeContext, context);
  if (!api->get_context(session, &context)) {
    api->destroy_session(session);
    api->finalize();
    return Fail("get_context");
  }

  int expected_index = -1;
  std::string preedit = context.composition.preedit
                            ? context.composition.preedit
                            : "";
  for (int index = 0; index < context.menu.num_candidates; ++index) {
    const char* text = context.menu.candidates[index].text;
    if (text && std::string(text) == "你好") {
      expected_index = index;
      break;
    }
  }
  std::cout << "SNAPSHOT preedit=" << preedit
            << " candidates=" << context.menu.num_candidates
            << " expected_index=" << expected_index << '\n';
  api->free_context(&context);
  if (preedit.empty() || expected_index < 0) {
    api->destroy_session(session);
    api->finalize();
    return Fail("nihao_candidate");
  }

  if (!api->select_candidate_on_current_page(
          session, static_cast<size_t>(expected_index))) {
    api->destroy_session(session);
    api->finalize();
    return Fail("select_candidate");
  }
  RIME_STRUCT(RimeCommit, commit);
  if (!api->get_commit(session, &commit)) {
    api->destroy_session(session);
    api->finalize();
    return Fail("get_commit");
  }
  const std::string committed = commit.text ? commit.text : "";
  api->free_commit(&commit);
  std::cout << "COMMIT text=" << committed << '\n';
  if (committed != "你好") {
    api->destroy_session(session);
    api->finalize();
    return Fail("commit_value");
  }

  if (!api->simulate_key_sequence(session, "nihao")) {
    api->destroy_session(session);
    api->finalize();
    return Fail("simulate_before_reset");
  }
  api->clear_composition(session);
  if (!IsCompositionEmpty(api, session)) {
    api->destroy_session(session);
    api->finalize();
    return Fail("reset");
  }
  std::cout << "RESET composition=empty\n";

  api->destroy_session(session);
  api->finalize();
  std::cout << "PASS vector=nihao-select-reset\n";
  return EXIT_SUCCESS;
}
