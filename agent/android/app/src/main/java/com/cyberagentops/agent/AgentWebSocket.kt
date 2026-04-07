package com.cyberagentops.agent

import android.content.Context
import android.content.Intent
import android.util.Log
import kotlinx.coroutines.*
import okhttp3.*
import okio.ByteString
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager
import java.security.cert.X509Certificate

class AgentWebSocket(private val context: Context, private val onStatus: (String) -> Unit) {

    private val TAG = "AgentWebSocket"
    private var webSocket: WebSocket? = null
    private var reconnectJob: Job? = null
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private var reconnectDelay = 2000L
    @Volatile private var isConnecting = false
    @Volatile private var isStopped = false

    private val trustAllCerts = arrayOf<TrustManager>(object : X509TrustManager {
        override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {}
        override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {}
        override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
    })

    private val client: OkHttpClient by lazy {
        val sslContext = SSLContext.getInstance("TLS")
        sslContext.init(null, trustAllCerts, java.security.SecureRandom())
        OkHttpClient.Builder()
            .sslSocketFactory(sslContext.socketFactory, trustAllCerts[0] as X509TrustManager)
            .hostnameVerifier { _, _ -> true }
            .pingInterval(60, TimeUnit.SECONDS)
            .connectTimeout(15, TimeUnit.SECONDS)
            .build()
    }

    private fun updateStatus(status: String) {
        onStatus(status)
    }

    fun connect() {
        isStopped = false
        if (isConnecting) return
        reconnectJob?.cancel()
        reconnectJob = scope.launch {
            isConnecting = true
            try {
                val serverUrl = AgentConfig.getServerUrl(context).trim()
                if (serverUrl.isEmpty()) return@launch

                // 规范化 URL：支持 https:// http:// wss:// ws:// 以及裸地址
                // OkHttp WebSocket 支持 http/https scheme，会自动升级
                val finalUrl = when {
                    serverUrl.startsWith("wss://", ignoreCase = true) ->
                        serverUrl.replaceFirst("wss://", "https://", ignoreCase = true)
                    serverUrl.startsWith("ws://", ignoreCase = true) ->
                        serverUrl.replaceFirst("ws://", "http://", ignoreCase = true)
                    serverUrl.startsWith("https://", ignoreCase = true) -> serverUrl
                    serverUrl.startsWith("http://", ignoreCase = true) -> serverUrl
                    else -> "http://$serverUrl"
                }

                val agentId = AgentConfig.getAgentId(context)
                val wsUrl = finalUrl.trimEnd('/') + "/ws/agent/$agentId"

                updateStatus("正在连接...")

                val request = Request.Builder().url(wsUrl).build()
                webSocket = client.newWebSocket(request, object : WebSocketListener() {
                    override fun onOpen(ws: WebSocket, response: Response) {
                        Log.i(TAG, "WS Open, sending register...")
                        updateStatus("已连接")
                        reconnectDelay = 2000L
                        sendRegisterInfo(ws, agentId)
                    }

                    override fun onMessage(ws: WebSocket, text: String) {
                        scope.launch { handleServerMessage(ws, text) }
                    }

                    override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
                        Log.w(TAG, "连接失败: ${t.message}")
                        if (isStopped) return
                        updateStatus("重连中...")
                        scheduleReconnect()
                    }

                    override fun onClosed(ws: WebSocket, code: Int, reason: String) {
                        if (isStopped) return
                        scheduleReconnect()
                    }
                })
            } catch (e: Exception) {
                updateStatus("错误: ${e.message}")
                scheduleReconnect()
            } finally {
                isConnecting = false
            }
        }
    }

    private fun sendRegisterInfo(ws: WebSocket, agentId: String) {
        try {
            val info = DeviceInfo.get(context)
            val msg = JSONObject().apply {
                put("type", "register")
                put("agent_id", agentId)
                put("id", agentId) // 增加冗余 id 字段，提高兼容性
                put("os_info", JSONObject().apply {
                    put("os", "Android")
                    put("os_version", info.osVersion)
                    put("model", info.model)
                    put("brand", info.brand)
                    put("hostname", info.hostname)
                })
                // 将部分信息平铺到外层，有些简单的服务端只会读外层
                put("hostname", info.hostname)
                put("os", "Android")
            }
            val payload = msg.toString()
            val success = ws.send(payload)
            Log.i(TAG, "Register Payload: $payload")
        } catch (e: Exception) {
            Log.e(TAG, "Register Failed", e)
        }
    }

    private fun scheduleReconnect() {
        reconnectJob?.cancel()
        reconnectJob = scope.launch {
            delay(reconnectDelay)
            reconnectDelay = minOf(reconnectDelay * 2, 60_000L)
            connect()
        }
    }

    private suspend fun handleServerMessage(ws: WebSocket, text: String) {
        try {
            val json = JSONObject(text)
            val type = json.optString("type")
            val taskId = json.optString("task_id")

            when (type) {
                "ping" -> ws.send(JSONObject().apply { put("type", "pong") }.toString())

                "exec" -> {
                    val command = json.optString("command")
                    val timeout = json.optInt("timeout", 60)
                    scope.launch(Dispatchers.Default) {
                        val result = CommandExecutor.exec(command, timeout)
                        ws.send(JSONObject().apply {
                            put("type", "result")
                            put("task_id", taskId)
                            put("success", result.success)
                            put("output", result.output)
                            put("error", result.error)
                            put("done", true)
                        }.toString())
                    }
                }

                "discover" -> {
                    scope.launch(Dispatchers.Default) {
                        val tools = discoverTools()
                        ws.send(JSONObject().apply {
                            put("type", "discover_result")
                            put("task_id", taskId)
                            put("data", JSONObject().apply {
                                put("tools", tools)
                                put("agent_id", AgentConfig.getAgentId(context))
                                put("hostname", DeviceInfo.get(context).hostname)
                            })
                            put("done", true)
                        }.toString())
                    }
                }

                "metrics" -> {
                    scope.launch(Dispatchers.Default) {
                        val metrics = DeviceInfo.getMetrics(context)
                        ws.send(JSONObject().apply {
                            put("type", "metrics_result")
                            put("task_id", taskId)
                            put("metrics", metrics)
                            put("done", true)
                        }.toString())
                    }
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Msg Error", e)
        }
    }

    fun disconnect() {
        isConnecting = false
        reconnectJob?.cancel()
        webSocket?.close(1000, "manual")
        webSocket = null
    }

    private fun discoverTools(): org.json.JSONArray {
        val tools = listOf(
            "ping" to "网络连通测试",
            "curl" to "HTTP 请求",
            "wget" to "文件下载",
            "nslookup" to "DNS 查询",
            "traceroute" to "路由追踪",
            "netstat" to "网络连接状态",
            "ss" to "Socket 统计",
            "ip" to "网络接口管理",
            "ifconfig" to "网络接口配置",
            "cat" to "文件查看",
            "grep" to "文本搜索",
            "awk" to "文本处理",
            "sed" to "流编辑器",
            "find" to "文件查找",
            "ps" to "进程查看",
            "top" to "系统监控",
            "df" to "磁盘使用",
            "du" to "目录大小",
            "getprop" to "Android 系统属性",
            "dumpsys" to "Android 系统服务信息",
            "am" to "Android Activity 管理",
            "pm" to "Android 包管理",
            "settings" to "Android 系统设置",
            "logcat" to "Android 日志",
            "python" to "Python 脚本",
            "python3" to "Python3 脚本",
        )
        val result = org.json.JSONArray()
        for ((tool, desc) in tools) {
            val check = CommandExecutor.exec("which $tool 2>/dev/null || command -v $tool 2>/dev/null", 3)
            if (check.success && check.output.isNotBlank()) {
                result.put(JSONObject().apply {
                    put("name", tool)
                    put("description", desc)
                    put("path", check.output.trim())
                })
            }
        }
        return result
    }
}
