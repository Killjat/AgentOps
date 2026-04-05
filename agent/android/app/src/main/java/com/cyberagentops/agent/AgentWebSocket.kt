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

class AgentWebSocket(private val context: Context) {

    private val TAG = "AgentWebSocket"
    private var webSocket: WebSocket? = null
    private var reconnectJob: Job? = null
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private var reconnectDelay = 2000L
    @Volatile private var isConnecting = false

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
        val intent = Intent("com.cyberagent.STATUS_UPDATE").apply {
            putExtra("status", status)
            setPackage(context.packageName)
        }
        context.sendBroadcast(intent)
    }

    fun connect() {
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
                        updateStatus("重连中...")
                        scheduleReconnect()
                    }

                    override fun onClosed(ws: WebSocket, code: Int, reason: String) {
                        updateStatus("已断开")
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
}
