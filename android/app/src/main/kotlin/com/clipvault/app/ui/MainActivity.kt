package com.clipvault.app.ui

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.clipvault.app.ClipVaultApp
import com.clipvault.app.data.ClipEntity
import com.clipvault.app.sync.Settings
import com.clipvault.app.sync.SyncClient
import com.clipvault.app.sync.SyncScheduler
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** History, search and pairing. Capture happens via Share Target / QS Tile /
 * IME — Android forbids background clipboard reads, so there is no auto-watch. */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        SyncScheduler.schedulePeriodic(this)
        setContent { MaterialTheme { Home() } }
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
            OutlinedTextField(query, { query = it }, Modifier.fillMaxWidth(),
                label = { Text("搜索") }, singleLine = true)
            Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                Switch(showSecret, { showSecret = it })
                Text("显示隔离区")
            }
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(clips) { c -> ClipCard(c) }
            }
        }
    }

    if (pairing) PairDialog(onDismiss = { pairing = false })
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
                OutlinedTextField(host, { host = it }, label = { Text("桌面 IP") }, singleLine = true)
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
