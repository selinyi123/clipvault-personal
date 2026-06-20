package com.clipvault.app.ui

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.provider.Settings as OsSettings
import android.view.inputmethod.InputMethodManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.clipvault.app.ClipVaultApp
import com.clipvault.app.data.ClipEntity
import com.clipvault.app.sync.Settings
import com.clipvault.app.sync.SyncClient
import com.clipvault.app.sync.SyncScheduler
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** History, search, pairing AND keyboard setup. Capture happens via Share Target /
 * QS Tile / IME — Android forbids background clipboard reads, so there is no auto-watch. */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        SyncScheduler.schedulePeriodic(this)
        setContent { MaterialTheme { Home() } }
    }
}

private fun imeEnabled(ctx: Context): Boolean {
    val imm = ctx.getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
    return imm.enabledInputMethodList.any { it.packageName == ctx.packageName }
}

private fun imeSelected(ctx: Context): Boolean {
    val cur = OsSettings.Secure.getString(ctx.contentResolver, OsSettings.Secure.DEFAULT_INPUT_METHOD)
    return cur?.startsWith(ctx.packageName + "/") == true
}

private fun isPaired(ctx: Context): Boolean {
    val s = Settings(ctx)
    return !s.host.isNullOrBlank() && !s.token.isNullOrBlank()
}

/** Open the system input-method settings, falling back to general Settings so a
 * stripped ROM (no IME settings activity) never crashes the app. */
private fun openImeSettings(ctx: Context) {
    val intents = listOf(
        Intent(OsSettings.ACTION_INPUT_METHOD_SETTINGS),
        Intent(OsSettings.ACTION_SETTINGS),
    )
    for (i in intents) {
        try {
            ctx.startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); return
        } catch (_: Exception) { /* try the next fallback */ }
    }
    android.widget.Toast.makeText(ctx, "请到系统设置 → 语言和输入法 启用 ClipVault 键盘",
        android.widget.Toast.LENGTH_LONG).show()
}

private fun switchKeyboard(ctx: Context) {
    try {
        (ctx.getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager)
            .showInputMethodPicker()
    } catch (_: Exception) {
        android.widget.Toast.makeText(ctx, "在任意输入框点右下角键盘图标切换",
            android.widget.Toast.LENGTH_LONG).show()
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Home() {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val scope = rememberCoroutineScope()
    var query by remember { mutableStateOf("") }
    var showSecret by remember { mutableStateOf(false) }
    var clips by remember { mutableStateOf(listOf<ClipEntity>()) }
    var pairing by remember { mutableStateOf(false) }

    // Re-check setup status whenever we return to the app (e.g. from IME settings).
    var statusKey by remember { mutableStateOf(0) }
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val obs = LifecycleEventObserver { _, e -> if (e == Lifecycle.Event.ON_RESUME) statusKey++ }
        lifecycleOwner.lifecycle.addObserver(obs)
        onDispose { lifecycleOwner.lifecycle.removeObserver(obs) }
    }
    val enabled = remember(statusKey) { imeEnabled(ctx) }
    val selected = remember(statusKey) { imeSelected(ctx) }
    val paired = remember(statusKey, pairing) { isPaired(ctx) }

    fun refresh() = scope.launch {
        clips = withContext(Dispatchers.IO) {
            ClipVaultApp.db(ctx).clips().list(query.trim(), if (showSecret) 1 else 0)
        }
    }
    LaunchedEffect(query, showSecret) { refresh() }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("ClipVault Personal") },
                actions = { TextButton(onClick = { pairing = true }) { Text("配对") } },
            )
        }
    ) { pad ->
        Column(Modifier.padding(pad).padding(12.dp)) {
            SetupCard(
                enabled = enabled, selected = selected, paired = paired,
                onEnable = { openImeSettings(ctx) },
                onSwitch = { switchKeyboard(ctx) },
                onPair = { pairing = true },
            )
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(query, { query = it }, Modifier.fillMaxWidth(),
                label = { Text("搜索剪切板历史") }, singleLine = true)
            Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                Switch(showSecret, { showSecret = it })
                Text("显示隔离区")
            }
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(clips) { c -> ClipCard(c) }
            }
        }
    }

    if (pairing) PairDialog(onDismiss = { pairing = false; statusKey++ })
}

@Composable
private fun SetupCard(
    enabled: Boolean, selected: Boolean, paired: Boolean,
    onEnable: () -> Unit, onSwitch: () -> Unit, onPair: () -> Unit,
) {
    var expanded by remember { mutableStateOf(true) }
    val allDone = enabled && paired
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(14.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                Text(if (allDone) "✅ ClipVault 输入法已就绪" else "开始使用 ClipVault 键盘",
                    style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                TextButton(onClick = { expanded = !expanded }) { Text(if (expanded) "收起" else "展开") }
            }
            if (expanded) {
                Spacer(Modifier.height(8.dp))
                Step(1, "启用 ClipVault 输入法", enabled,
                    "在系统设置里把 ClipVault 键盘打开（一次性）。", "去启用", onEnable)
                Step(2, "切换到 ClipVault 键盘", selected,
                    "在任意输入框点右下角键盘图标，选 ClipVault。用完切回你常用的输入法。",
                    "切换键盘", onSwitch)
                Step(3, "配对桌面（可选）", paired,
                    "想看电脑同步的剪切板/词库？在电脑面板点「配对设备」拿一次性码。", "配对", onPair)
                Spacer(Modifier.height(8.dp))
                Text("它是什么：ClipVault 是一个「面板键盘」——切到它，面板里有最近剪切板、" +
                    "电脑同步内容、常用词/短语/Prompt/命令，点一下直接粘贴；不是用来打字的，" +
                    "是用来快速调取你存过的内容的。",
                    style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
private fun Step(n: Int, title: String, done: Boolean, hint: String,
                 action: String, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp),
        verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
        Text(if (done) "✅" else "$n.", Modifier.width(28.dp))
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge,
                fontWeight = if (done) FontWeight.Normal else FontWeight.Medium)
            Text(hint, style = MaterialTheme.typography.bodySmall)
        }
        if (!done) Button(onClick = onClick) { Text(action) }
        else TextButton(onClick = onClick) { Text(action) }
    }
}

@Composable
private fun ClipCard(c: ClipEntity) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp)) {
            Text("${c.contentType}${if (c.isSecret) " · 隔离(${c.secretLevel})" else ""}",
                style = MaterialTheme.typography.labelSmall)
            Spacer(Modifier.height(4.dp))
            Text(if (c.isSecret) c.content.take(4) + "••••" else c.content,
                style = MaterialTheme.typography.bodyMedium, maxLines = 6)
        }
    }
}

@Composable
private fun PairDialog(onDismiss: () -> Unit) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val scope = rememberCoroutineScope()
    var host by remember { mutableStateOf(Settings(ctx).host ?: "") }
    var code by remember { mutableStateOf("") }
    var msg by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("配对桌面") },
        text = {
            Column {
                Text("在电脑的 ClipVault 面板点「配对设备」，把显示的 IP 和一次性码填到这里。",
                    style = MaterialTheme.typography.bodySmall)
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(host, { host = it }, label = { Text("桌面 IP（如 192.168.1.5）") }, singleLine = true)
                OutlinedTextField(code, { code = it }, label = { Text("一次性配对码") }, singleLine = true)
                if (msg.isNotEmpty()) Text(msg)
            }
        },
        confirmButton = {
            TextButton(onClick = {
                val s = Settings(ctx).apply { this.host = host.trim() }
                scope.launch {
                    val ok = withContext(Dispatchers.IO) { SyncClient(s).pair(code.trim()) }
                    if (ok) { SyncScheduler.requestPush(ctx); onDismiss() } else msg = "配对失败，请检查 IP 与配对码"
                }
            }) { Text("配对") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}
