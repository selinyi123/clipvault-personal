#include "globals.h"

#include <objbase.h>

HRESULT CreateClassFactory(REFIID interface_id, void** object);

BOOL APIENTRY DllMain(HINSTANCE module, DWORD reason, LPVOID) {
  if (reason == DLL_PROCESS_ATTACH) {
    g_module_instance = module;
    DisableThreadLibraryCalls(module);
  }
  return TRUE;
}

extern "C" HRESULT __stdcall DllCanUnloadNow() {
  return g_module_references == 0 ? S_OK : S_FALSE;
}

extern "C" HRESULT __stdcall DllGetClassObject(REFCLSID class_id,
                                                REFIID interface_id,
                                                void** object) {
  if (!IsEqualCLSID(class_id, CLSID_ClipVaultTextService))
    return CLASS_E_CLASSNOTAVAILABLE;
  return CreateClassFactory(interface_id, object);
}
