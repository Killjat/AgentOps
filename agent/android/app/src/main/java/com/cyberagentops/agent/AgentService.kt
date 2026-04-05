package com.cyberagentops.agent

import android.app.*
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.net.wifi.WifiManager
import android.util.Log
import androidx.core.app.NotificationCompat

class AgentService : Service() {

    private var agentWs: AgentWebSocket? = null
    private val CHANNEL_ID = "agent_channel"
    private val NOTIF_ID = 1
    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null

    private val statusReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val status = intent.getStringExtra("status") ?: return
            updateNotification(status)
        }
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(statusReceiver, IntentFilter("com.cyberagent.STATUS_UPDATE"), Context.RECEIVER_NOT_EXPORTED)
        } else {
            registerReceiver(statusReceiver, IntentFilter("com.cyberagent.STATUS_UPDATE"))
        }

        val notification = buildNotification("准备连接...")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIF_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(NOTIF_ID, notification)
        }

        // 初始化电源锁，防止 CPU 进入休眠
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "CyberAgent::WakeLock")
        wakeLock?.acquire()

        // 初始化 Wifi 锁，防止网络进入休眠
        val wm = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        wifiLock = wm.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "CyberAgent::WifiLock")
        wifiLock?.acquire()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        Log.i("AgentService", "收到启动指令，尝试连接...")
        if (agentWs == null) {
            agentWs = AgentWebSocket(this)
        }
        agentWs?.disconnect()
        agentWs?.connect()
        
        return START_STICKY
    }

    override fun onDestroy() {
        try {
            unregisterReceiver(statusReceiver)
        } catch (e: Exception) {}
        
        if (wakeLock?.isHeld == true) wakeLock?.release()
        if (wifiLock?.isHeld == true) wifiLock?.release()
        
        agentWs?.disconnect()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun updateNotification(status: String) {
        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(NOTIF_ID, buildNotification(status))
    }

    private fun buildNotification(status: String): Notification {
        val agentId = AgentConfig.getAgentId(this)
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("CyberAgent 状态")
            .setContentText("$agentId · $status")
            .setSmallIcon(R.drawable.ic_launcher)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val manager = getSystemService(NotificationManager::class.java)
            if (manager.getNotificationChannel(CHANNEL_ID) == null) {
                val channel = NotificationChannel(
                    CHANNEL_ID, "Agent 服务",
                    NotificationManager.IMPORTANCE_LOW
                ).apply { 
                    description = "CyberAgentOps 后台服务"
                    setShowBadge(false)
                }
                manager.createNotificationChannel(channel)
            }
        }
    }
}
