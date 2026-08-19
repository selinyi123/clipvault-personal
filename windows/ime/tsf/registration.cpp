#include "globals.h"

#include <msctf.h>
#include <objbase.h>

#include <array>
#include <string>

namespace {

constexpr wchar_t kDescription[] = L"ClipVault Input v2";
constexpr LANGID kLanguage = MAKELANGID(LANG_CHINESE, SUBLANG_CHINESE_SIMPLIFIED);

std::wstring GuidText(REFGUID guid) {
  std::array<wchar_t, 40> value{};
  return StringFromGUID2(guid, value.data(), static_cast<int>(value.size())) > 0
             ? std::wstring(value.data())
             : std::wstring{};
}

HRESULT RegisterComServer() {
  std::array<wchar_t, 32768> module{};
  const DWORD length = GetModuleFileNameW(g_module_instance, module.data(),
                                           static_cast<DWORD>(module.size()));
  const auto clsid = GuidText(CLSID_ClipVaultTextService);
  if (length == 0 || length >= static_cast<DWORD>(module.size()) || clsid.empty())
    return E_FAIL;
  const std::wstring path = L"Software\\Classes\\CLSID\\" + clsid;
  HKEY key = nullptr;
  const LONG create_status = RegCreateKeyExW(HKEY_CURRENT_USER, path.c_str(), 0,
                                              nullptr, 0, KEY_WRITE, nullptr,
                                              &key, nullptr);
  if (create_status != ERROR_SUCCESS) return HRESULT_FROM_WIN32(create_status);
  const auto description_bytes = static_cast<DWORD>(sizeof(kDescription));
  LONG status = RegSetValueExW(key, nullptr, 0, REG_SZ,
                              reinterpret_cast<const BYTE*>(kDescription),
                              description_bytes);
  HKEY inproc = nullptr;
  if (status == ERROR_SUCCESS)
    status = RegCreateKeyExW(key, L"InprocServer32", 0, nullptr, 0, KEY_WRITE,
                             nullptr, &inproc, nullptr);
  if (status == ERROR_SUCCESS) {
    status = RegSetValueExW(inproc, nullptr, 0, REG_SZ,
                            reinterpret_cast<const BYTE*>(module.data()),
                            static_cast<DWORD>((length + 1) * sizeof(wchar_t)));
  }
  constexpr wchar_t kApartment[] = L"Apartment";
  if (status == ERROR_SUCCESS) {
    status = RegSetValueExW(inproc, L"ThreadingModel", 0, REG_SZ,
                            reinterpret_cast<const BYTE*>(kApartment),
                            static_cast<DWORD>(sizeof(kApartment)));
  }
  if (inproc != nullptr) RegCloseKey(inproc);
  RegCloseKey(key);
  return status == ERROR_SUCCESS ? S_OK : HRESULT_FROM_WIN32(status);
}

void UnregisterComServer() {
  const auto clsid = GuidText(CLSID_ClipVaultTextService);
  if (!clsid.empty()) {
    const std::wstring path = L"Software\\Classes\\CLSID\\" + clsid;
    RegDeleteTreeW(HKEY_CURRENT_USER, path.c_str());
  }
}

HRESULT RegisterTsfProfile() {
  ITfInputProcessorProfiles* profiles = nullptr;
  HRESULT result = CoCreateInstance(CLSID_TF_InputProcessorProfiles, nullptr,
                                    CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&profiles));
  if (FAILED(result)) return result;
  result = profiles->Register(CLSID_ClipVaultTextService);
  if (SUCCEEDED(result)) {
    const auto directory = ModuleDirectory();
    const std::wstring icon = directory + L"\\ClipVaultTextService.dll";
    result = profiles->AddLanguageProfile(
        CLSID_ClipVaultTextService, kLanguage, GUID_ClipVaultLanguageProfile,
        kDescription, static_cast<ULONG>(std::size(kDescription) - 1), icon.c_str(),
        static_cast<ULONG>(icon.size()), 0);
  }
  profiles->Release();
  if (FAILED(result)) return result;

  ITfCategoryMgr* categories = nullptr;
  result = CoCreateInstance(CLSID_TF_CategoryMgr, nullptr, CLSCTX_INPROC_SERVER,
                            IID_PPV_ARGS(&categories));
  if (FAILED(result)) return result;
  const GUID* category_ids[] = {&GUID_TFCAT_TIP_KEYBOARD,
                                &GUID_TFCAT_TIPCAP_IMMERSIVESUPPORT,
                                &GUID_TFCAT_TIPCAP_UIELEMENTENABLED,
                                &GUID_TFCAT_TIPCAP_SYSTRAYSUPPORT};
  for (const GUID* category : category_ids) {
    result = categories->RegisterCategory(CLSID_ClipVaultTextService, *category,
                                          CLSID_ClipVaultTextService);
    if (FAILED(result)) break;
  }
  categories->Release();
  return result;
}

void UnregisterTsfProfile() {
  ITfCategoryMgr* categories = nullptr;
  if (SUCCEEDED(CoCreateInstance(CLSID_TF_CategoryMgr, nullptr, CLSCTX_INPROC_SERVER,
                                 IID_PPV_ARGS(&categories)))) {
    const GUID* category_ids[] = {&GUID_TFCAT_TIP_KEYBOARD,
                                  &GUID_TFCAT_TIPCAP_IMMERSIVESUPPORT,
                                  &GUID_TFCAT_TIPCAP_UIELEMENTENABLED,
                                  &GUID_TFCAT_TIPCAP_SYSTRAYSUPPORT};
    for (const GUID* category : category_ids) {
      categories->UnregisterCategory(CLSID_ClipVaultTextService, *category,
                                     CLSID_ClipVaultTextService);
    }
    categories->Release();
  }
  ITfInputProcessorProfiles* profiles = nullptr;
  if (SUCCEEDED(CoCreateInstance(CLSID_TF_InputProcessorProfiles, nullptr,
                                 CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&profiles)))) {
    profiles->RemoveLanguageProfile(CLSID_ClipVaultTextService, kLanguage,
                                    GUID_ClipVaultLanguageProfile);
    profiles->Unregister(CLSID_ClipVaultTextService);
    profiles->Release();
  }
}

}  // namespace

extern "C" HRESULT __stdcall DllRegisterServer() {
  const HRESULT initialized = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
  const bool uninitialize = SUCCEEDED(initialized);
  HRESULT result = RegisterComServer();
  if (SUCCEEDED(result)) result = RegisterTsfProfile();
  if (FAILED(result)) {
    UnregisterTsfProfile();
    UnregisterComServer();
  }
  if (uninitialize) CoUninitialize();
  return result;
}

extern "C" HRESULT __stdcall DllUnregisterServer() {
  const HRESULT initialized = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
  const bool uninitialize = SUCCEEDED(initialized);
  UnregisterTsfProfile();
  UnregisterComServer();
  if (uninitialize) CoUninitialize();
  return S_OK;
}
