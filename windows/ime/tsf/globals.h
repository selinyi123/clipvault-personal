#pragma once

#include <windows.h>

#include <string>

extern HINSTANCE g_module_instance;
extern volatile long g_module_references;

extern const CLSID CLSID_ClipVaultTextService;
extern const GUID GUID_ClipVaultLanguageProfile;

void ModuleAddRef() noexcept;
void ModuleRelease() noexcept;
std::wstring ModuleDirectory();
