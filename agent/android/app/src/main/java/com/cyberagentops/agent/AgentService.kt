package com.cyberagentops.agent

import android.app.*
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.localbroadcastmanager.content.LocalBroadcastManager

class AgentService : Service() {

    private var agentWs: AgentWebSocket? = null
    private val CHANNEL_ID = "agent_channel"
    private val NOTIF_ID = 1

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        val notification = buildNotification("准备连接...")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIF_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(NOTIF_ID, notification)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        Log.i("AgentService", "收到启动指令，尝试连接...")
        if (agentWs == null) {
            agentWs = AgentWebSocket(this) { status ->
                // 更新通知栏
                updateNotification(status)
                // 仅在 APP 内部广播，不会影响其他 APP
                val i = Intent("com.cyberagent.STATUS_UPDATE").putExtra("status", status)
                LocalBroadcastManager.getInstance(this).sendBroadcast(i)
            }
        }
        agentWs?.disconnect()
        agentWs?.connect()
        return START_NOT_STICKY  // 不自动重启，APP 退出就停
    }

    override fun onDestroy() {
        agentWs?.disconnect()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    fun updateNotification(status: String) {
        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(NOTIF_ID, buildNotification(status))
    }

    private fun buildNotification(status: String): Notification {
        val agentId = AgentConfig.getAgentId(this)
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("CyberAgent")
            .setContentText("$agentId · $status")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
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
                    setSound(null, null)  // 禁止通知声音
                    enableVibration(false) // 禁止震动
                }
                manager.createNotificationChannel(channel)
            }
        }
    }
}
