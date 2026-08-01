#include "globals.h"
#include "text_service.h"

#include <new>

class ClassFactory final : public IClassFactory {
 public:
  ClassFactory() { ModuleAddRef(); }
  ~ClassFactory() { ModuleRelease(); }

  STDMETHODIMP QueryInterface(REFIID interface_id, void** object) override {
    if (object == nullptr) return E_INVALIDARG;
    *object = nullptr;
    if (IsEqualIID(interface_id, IID_IUnknown) ||
        IsEqualIID(interface_id, IID_IClassFactory)) {
      *object = static_cast<IClassFactory*>(this);
      AddRef();
      return S_OK;
    }
    return E_NOINTERFACE;
  }
  STDMETHODIMP_(ULONG) AddRef() override {
    return static_cast<ULONG>(InterlockedIncrement(&references_));
  }
  STDMETHODIMP_(ULONG) Release() override {
    const ULONG remaining = static_cast<ULONG>(InterlockedDecrement(&references_));
    if (remaining == 0) delete this;
    return remaining;
  }
  STDMETHODIMP CreateInstance(IUnknown* outer, REFIID interface_id,
                              void** object) override {
    if (outer != nullptr) return CLASS_E_NOAGGREGATION;
    auto* service = new (std::nothrow) TextService();
    if (service == nullptr) return E_OUTOFMEMORY;
    const HRESULT result = service->QueryInterface(interface_id, object);
    service->Release();
    return result;
  }
  STDMETHODIMP LockServer(BOOL lock) override {
    lock ? ModuleAddRef() : ModuleRelease();
    return S_OK;
  }

 private:
  volatile LONG references_ = 1;
};

HRESULT CreateClassFactory(REFIID interface_id, void** object) {
  auto* factory = new (std::nothrow) ClassFactory();
  if (factory == nullptr) return E_OUTOFMEMORY;
  const HRESULT result = factory->QueryInterface(interface_id, object);
  factory->Release();
  return result;
}
