package com.clipvault.imeapp

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.Gravity
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import com.clipvault.app.ime.rime.RimeEngineFactory
import com.clipvault.app.ime.rime.RimeReadiness

/** Minimal local setup/settings surface for the standalone IME APK. */
class ClipVaultImeSetupActivity : Activity() {
    private val handler = Handler(Looper.getMainLooper())
    private var engineStatus: TextView? = null
    private val refreshStatus = object : Runnable {
        override fun run() {
            engineStatus?.text = engineStatusText()
            handler.postDelayed(this, 500L)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = "ClipVault IME 设置"
        RimeEngineFactory.prewarmAsync(this)

        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(20), dp(20), dp(32))
        }
        content.addView(TextView(this).apply {
            text = "ClipVault 本地中文输入法"
            textSize = 22f
            gravity = Gravity.CENTER_HORIZONTAL
        })
        content.addView(TextView(this).apply {
            text = "输入法 APK 不具备网络、短信或通知读取权限。ClipVault 候选仅从同一签名的 Runtime 获取已过滤快照，且从不发送按键或组字内容。"
            setPadding(0, dp(12), 0, dp(12))
        })

        engineStatus = TextView(this).also(content::addView)
        content.addView(Button(this).apply {
            text = "1. 打开系统输入法设置"
            isAllCaps = false
            setOnClickListener {
                startActivity(Intent(Settings.ACTION_INPUT_METHOD_SETTINGS))
            }
        })
        content.addView(Button(this).apply {
            text = "2. 选择 ClipVault 输入法"
            isAllCaps = false
            setOnClickListener {
                getSystemService(InputMethodManager::class.java)?.showInputMethodPicker()
            }
        })

        content.addView(EditText(this).apply {
            hint = "在这里输入 nihao 测试中文候选"
            isSingleLine = false
            minLines = 2
        })

        content.addView(CheckBox(this).apply {
            text = "按键振动反馈"
            isChecked = ImePreferences.hapticFeedbackEnabled(this@ClipVaultImeSetupActivity)
            setOnCheckedChangeListener { _, enabled ->
                if (!ImePreferences.setHapticFeedbackEnabled(this@ClipVaultImeSetupActivity, enabled)) {
                    Toast.makeText(context, "设置保存失败；为安全起见已关闭振动", Toast.LENGTH_SHORT).show()
                    isChecked = false
                }
            }
        })

        content.addView(TextView(this).apply {
            text = "敏感应用包名（每行一个）"
            textSize = 17f
            setPadding(0, dp(16), 0, dp(4))
        })
        content.addView(TextView(this).apply {
            text = "这些应用中自动启用无痕中文方案，并彻底隐藏 Runtime、剪贴板、Memory 与 OTP 候选。包名未知或策略读取失败时同样隐藏。"
        })
        val sensitivePackages = EditText(this).apply {
            minLines = 7
            setText(
                ImePreferences.readSensitivePackages(this@ClipVaultImeSetupActivity)
                    ?.sorted()
                    ?.joinToString("\n")
                    .orEmpty(),
            )
            hint = "com.example.bank"
        }
        content.addView(sensitivePackages)
        content.addView(Button(this).apply {
            text = "保存敏感应用名单"
            isAllCaps = false
            setOnClickListener {
                val parsed = ImePreferences.parsePackageList(sensitivePackages.text.toString())
                val saved = ImePreferences.writeSensitivePackages(
                    this@ClipVaultImeSetupActivity,
                    parsed,
                )
                Toast.makeText(
                    context,
                    if (saved) "已保存 ${parsed.size} 个敏感应用" else "保存失败；策略将安全关闭 ClipVault 候选",
                    Toast.LENGTH_SHORT,
                ).show()
            }
        })

        setContentView(ScrollView(this).apply { addView(content) })
    }

    override fun onResume() {
        super.onResume()
        handler.removeCallbacks(refreshStatus)
        handler.post(refreshStatus)
    }

    override fun onPause() {
        handler.removeCallbacks(refreshStatus)
        super.onPause()
    }

    private fun engineStatusText(): String = when (RimeEngineFactory.readiness()) {
        RimeReadiness.IDLE -> "中文引擎：尚未启动"
        RimeReadiness.WARMING -> "中文引擎：后台部署中…"
        RimeReadiness.READY -> "中文引擎：已就绪（后台部署 ${RimeEngineFactory.lastWarmupDurationMs()} ms）"
        RimeReadiness.FAILED -> "中文引擎：不可用，将安全降级为英文直输"
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
}
