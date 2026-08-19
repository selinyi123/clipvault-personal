#include "globals.h"

#include <array>
#include <string>

HINSTANCE g_module_instance = nullptr;
volatile long g_module_references = 0;

// {C5CEE00A-05AD-4ABA-93BB-6E76932AF126}
const CLSID CLSID_ClipVaultTextService = {
    0xc5cee00a, 0x05ad, 0x4aba, {0x93, 0xbb, 0x6e, 0x76, 0x93, 0x2a, 0xf1, 0x26}};

// {6A476B5D-8B94-4466-BF30-04CC62492491}
const GUID GUID_ClipVaultLanguageProfile = {
    0x6a476b5d, 0x8b94, 0x4466, {0xbf, 0x30, 0x04, 0xcc, 0x62, 0x49, 0x24, 0x91}};

void ModuleAddRef() noexcept { InterlockedIncrement(&g_module_references); }
void ModuleRelease() noexcept { InterlockedDecrement(&g_module_references); }

std::wstring ModuleDirectory() {
  std::array<wchar_t, 32768> path{};
  const DWORD length = GetModuleFileNameW(g_module_instance, path.data(),
                                           static_cast<DWORD>(path.size()));
  if (length == 0 || length >= path.size()) return {};
  std::wstring value(path.data(), length);
  const auto separator = value.find_last_of(L"\\/");
  return separator == std::wstring::npos ? std::wstring{} : value.substr(0, separator);
}
