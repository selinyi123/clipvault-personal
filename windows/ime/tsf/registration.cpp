#include "globals.h"

#include <msctf.h>
#include <objbase.h>

#include <array>
#include <string>

namespace {

constexpr wchar_t kDescription[] = L"ClipVault Input v2";
constexpr LANGID kLanguage = MAKELANGID(LANG_CHINESE, SUBLANG_CHINESE_SIMPLIFIED);

#ifndef CLIPVAULT_TSF_PROFILE_OWNER
#define CLIPVAULT_TSF_PROFILE_OWNER 0
#endif

constexpr REGSAM ComRegistryView() {
#if defined(_WIN64)
  return KEY_WOW64_64KEY;
#else
  return KEY_WOW64_32KEY;
#endif
}

void RememberFailure(HRESULT candidate, HRESULT* first_failure) {
  if (FAILED(candidate) && SUCCEEDED(*first_failure)) *first_failure = candidate;
}

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
  const LONG create_status = RegCreateKeyExW(
      HKEY_LOCAL_MACHINE, path.c_str(), 0, nullptr, 0,
      KEY_WRITE | ComRegistryView(), nullptr, &key, nullptr);
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

HRESULT UnregisterComServer() {
  const auto clsid = GuidText(CLSID_ClipVaultTextService);
  if (clsid.empty()) return E_FAIL;
  HKEY classes = nullptr;
  LONG status = RegOpenKeyExW(HKEY_LOCAL_MACHINE,
                              L"Software\\Classes\\CLSID", 0,
                              KEY_WRITE | ComRegistryView(), &classes);
  if (status == ERROR_FILE_NOT_FOUND || status == ERROR_PATH_NOT_FOUND)
    return S_OK;
  if (status != ERROR_SUCCESS) return HRESULT_FROM_WIN32(status);
  status = RegDeleteTreeW(classes, clsid.c_str());
  RegCloseKey(classes);
  if (status == ERROR_FILE_NOT_FOUND || status == ERROR_PATH_NOT_FOUND)
    return S_OK;
  return status == ERROR_SUCCESS ? S_OK : HRESULT_FROM_WIN32(status);
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
  if (SUCCEEDED(result)) {
    result = profiles->EnableLanguageProfile(
        CLSID_ClipVaultTextService, kLanguage, GUID_ClipVaultLanguageProfile,
        TRUE);
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

HRESULT UnregisterTsfProfile() {
  HRESULT first_failure = S_OK;
  ITfCategoryMgr* categories = nullptr;
  HRESULT result = CoCreateInstance(CLSID_TF_CategoryMgr, nullptr,
                                    CLSCTX_INPROC_SERVER,
                                    IID_PPV_ARGS(&categories));
  if (SUCCEEDED(result)) {
    const GUID* category_ids[] = {&GUID_TFCAT_TIP_KEYBOARD,
                                  &GUID_TFCAT_TIPCAP_IMMERSIVESUPPORT,
                                  &GUID_TFCAT_TIPCAP_UIELEMENTENABLED,
                                  &GUID_TFCAT_TIPCAP_SYSTRAYSUPPORT};
    for (const GUID* category : category_ids) {
      RememberFailure(categories->UnregisterCategory(
                          CLSID_ClipVaultTextService, *category,
                          CLSID_ClipVaultTextService),
                      &first_failure);
    }
    categories->Release();
  } else {
    RememberFailure(result, &first_failure);
  }
  ITfInputProcessorProfiles* profiles = nullptr;
  result = CoCreateInstance(CLSID_TF_InputProcessorProfiles, nullptr,
                            CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&profiles));
  if (SUCCEEDED(result)) {
    RememberFailure(profiles->EnableLanguageProfile(
                        CLSID_ClipVaultTextService, kLanguage,
                        GUID_ClipVaultLanguageProfile, FALSE),
                    &first_failure);
    RememberFailure(profiles->RemoveLanguageProfile(
                        CLSID_ClipVaultTextService, kLanguage,
                        GUID_ClipVaultLanguageProfile),
                    &first_failure);
    RememberFailure(profiles->Unregister(CLSID_ClipVaultTextService),
                    &first_failure);
    profiles->Release();
  } else {
    RememberFailure(result, &first_failure);
  }
  return first_failure;
}

}  // namespace

extern "C" HRESULT __stdcall DllRegisterServer() {
  const HRESULT initialized = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
  const bool uninitialize = SUCCEEDED(initialized);
  HRESULT result = RegisterComServer();
#if CLIPVAULT_TSF_PROFILE_OWNER
  if (SUCCEEDED(result)) result = RegisterTsfProfile();
#endif
  if (FAILED(result)) {
#if CLIPVAULT_TSF_PROFILE_OWNER
    UnregisterTsfProfile();
#endif
    UnregisterComServer();
  }
  if (uninitialize) CoUninitialize();
  return result;
}

extern "C" HRESULT __stdcall DllUnregisterServer() {
  const HRESULT initialized = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
  const bool uninitialize = SUCCEEDED(initialized);
  HRESULT result = S_OK;
#if CLIPVAULT_TSF_PROFILE_OWNER
  RememberFailure(UnregisterTsfProfile(), &result);
#endif
  RememberFailure(UnregisterComServer(), &result);
  if (uninitialize) CoUninitialize();
  return result;
}
