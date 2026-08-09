#include <windows.h>

namespace {

constexpr wchar_t kWindowClass[] = L"ClipVaultTsfEvidenceEditor";
constexpr wchar_t kWindowTitle[] = L"ClipVault TSF Evidence Editor";

HWND Editor(HWND window) {
  return reinterpret_cast<HWND>(GetWindowLongPtrW(window, GWLP_USERDATA));
}

LRESULT CALLBACK WindowProcedure(HWND window, UINT message, WPARAM wparam,
                                 LPARAM lparam) {
  switch (message) {
    case WM_CREATE: {
      const HWND editor = CreateWindowExW(
          WS_EX_CLIENTEDGE, L"EDIT", L"",
          WS_CHILD | WS_VISIBLE | WS_TABSTOP | WS_VSCROLL | ES_LEFT |
              ES_MULTILINE | ES_AUTOVSCROLL | ES_WANTRETURN,
          0, 0, 0, 0, window, nullptr, GetModuleHandleW(nullptr), nullptr);
      if (editor == nullptr) return -1;
      SetWindowLongPtrW(window, GWLP_USERDATA,
                        reinterpret_cast<LONG_PTR>(editor));
      SendMessageW(editor, WM_SETFONT,
                   reinterpret_cast<WPARAM>(GetStockObject(DEFAULT_GUI_FONT)),
                   TRUE);
      SetFocus(editor);
      return 0;
    }
    case WM_SIZE: {
      const HWND editor = Editor(window);
      if (editor != nullptr) {
        MoveWindow(editor, 12, 12, LOWORD(lparam) - 24, HIWORD(lparam) - 24,
                   TRUE);
      }
      return 0;
    }
    case WM_SETFOCUS: {
      const HWND editor = Editor(window);
      if (editor != nullptr) SetFocus(editor);
      return 0;
    }
    case WM_DESTROY:
      PostQuitMessage(0);
      return 0;
    default:
      return DefWindowProcW(window, message, wparam, lparam);
  }
}

}  // namespace

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int show_command) {
  WNDCLASSEXW window_class{};
  window_class.cbSize = sizeof(window_class);
  window_class.hInstance = instance;
  window_class.lpfnWndProc = WindowProcedure;
  window_class.lpszClassName = kWindowClass;
  window_class.hCursor = LoadCursorW(nullptr, IDC_IBEAM);
  window_class.hbrBackground =
      reinterpret_cast<HBRUSH>(GetStockObject(WHITE_BRUSH));
  if (RegisterClassExW(&window_class) == 0) return 1;

  const HWND window = CreateWindowExW(
      0, kWindowClass, kWindowTitle, WS_OVERLAPPEDWINDOW, CW_USEDEFAULT,
      CW_USEDEFAULT, 720, 480, nullptr, nullptr, instance, nullptr);
  if (window == nullptr) return 2;

  ShowWindow(window, show_command);
  UpdateWindow(window);

  MSG message{};
  while (GetMessageW(&message, nullptr, 0, 0) > 0) {
    TranslateMessage(&message);
    DispatchMessageW(&message);
  }
  return static_cast<int>(message.wParam);
}
