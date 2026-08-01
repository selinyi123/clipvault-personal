package com.clipvault.app.otp

import android.Manifest
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class OtpRelaySettingsActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { MaterialTheme { OtpSettingsScreen() } }
    }
}

@Composable
private fun OtpSettingsScreen() {
    val context = androidx.compose.ui.platform.LocalContext.current
    val scope = rememberCoroutineScope()
    var refresh by remember { mutableStateOf(0) }
    var status by remember { mutableStateOf(OtpRelayRuntime.status(context)) }
    var message by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var confirmForget by remember { mutableStateOf(false) }
    val lifecycleOwner = LocalLifecycleOwner.current

    fun reload() {
        status = OtpRelayRuntime.status(context)
        refresh++
    }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        val armed = granted && OtpRelayRuntime.authorizeApprovedSms(context)
        message = if (armed) "本次进程已授权；锁屏、超时或进程退出会自动撤权。"
        else "授权未建立，OTP Relay 保持关闭。"
        reload()
    }

    LaunchedEffect(refresh) { status = OtpRelayRuntime.status(context) }
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) reload()
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    Column(
        Modifier.fillMaxSize().padding(18.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("OTP Relay", style = MaterialTheme.typography.headlineSmall)
        Text(
            "验证码走独立内存通道，不进入剪贴板、Room、普通同步 outbox 或日志。" +
                " 默认关闭；自动短信捕获仅存在于通过审核的 otpSmsRelay 构建。",
        )
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(if (status.paired) "安全配对：已建立" else "安全配对：未建立")
                Text(if (status.activeGrant) "捕获授权：当前有效" else "捕获授权：关闭")
                Text(
                    if (status.userConsentSessionActive) "逐条确认会话：正在等待"
                    else "逐条确认会话：关闭",
                )
                Text("已预留发送序号：${status.highSequence}")
                Text(
                    if (status.smsCaptureIncluded) "短信捕获组件：本构建包含（仍需系统权限和 Play 审核）"
                    else "短信捕获组件：默认构建未包含",
                )
            }
        }
        Button(
            enabled = !busy && !status.paired,
            onClick = {
                busy = true
                scope.launch {
                    val result = withContext(Dispatchers.IO) { OtpRelayRuntime.pair(context) }
                    message = when (result) {
                        OtpPairingStatus.PAIRED -> "OTP 安全配对已一次性导入 Android Keystore。"
                        OtpPairingStatus.ALREADY_PAIRED -> "OTP 安全配对已存在。"
                        OtpPairingStatus.OFFLINE -> "电脑不在线，未创建持久队列。"
                        OtpPairingStatus.REJECTED -> "配对被拒绝；请确认同步配对、Tailscale 与 Desktop OTP Pair Authority。"
                    }
                    busy = false
                    reload()
                }
            },
        ) { Text("建立 OTP 安全配对") }

        Button(
            enabled = !busy && status.paired && !status.activeGrant &&
                !status.userConsentSessionActive,
            onClick = {
                val session = OtpRelayRuntime.beginUserConsentSession(context)
                if (session == null) {
                    message = "无法建立逐条确认会话；请确认设备已解锁且配对仍有效。"
                } else {
                    context.startActivity(OtpUserConsentActivity.intent(context, session))
                    message = "正在等待下一条验证码；收到后请在系统对话框中逐条确认。"
                }
                reload()
            },
        ) { Text("等待并确认下一条短信验证码") }

        Button(
            enabled = !busy && status.paired && status.smsCaptureIncluded && !status.activeGrant,
            onClick = {
                if (status.smsPermissionGranted) {
                    val armed = OtpRelayRuntime.authorizeApprovedSms(context)
                    message = if (armed) "已显式授权本次进程。" else "授权失败，保持关闭。"
                    reload()
                } else {
                    permissionLauncher.launch(Manifest.permission.RECEIVE_SMS)
                }
            },
        ) { Text("授权自动 OTP 中继（最长 8 小时）") }

        OutlinedButton(
            enabled = status.activeGrant,
            onClick = {
                OtpRelayRuntime.revokeCapture()
                message = "捕获授权与内存状态已清除。"
                reload()
            },
        ) { Text("立即撤销捕获授权") }

        TextButton(enabled = status.paired, onClick = { confirmForget = true }) {
            Text("忘记本机 OTP 配对")
        }
        if (message.isNotBlank()) Text(message, style = MaterialTheme.typography.bodySmall)
        Spacer(Modifier.height(4.dp))
        Text(
            "Notification Listener 未注册；Android 15 会隐藏不受信任监听器中的 OTP。" +
                " SMS User Consent 默认关闭，只在点击按钮后的短期会话内等待一条短信，" +
                "且短信正文只有在系统逐条确认后才交给 Runtime。",
            style = MaterialTheme.typography.bodySmall,
        )
    }

    if (confirmForget) {
        AlertDialog(
            onDismissRequest = { confirmForget = false },
            title = { Text("忘记 OTP 配对？") },
            text = { Text("本机 Keystore 凭据、序号和 nonce 历史将删除。Desktop 端仍需撤销对应设备后才能重新配对。") },
            confirmButton = {
                Button(onClick = {
                    val cleared = OtpRelayRuntime.forgetPair()
                    message = if (cleared) "本机 OTP 配对已清除。" else "本机清除失败，保持关闭。"
                    confirmForget = false
                    reload()
                }) { Text("确认清除") }
            },
            dismissButton = {
                TextButton(onClick = { confirmForget = false }) { Text("取消") }
            },
        )
    }
}
